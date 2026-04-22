import logging
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
import config

logger = logging.getLogger(__name__)


class LLMLogger(BaseCallbackHandler):
    def __init__(self) -> None:
        super().__init__()
        self._start_time: float | None = None

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], **kwargs: Any
    ) -> None:
        self._start_time = time.monotonic()
        for batch in messages:
            for msg in batch:
                logger.debug("[LLM →] %s: %s", msg.type, msg.content[:500] if msg.content else "")

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        elapsed = (
            round(time.monotonic() - self._start_time, 3) if self._start_time is not None else None
        )
        self._start_time = None

        usage = (response.llm_output or {}).get("token_usage", {})
        prompt_tokens = usage.get("prompt_tokens", "?")
        completion_tokens = usage.get("completion_tokens", "?")
        total_tokens = usage.get("total_tokens", "?")
        model = (response.llm_output or {}).get("model_name", config.LLAMA_CPP_MODEL)

        if elapsed and isinstance(completion_tokens, int) and elapsed > 0:
            tokens_per_sec = f"{completion_tokens / elapsed:.1f}"
        else:
            tokens_per_sec = "?"

        logger.info(
            "[LLM stats] model=%s latency=%.3fs tokens/s=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            model,
            elapsed if elapsed is not None else -1,
            tokens_per_sec,
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )

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
