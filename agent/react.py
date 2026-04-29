"""
Minimal ReAct loop for Gemma 4 via llama.cpp.
Gemma 4's template (--chat-template gemma4 with --jinja) supports system role
but not native tool calling, so we embed tool descriptions in the system prompt
and parse JSON tool calls from the model's output.
"""
import asyncio
import json
import logging
import re
import threading
from typing import Any, Callable

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

MAX_STEPS = 10

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

MAX_REDIRECTS = 3  # max consecutive steps with no tool call before aborting

_SYSTEM_PREFIX = """\
{system_prompt}

You have access to the following tools. To call a tool, output EXACTLY this format and nothing else on that turn:

<tool_call>{{"name": "TOOL_NAME", "arguments": {{...}}}}</tool_call>

You may output multiple tool calls in a single turn to fetch data in parallel — put each on its own line.
You will then receive the results and can call more tools or write your final answer.
Do NOT write any analysis or numbers before calling tools — you have no real data yet.
Do NOT introduce yourself or greet. Go straight to calling tools.

Available tools:
{tool_descriptions}

--- EXAMPLES ---

Example 1 — calling a tool with arguments:
User: How much did I spend on dining last month?
Assistant: <tool_call>{{"name": "get_spending_by_category", "arguments": {{"days": 30}}}}</tool_call>
Tool result: {{"dining": 340.50, "groceries": 210.00}}
Assistant: You spent $340.50 on dining last month.

Example 2 — calling multiple tools in parallel (preferred when data is independent):
User: Give me a full spending summary with savings rate.
Assistant: <tool_call>{{"name": "get_spending_by_category", "arguments": {{"days": 30}}}}</tool_call>
<tool_call>{{"name": "get_savings_rate", "arguments": {{}}}}</tool_call>
<tool_call>{{"name": "get_subscription_list", "arguments": {{}}}}</tool_call>
Tool results: ...
Assistant: [write full answer using all three results]

Example 3 — sequential calls when later calls depend on earlier results:
User: Check for anomalies and cross-verify the biggest one.
Assistant: <tool_call>{{"name": "get_anomalies", "arguments": {{"days": 7}}}}</tool_call>
Tool result: ⚠️ Amazon — $340 on 2026-04-21 (5x typical)
Assistant: <tool_call>{{"name": "compare_to_baseline", "arguments": {{"category": "Groceries", "current_amount": 340.0}}}}</tool_call>
Tool result: $340 is 2.1x the $162/month baseline.
Assistant: Amazon $340 on Apr 21 is worth flagging — 2.1x your typical grocery month.

--- END EXAMPLES ---"""


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


