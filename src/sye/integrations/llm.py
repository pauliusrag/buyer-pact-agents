"""LLM provider abstraction.

Business logic never imports a vendor SDK. It asks a provider for a *typed*
object: ``await llm.structured(schema=UserIntentExtraction, system=..., user=...)``.

Three implementations:

* ``LangChainProvider``  — Anthropic (default) or OpenAI through LangChain's
  ``with_structured_output``; structured output is mandatory for every step that
  creates business data.
* ``NullProvider``       — no key configured / offline: raises ``LLMUnavailable``
  so the caller falls back to its deterministic heuristic and records that fact
  in the audit trail.
* ``ScriptedProvider``   — test double returning pre-baked objects.

Every provider counts its calls so the run metrics are honest.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from sye.config import Settings
from sye.observability.logging import get_logger

logger = get_logger("sye.llm")

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-4.1-mini",
}


class LLMUnavailable(RuntimeError):
    """Raised when no usable LLM is configured, or a call definitively failed."""


class LLMProvider(Protocol):
    name: str
    model: str
    call_count: int

    async def structured(
        self, *, schema: type[T], system: str, user: str, task: str = "generic"
    ) -> T: ...


class BaseProvider:
    name: str = "base"
    model: str = ""

    def __init__(self) -> None:
        self.call_count = 0
        self.failure_count = 0
        self.tasks: list[str] = []


class NullProvider(BaseProvider):
    """Used offline or without an API key. Callers must handle ``LLMUnavailable``."""

    name = "none"

    def __init__(self, reason: str = "no LLM configured") -> None:
        super().__init__()
        self.reason = reason

    async def structured(
        self, *, schema: type[T], system: str, user: str, task: str = "generic"
    ) -> T:
        raise LLMUnavailable(self.reason)


class ScriptedProvider(BaseProvider):
    """Deterministic test double.

    ``responses`` maps a task name to either a single object or a queue of objects.
    """

    name = "scripted"

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.responses = responses or {}
        self.model = "scripted"

    async def structured(
        self, *, schema: type[T], system: str, user: str, task: str = "generic"
    ) -> T:
        self.call_count += 1
        self.tasks.append(task)
        value = self.responses.get(task)
        if value is None:
            raise LLMUnavailable(f"no scripted response for task {task!r}")
        if isinstance(value, list):
            if not value:
                raise LLMUnavailable(f"scripted responses for {task!r} exhausted")
            value = value.pop(0)
        if callable(value):
            value = value(user)
        if isinstance(value, schema):
            return value
        return schema.model_validate(value)


class LangChainProvider(BaseProvider):
    """Anthropic (default) or OpenAI via LangChain's structured-output binding."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        super().__init__()
        self.name = provider
        self.model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._chat = self._build_chat(provider, model, api_key, timeout, max_retries)
        self._structured_cache: dict[type[BaseModel], Any] = {}

    @staticmethod
    def _build_chat(
        provider: str, model: str, api_key: str, timeout: float, max_retries: int
    ) -> Any:
        if provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=model,
                api_key=api_key,
                timeout=timeout,
                max_retries=max_retries,
                max_tokens=4096,
            )
        if provider == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=model, api_key=api_key, timeout=timeout, max_retries=max_retries
            )
        raise LLMUnavailable(f"unsupported LLM provider {provider!r}")

    def _runnable(self, schema: type[BaseModel]) -> Any:
        if schema not in self._structured_cache:
            self._structured_cache[schema] = self._chat.with_structured_output(schema)
        return self._structured_cache[schema]

    async def structured(
        self, *, schema: type[T], system: str, user: str, task: str = "generic"
    ) -> T:
        self.call_count += 1
        self.tasks.append(task)
        messages: Sequence[tuple[str, str]] = [("system", system), ("human", user)]
        try:
            result = await asyncio.wait_for(
                self._runnable(schema).ainvoke(messages), timeout=self._timeout + 5
            )
        except Exception as exc:  # network, validation, rate limit...
            self.failure_count += 1
            logger.warning("LLM task %s failed: %s", task, exc)
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if isinstance(result, schema):
            return result
        return schema.model_validate(result)


def build_llm_provider(settings: Settings, *, offline: bool = False) -> LLMProvider:
    """Pick a provider from settings. Never raises — offline falls back to Null."""
    if offline:
        return NullProvider("offline mode: deterministic heuristics only")
    if settings.llm_provider == "none" or not settings.has_llm_key:
        return NullProvider(f"no API key for provider {settings.llm_provider!r}")
    model = settings.llm_model or DEFAULT_MODELS.get(settings.llm_provider, "")
    key = (
        settings.anthropic_api_key
        if settings.llm_provider == "anthropic"
        else settings.openai_api_key
    )
    try:
        return LangChainProvider(
            provider=settings.llm_provider,
            model=model,
            api_key=key or "",
            timeout=settings.llm_timeout_seconds,
        )
    except Exception as exc:  # missing extra, bad config...
        logger.warning("falling back to deterministic heuristics: %s", exc)
        return NullProvider(f"LLM init failed: {exc}")
