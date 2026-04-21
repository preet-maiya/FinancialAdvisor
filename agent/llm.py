import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
import config

logger = logging.getLogger(__name__)


class LLMLogger(BaseCallbackHandler):
    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], **kwargs: Any
    ) -> None:
        for batch in messages:
            for msg in batch:
                logger.debug("[LLM →] %s: %s", msg.type, msg.content[:500] if msg.content else "")

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        for gen_list in response.generations:
            for gen in gen_list:
                text = getattr(gen, "text", "") or ""
                logger.debug("[LLM ←] %s", text[:500])


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=config.LLAMA_CPP_BASE_URL,
        api_key="not-needed",
        model=config.LLAMA_CPP_MODEL,
        max_tokens=config.LLAMA_CPP_MAX_TOKENS,
        temperature=config.LLAMA_CPP_TEMPERATURE,
        callbacks=[LLMLogger()],
    )
