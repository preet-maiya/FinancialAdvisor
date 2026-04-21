"""
Minimal ReAct loop for Gemma 4 via llama.cpp.
Gemma 4's template (--chat-template gemma4 with --jinja) supports system role
but not native tool calling, so we embed tool descriptions in the system prompt
and parse JSON tool calls from the model's output.
"""
import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

MAX_STEPS = 10

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

_SYSTEM_PREFIX = """\
{system_prompt}

You have access to the following tools. To call a tool, output EXACTLY this format:

<tool_call>{{"name": "TOOL_NAME", "arguments": {{...}}}}</tool_call>

After receiving the tool result, continue reasoning and call more tools as needed.
When you have all the data you need, write your final answer without any <tool_call> tags.
Do NOT introduce yourself or greet. Go straight to calling tools.

Available tools:
{tool_descriptions}"""


def _build_tool_descriptions(tools: list[BaseTool]) -> str:
    lines = []
    for t in tools:
        schema = t.args_schema.model_json_schema() if t.args_schema else {}
        props = schema.get("properties", {})
        args = ", ".join(
            f"{k}: {v.get('type', 'any')} = {json.dumps(v['default'])}"
            if "default" in v else f"{k}: {v.get('type', 'any')}"
            for k, v in props.items()
        )
        lines.append(f"- {t.name}({args}): {t.description}")
    return "\n".join(lines)


async def run_react(
    llm: Any,
    tools: list[BaseTool],
    system_prompt: str,
    user_message: str,
) -> str:
    tool_map = {t.name: t for t in tools}
    tool_descriptions = _build_tool_descriptions(tools)

    system = _SYSTEM_PREFIX.format(
        system_prompt=system_prompt,
        tool_descriptions=tool_descriptions,
    )
    messages: list = [
        SystemMessage(content=system),
        HumanMessage(content=user_message),
    ]

    tools_called = False

    for step in range(MAX_STEPS):
        response = await llm.ainvoke(messages)
        reply = response.content
        logger.debug("[ReAct step %d] model output: %s", step + 1, reply[:300])

        tool_calls = TOOL_CALL_RE.findall(reply)
        if not tool_calls:
            if not tools_called:
                # Model skipped tools and hallucinated data — force it to call tools
                logger.warning("[ReAct step %d] No tool calls detected and no tools have been called yet. Redirecting.", step + 1)
                messages.append(AIMessage(content=reply))
                messages.append(HumanMessage(
                    content=(
                        "STOP. You generated fake data instead of calling tools. "
                        "Do NOT make up numbers. You MUST call tools to fetch real data first. "
                        "Call get_spending_by_category now:\n"
                        '<tool_call>{"name": "get_spending_by_category", "arguments": {"days": 30}}</tool_call>'
                    )
                ))
                continue
            return reply

        tools_called = True

        messages.append(AIMessage(content=reply))

        results = []
        for raw in tool_calls:
            try:
                call = json.loads(raw)
                name = call["name"]
                args = call.get("arguments", {})
                tool = tool_map.get(name)
                if tool is None:
                    results.append(f"Tool '{name}' not found.")
                    continue
                logger.debug("[ReAct] calling %s(%s)", name, args)
                result = await tool.ainvoke(args)
                results.append(f"Tool '{name}' result:\n{result}")
            except Exception as e:
                results.append(f"Tool call failed: {e}")

        tool_results = "\n\n".join(results)
        messages.append(HumanMessage(content=f"Tool results:\n\n{tool_results}\n\nContinue."))

    logger.warning("ReAct loop reached max steps (%d)", MAX_STEPS)
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg.content
    return ""
