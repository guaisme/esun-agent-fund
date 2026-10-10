from __future__ import annotations
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import logging
import os
import random
import time
import httpx
from mcp.shared.exceptions import MCPError
from typing import Any, AsyncGenerator, Awaitable, Callable, TypeVar
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini
from google.adk.tools.base_tool import BaseTool
from google.adk.events import Event
from google.genai import types

LOG = logging.getLogger(__name__)
DEADLINE: ContextVar[float | None] = ContextVar("research_deadline", default=None)
T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    initial_backoff: float = 2
    max_backoff: float = 15
    request_timeout: float = 60
    tool_timeout: float = 30
    task_deadline: float = 240

    def __post_init__(self) -> None:
        if self.max_retries < 0 or any(
            v <= 0
            for v in [
                self.initial_backoff,
                self.max_backoff,
                self.request_timeout,
                self.tool_timeout,
                self.task_deadline,
            ]
        ):
            raise ValueError("Invalid retry policy")

    @classmethod
    def from_env(cls) -> RetryPolicy:
        return cls(
            max_retries=int(os.getenv("AGENT_MAX_RETRIES", "3")),
            initial_backoff=float(os.getenv("AGENT_INITIAL_BACKOFF", "2")),
            max_backoff=float(os.getenv("AGENT_MAX_BACKOFF", "15")),
            request_timeout=float(os.getenv("GEMINI_REQUEST_TIMEOUT", "60")),
            tool_timeout=float(os.getenv("MCP_TOOL_TIMEOUT", "30")),
            task_deadline=float(os.getenv("AGENT_TASK_DEADLINE", "240")),
        )


def remaining(timeout: float) -> float:
    deadline = DEADLINE.get()
    seconds = min(timeout, deadline - time.monotonic()) if deadline else timeout
    if seconds <= 0:
        raise TimeoutError("Overall research deadline exceeded")
    return seconds


def retryable(error: Exception) -> bool:
    code = getattr(error, "code", getattr(getattr(error, "response", None), "status_code", None))
    return (
        code in {429, 500, 502, 503, 504}
        or isinstance(
            error, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.NetworkError)
        )
        or (isinstance(error, MCPError) and "timed out" in str(error).lower())
        or "timeout" in type(error).__name__.lower()
    )


async def pause(policy: RetryPolicy, attempt: int, kind: str, error: Exception) -> None:
    cap = min(policy.max_backoff, policy.initial_backoff * 2**attempt)
    delay = random.uniform(cap / 2, cap)
    LOG.warning(
        "retry kind=%s type=%s code=%s next_attempt=%s delay=%.2f",
        kind,
        type(error).__name__,
        getattr(error, "code", None),
        attempt + 2,
        delay,
    )
    if DEADLINE.get() and delay >= remaining(policy.task_deadline):
        raise TimeoutError("Research deadline before retry")
    await asyncio.sleep(delay)


async def bounded_call(
    operation: Callable[[], Awaitable[T]], policy: RetryPolicy, timeout: float, kind: str
) -> T:
    for attempt in range(policy.max_retries + 1):
        try:
            async with asyncio.timeout(remaining(timeout)):
                return await operation()
        except Exception as error:
            if not retryable(error) or attempt == policy.max_retries:
                raise
            await pause(policy, attempt, kind, error)
    raise AssertionError("Unreachable")


class ReliableGemini(Gemini):
    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        policy = RetryPolicy.from_env()
        # The SDK must make only one transport attempt. This layer owns retries.
        llm_request.config.http_options = types.HttpOptions(
            timeout=int(policy.request_timeout * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        for attempt in range(policy.max_retries + 1):
            emitted = False
            try:
                async with asyncio.timeout(remaining(policy.request_timeout)):
                    async for response in super().generate_content_async(llm_request, stream):
                        emitted = True
                        yield response
                return
            except Exception as error:
                # Never replay a request after yielding a function call or partial answer.
                if emitted or not retryable(error) or attempt == policy.max_retries:
                    raise
                await pause(policy, attempt, "Gemini", error)


class ReliableTool(BaseTool):
    def __init__(self, tool: BaseTool) -> None:
        super().__init__(
            name=tool.name, description=tool.description, is_long_running=tool.is_long_running
        )
        self.delegate = tool

    def _get_declaration(self):
        return self.delegate._get_declaration()

    async def run_async(self, *, args: dict, tool_context: Any) -> Any:
        p = RetryPolicy.from_env()
        return await bounded_call(
            lambda: self.delegate.run_async(args=args, tool_context=tool_context),
            p,
            p.tool_timeout,
            "MCP:" + self.name,
        )


class ReliableResearchAgent(LlmAgent):
    @property
    def canonical_model(self) -> BaseLlm:
        if isinstance(self.model, str):
            resolved = self._resolved_model
            if resolved is None or resolved[0] != self.model:
                self._resolved_model = (self.model, ReliableGemini(model=self.model))
            return self._resolved_model[1]
        return super().canonical_model

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        policy = RetryPolicy.from_env()
        token = DEADLINE.set(time.monotonic() + policy.task_deadline)
        try:
            async with asyncio.timeout(policy.task_deadline):
                async for event in super()._run_async_impl(ctx):
                    yield event
        except TimeoutError:
            LOG.error("research_failed type=OverallDeadline seconds=%s", policy.task_deadline)
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text="研究已達整體時間上限；本輪未完成。已取得的工具資料不代表完整研究結果，請稍後重試或縮小問題範圍。"
                        )
                    ],
                ),
            )
        finally:
            DEADLINE.reset(token)