async def run_react_stream(
    llm: Any,
    tools: list[BaseTool],
    system_prompt: str,
    user_message: str,
    history: list[dict] | None = None,
    max_steps: int = MAX_STEPS,
    on_tool_call: "Callable[[], None] | None" = None,
    cancel_event: "threading.Event | None" = None,
):
    """
    Async generator yielding dicts:
      {"type": "tool",  "name": "..."}          – tool being called
      {"type": "token", "text": "..."}          – streaming final-answer token
      {"type": "reset"}                         – discard already-streamed tokens (rare)
      {"type": "done",  "reply": "full reply"}  – stream complete
    """
    tool_map = {t.name: t for t in tools}
    tool_descriptions = _build_tool_descriptions(tools)

    system = _SYSTEM_PREFIX.format(
        system_prompt=system_prompt,
        tool_descriptions=tool_descriptions,
    )

    prior: list = []
    for msg in (history or []):
        if msg["role"] == "user":
            prior.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            prior.append(AIMessage(content=msg["content"]))

    messages: list = [SystemMessage(content=system), *prior, HumanMessage(content=user_message)]

    tools_called = False
    last_tool_calls: list[str] = []
    consecutive_redirects = 0

    for step in range(max_steps):
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError("Job cancelled")

        full_text = ""
        tool_call_seen = False
        streamed_any = False
        in_think = False

        async for chunk in llm.astream(messages):
            if cancel_event and cancel_event.is_set():
                break
            token = chunk.content
            if not token:
                continue
            full_text += token
            if not tool_call_seen:
                if "<tool_call>" in full_text:
                    tool_call_seen = True  # stop streaming from here
                else:
                    # Suppress <think>...</think> blocks from streaming to client
                    if "<think>" in full_text and "</think>" not in full_text:
                        in_think = True
                    elif "</think>" in full_text:
                        in_think = False
                    if not in_think:
                        yield {"type": "token", "text": token}
                        streamed_any = True

        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError("Job cancelled")

        reply = THINK_RE.sub("", full_text).strip()
        logger.info("[ReAct stream step %d] output: %s", step + 1, reply[:300])

        tool_calls = TOOL_CALL_RE.findall(reply)

        if not tool_calls:
            if not tools_called:
                consecutive_redirects += 1
                if consecutive_redirects >= MAX_REDIRECTS:
                    logger.warning(
                        "[ReAct stream step %d] Model failed to call any tool after %d redirects — aborting.",
                        step + 1, MAX_REDIRECTS,
                    )
                    if streamed_any:
                        yield {"type": "reset"}
                    yield {"type": "done", "reply": ""}
                    return
                # Model skipped tools — reset client and force a tool call
                if streamed_any:
                    yield {"type": "reset"}
                first_tool = tools[0] if tools else None
                example = (
                    f'<tool_call>{{"name": "{first_tool.name}", "arguments": {{}}}}</tool_call>'
                    if first_tool else ""
                )
                messages.append(AIMessage(content=reply))
                messages.append(HumanMessage(content=(
                    "STOP. You must call a tool before writing anything. "
                    "Do NOT write any analysis or numbers yet — you have no real data. "
                    f"Output a tool call RIGHT NOW using this exact format:\n{example}"
                )))
                continue
            yield {"type": "done", "reply": reply}
            return

        tools_called = True

        if tool_calls == last_tool_calls:
            logger.warning("[ReAct stream step %d] repeated tool call, forcing final answer.", step + 1)
            messages.append(HumanMessage(content=(
                "You already called that tool and received the result. "
                "Do NOT call it again. Write your final answer now based on what you have."
            )))
            forced_reply = ""
            async for chunk in llm.astream(messages):
                token = chunk.content
                if token:
                    forced_reply += token
                    yield {"type": "token", "text": token}
            yield {"type": "done", "reply": THINK_RE.sub("", forced_reply).strip()}
            return

        last_tool_calls = tool_calls
        messages.append(AIMessage(content=reply))

        results = []
        for raw in tool_calls:
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError("Job cancelled")
            try:
                call = json.loads(raw)
                name = call["name"]
                args = call.get("arguments", {})
                yield {"type": "tool", "name": name}
                if on_tool_call:
                    on_tool_call()
                tool = tool_map.get(name)
                if tool is None:
                    results.append(f"Tool '{name}' not found.")
                    continue
                logger.debug("[ReAct stream] calling %s(%s)", name, args)
                result = await tool.ainvoke(args)
                results.append(f"Tool '{name}' result:\n{result}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                results.append(f"Tool call failed: {e}")

        tool_results = "\n\n".join(results)
        messages.append(HumanMessage(
            content=f"Tool results:\n\n{tool_results}\n\nContinue. If you have enough data to answer, write your final answer now instead of calling more tools."
        ))

    logger.warning("ReAct stream loop reached max steps (%d)", max_steps)
    if cancel_event and cancel_event.is_set():
        raise asyncio.CancelledError("Job cancelled")
    messages.append(HumanMessage(content=(
        "You have reached the maximum number of tool calls. "
        "Based on the tool results above, write your final answer now. "
        "Do NOT call any more tools."
    )))
    final_reply = ""
    async for chunk in llm.astream(messages):
        if cancel_event and cancel_event.is_set():
            break
        token = chunk.content
        if token:
            final_reply += token
            yield {"type": "token", "text": token}
    yield {"type": "done", "reply": THINK_RE.sub("", final_reply).strip()}


async def run_react(
    llm: Any,
    tools: list[BaseTool],
    system_prompt: str,
    user_message: str,
    history: list[dict] | None = None,
    max_steps: int = MAX_STEPS,
    on_tool_call: Callable[[], None] | None = None,
    cancel_event: "threading.Event | None" = None,
) -> str:
    tool_map = {t.name: t for t in tools}
    tool_descriptions = _build_tool_descriptions(tools)

    system = _SYSTEM_PREFIX.format(
        system_prompt=system_prompt,
        tool_descriptions=tool_descriptions,
    )

    prior = []
    for msg in (history or []):
        if msg["role"] == "user":
            prior.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            prior.append(AIMessage(content=msg["content"]))

    messages: list = [
        SystemMessage(content=system),
        *prior,
        HumanMessage(content=user_message),
    ]

    tools_called = False
    last_tool_calls: list[str] = []
    consecutive_redirects = 0

    for step in range(max_steps):
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError("Job cancelled")
        try:
            response = await llm.ainvoke(messages)
        except Exception as e:
            if any(kw in str(e).lower() for kw in ["400", "context", "token", "exceed"]):
                logger.error(
                    "[ReAct step %d] Context overflow: %d messages in history. Error: %s",
                    step + 1, len(messages), e,
                )
            raise
        reply = THINK_RE.sub("", response.content).strip()
        logger.info("[ReAct step %d] model output: %s", step + 1, reply[:300])

        tool_calls = TOOL_CALL_RE.findall(reply)
        if not tool_calls:
            if not tools_called:
                consecutive_redirects += 1
                if consecutive_redirects >= MAX_REDIRECTS:
                    logger.warning(
                        "[ReAct step %d] Model failed to call any tool after %d redirects — aborting.",
                        step + 1, MAX_REDIRECTS,
                    )
                    return ""
                # Model skipped tools and hallucinated data — force it to call tools
                logger.warning("[ReAct step %d] No tool calls detected and no tools have been called yet. Redirecting.", step + 1)
                first_tool = tools[0] if tools else None
                example = (
                    f'<tool_call>{{"name": "{first_tool.name}", "arguments": {{}}}}</tool_call>'
                    if first_tool else ""
                )
                messages.append(AIMessage(content=reply))
                messages.append(HumanMessage(
                    content=(
                        "STOP. You must call a tool before writing anything. "
                        "Do NOT write any analysis or numbers yet — you have no real data. "
                        f"Output a tool call RIGHT NOW using this exact format:\n{example}"
                    )
                ))
                continue
            return reply

        tools_called = True

        # Detect if model is repeating the same tool call — break the loop
        if tool_calls == last_tool_calls:
            logger.warning("[ReAct step %d] Model repeated the same tool call(s). Forcing final answer.", step + 1)
            messages.append(HumanMessage(
                content=(
                    "You already called that tool and received the result. "
                    "Do NOT call it again. Write your final answer now based on what you have."
                )
            ))
            response = await llm.ainvoke(messages)
            return response.content.strip()
        last_tool_calls = tool_calls

        messages.append(AIMessage(content=reply))

        results = []
        for raw in tool_calls:
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError("Job cancelled")
            try:
                call = json.loads(raw)
                name = call["name"]
                args = call.get("arguments", {})
                if on_tool_call:
                    on_tool_call()
                tool = tool_map.get(name)
                if tool is None:
                    results.append(f"Tool '{name}' not found.")
                    continue
                logger.debug("[ReAct] calling %s(%s)", name, args)
                result = await tool.ainvoke(args)
                results.append(f"Tool '{name}' result:\n{result}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                results.append(f"Tool call failed: {e}")

        tool_results = "\n\n".join(results)
        messages.append(HumanMessage(content=f"Tool results:\n\n{tool_results}\n\nContinue. If you have enough data to answer, write your final answer now instead of calling more tools."))

    logger.warning("ReAct loop reached max steps (%d)", max_steps)
    if cancel_event and cancel_event.is_set():
        raise asyncio.CancelledError("Job cancelled")
    # Ask the model to produce a final answer from what it has gathered so far
    messages.append(HumanMessage(
        content=(
            "You have reached the maximum number of tool calls. "
            "Based on the tool results above, write your final answer now. "
            "Do NOT call any more tools."
        )
    ))
    response = await llm.ainvoke(messages)
    return response.content.strip()
