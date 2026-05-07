"""
LLM service — 多供应商统一客户端 (LiteLLM 中转 / 直连)。

完整实现 src/llm/client.py 全部功能：
  - DeploymentMode A/B 客户端自动切换
  - 敏感信息脱敏（VIN / 手机号 / 车牌号）
  - chat_completion（阻塞/流式聚合 + tool calling）
  - chat_completion_stream（真流式 SSE delta 输出）
  - get_embeddings（向量嵌入）
  - parse_tool_calls
  - 全链路 chain_log 调用链日志
"""

from __future__ import annotations

import json
import logging
import os
import time
from types import SimpleNamespace
from typing import Any, AsyncIterator, List, Literal

from anthropic import AsyncAnthropic, NotFoundError
from openai import AsyncOpenAI

from common.chain_log import chain_debug, chain_info
from common.redaction import redact_sensitive_info, sensitive_redactor
from config import settings, DeploymentMode

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────
# Client singleton
# ────────────────────────────────────────────────────────────────────

_client: AsyncOpenAI | AsyncAnthropic | None = None
_client_provider: Literal["openai", "anthropic"] | None = None


def _detect_provider() -> Literal["openai", "anthropic"]:
    """Choose provider for current deployment mode."""
    if settings.DEPLOYMENT_MODE == DeploymentMode.SCENARIO_A:
        return "openai"
    # 场景 B 按 key 自动选择客户端：优先 Anthropic，其次 OpenAI
    if settings.ANTHROPIC_API_KEY:
        return "anthropic"
    return "openai"


def _resolve_openai_model(model: str) -> str:
    """Translate gateway virtual model aliases for OpenAI direct mode."""
    if model == "router-model":
        return os.environ.get("OPENAI_ROUTER_MODEL", "gpt-4o-mini")
    if model == "agent-model":
        return os.environ.get("OPENAI_AGENT_MODEL", "gpt-4o")
    if model == "synthesizer-model":
        return os.environ.get("OPENAI_SYNTHESIZER_MODEL", "gpt-4o")
    if model == "embedding-model":
        return os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    return model


def _resolve_anthropic_model(model: str) -> str:
    """Translate gateway virtual model aliases for Anthropic direct mode."""
    if model == "router-model":
        return os.environ.get("ANTHROPIC_ROUTER_MODEL", "claude-haiku-4-5-20251001")
    if model == "agent-model":
        return os.environ.get("ANTHROPIC_AGENT_MODEL", "claude-sonnet-4-6")
    if model == "synthesizer-model":
        return os.environ.get("ANTHROPIC_SYNTHESIZER_MODEL", "claude-sonnet-4-6")
    return model


def _is_gateway_alias(model: str) -> bool:
    return model in {"agent-model", "router-model", "synthesizer-model"}


async def _pick_available_anthropic_model(
    client: AsyncAnthropic,
    current_model: str,
) -> str | None:
    """Pick a usable model from account-available Anthropic models."""
    try:
        model_list = await client.models.list(limit=100)
    except Exception:
        return None

    available = [getattr(m, "id", "") for m in (getattr(model_list, "data", None) or []) if getattr(m, "id", "")]
    if not available:
        return None
    if current_model in available:
        return current_model

    preferred = [
        os.environ.get("ANTHROPIC_AGENT_MODEL", ""),
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-3-7-sonnet-latest",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
        "claude-3-haiku-20240307",
    ]
    for candidate in preferred:
        if candidate and candidate in available:
            return candidate

    # Last resort: any Claude model visible to this key
    for mid in available:
        if mid.startswith("claude"):
            return mid
    return None


def _normalize_messages_for_anthropic(messages: list[dict]) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert OpenAI-style messages into Anthropic messages payload."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "")
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        if role in {"user", "assistant"}:
            out.append({"role": role, "content": content})
    system_prompt = "\n\n".join(system_parts).strip() if system_parts else None
    return system_prompt, out


def _normalize_tools_for_anthropic(tools: list[dict] | None) -> list[dict[str, Any]] | None:
    """Convert OpenAI tool schema to Anthropic tool schema."""
    if not tools:
        return None
    out: list[dict[str, Any]] = []
    for t in tools:
        fn = t.get("function") if isinstance(t, dict) else None
        if not isinstance(fn, dict):
            continue
        out.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return out or None


def _anthropic_msg_to_openai_like(message: Any) -> Any:
    """Convert Anthropic response message to OpenAI-like shape used by callers."""
    content_parts: list[str] = []
    tool_calls: list[Any] = []
    for block in getattr(message, "content", []) or []:
        btype = getattr(block, "type", "")
        if btype == "text":
            content_parts.append(getattr(block, "text", ""))
        elif btype == "tool_use":
            tool_calls.append(
                SimpleNamespace(
                    id=getattr(block, "id", ""),
                    function=SimpleNamespace(
                        name=getattr(block, "name", ""),
                        arguments=json.dumps(getattr(block, "input", {}) or {}, ensure_ascii=False),
                    ),
                )
            )
    return SimpleNamespace(
        content=("".join(content_parts) or None),
        tool_calls=(tool_calls or None),
    )


def _resolve_llm_route() -> dict[str, Any]:
    """Build redacted route info for diagnostics."""
    mode = settings.DEPLOYMENT_MODE.value
    provider = _detect_provider()
    if settings.DEPLOYMENT_MODE == DeploymentMode.SCENARIO_A:
        base_url = settings.LLM_BASE_URL
        route = "gateway"
        key_source = "LITELLM_API_KEY"
    elif provider == "anthropic":
        base_url = settings.ANTHROPIC_API_BASE
        route = "direct_anthropic"
        key_source = "ANTHROPIC_API_KEY"
    else:
        base_url = settings.LLM_BASE_URL
        route = "direct_openai_custom_base" if settings.LLM_BASE_URL else "direct_openai_default_base"
        key_source = "OPENAI_API_KEY"
    env_file = getattr(settings, "model_config", {}).get("env_file", ".env")
    default_chat_model = (
        _resolve_anthropic_model("agent-model")
        if provider == "anthropic"
        else _resolve_openai_model("agent-model")
    )
    default_embedding_model = (
        "N/A(anthropic_no_embeddings)"
        if provider == "anthropic"
        else _resolve_openai_model("embedding-model")
    )
    return {
        "deployment_mode": mode,
        "llm_provider": provider,
        "llm_route": route,
        "llm_base_url": base_url or "SDK_DEFAULT",
        "llm_api_key_set": bool(settings.LLM_API_KEY),
        "llm_key_source": key_source,
        "llm_default_chat_model": default_chat_model,
        "llm_default_embedding_model": default_embedding_model,
        "cwd": os.getcwd(),
        "env_file": env_file,
    }


def get_client() -> AsyncOpenAI | AsyncAnthropic:
    """根据 DeploymentMode 懒加载 AsyncOpenAI 客户端。"""
    global _client, _client_provider
    provider = _detect_provider()
    if _client is None or _client_provider != provider:
        route_info = _resolve_llm_route()
        chain_debug(
            logger,
            step="llm.client",
            event="INIT",
            **route_info,
        )
        if settings.DEPLOYMENT_MODE == DeploymentMode.SCENARIO_A:
            # 场景 A: 统一使用 OpenAI 协议访问 LiteLLM 网关
            _client = AsyncOpenAI(
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
            )
            _client_provider = "openai"
        elif provider == "anthropic":
            # 场景 B + Anthropic Key：使用 Anthropic 原生协议客户端
            _client = AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY,
                base_url=settings.ANTHROPIC_API_BASE,
            )
            _client_provider = "anthropic"
        else:
            # 场景 B + OpenAI Key：直连 OpenAI（或 OPENAI_API_BASE 中转）
            kwargs: dict[str, Any] = {"api_key": settings.OPENAI_API_KEY}
            if settings.LLM_BASE_URL:
                kwargs["base_url"] = settings.LLM_BASE_URL
            _client = AsyncOpenAI(**kwargs)
            _client_provider = "openai"
    return _client


def log_llm_route_on_startup() -> None:
    """Emit one startup log for effective LLM route decision."""
    chain_info(
        logger,
        step="llm.client",
        event="BOOT_ROUTE",
        **_resolve_llm_route(),
    )


# ────────────────────────────────────────────────────────────────────
# chat_completion (阻塞 + 流式聚合)
# ────────────────────────────────────────────────────────────────────


@sensitive_redactor
async def chat_completion(
    messages: list[dict],
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    *,
    model: str = "agent-model",
    stream: bool = False,
) -> Any:
    """
    统一对话接口 — 对应 src/llm/client.py 的 LLMClient.chat_completions()。

    - model 默认 ``agent-model``（LiteLLM 网关虚拟模型名）
    - 场景 B 直连时，需传入实际模型名（如 ``gpt-4o``）
    - stream=True 时自动聚合为完整 message 返回（供 tool calling 使用）
    """
    t0 = time.perf_counter()
    chain_debug(
        logger,
        step="llm.chat_completion",
        event="START",
        model=model,
        tools=bool(tools),
        msg_count=len(messages),
        max_tokens=max_tokens,
        stream_mode=stream,
        **_resolve_llm_route(),
    )
    client = get_client()
    provider = _detect_provider()
    model_name = _resolve_anthropic_model(model) if provider == "anthropic" else _resolve_openai_model(model)

    try:
        if provider == "anthropic":
            system_prompt, anthropic_messages = _normalize_messages_for_anthropic(messages)
            anthropic_tools = _normalize_tools_for_anthropic(tools)
            kwargs: dict[str, Any] = {
                "messages": anthropic_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            if anthropic_tools:
                kwargs["tools"] = anthropic_tools
            try:
                # Anthropic 直连先统一使用非流式，再转换成 OpenAI 兼容形态返回
                resp = await client.messages.create(model=model_name, **kwargs)
            except NotFoundError:
                fallback_model = await _pick_available_anthropic_model(client, model_name)
                if not fallback_model or fallback_model == model_name or not _is_gateway_alias(model):
                    raise
                chain_debug(
                    logger,
                    step="llm.chat_completion",
                    event="MODEL_FALLBACK",
                    from_model=model_name,
                    to_model=fallback_model,
                    provider="anthropic",
                )
                resp = await client.messages.create(model=fallback_model, **kwargs)
            msg = _anthropic_msg_to_openai_like(resp)
        elif stream:
            msg = await _accumulate_streamed_completion(
                client=client,
                model=model_name,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                t0=t0,
            )
        else:
            kwargs: dict[str, Any] = dict(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            resp = await client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        chain_debug(
            logger,
            step="llm.chat_completion",
            event="ERROR",
            elapsed_ms=elapsed_ms,
            stream_mode=stream,
        )
        logger.exception("[LLM] chat_completion failed")
        raise

    elapsed_ms = (time.perf_counter() - t0) * 1000
    n_tools = len(msg.tool_calls or [])
    content_len = len(msg.content or "")
    chain_debug(
        logger,
        step="llm.chat_completion",
        event="END",
        elapsed_ms=elapsed_ms,
        tool_calls=n_tools,
        content_chars=content_len,
        stream_mode=stream,
    )
    return msg


# ────────────────────────────────────────────────────────────────────
# 流式聚合 (内部)
# ────────────────────────────────────────────────────────────────────


async def _accumulate_streamed_completion(
    *,
    client: AsyncOpenAI | AsyncAnthropic,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    temperature: float,
    max_tokens: int,
    t0: float,
) -> Any:
    """流式拉取后聚合为与阻塞式相同的 message 形态（供 parse_tool_calls 使用）。"""
    kwargs: dict[str, Any] = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    stream_iter = await client.chat.completions.create(**kwargs)
    t_headers = (time.perf_counter() - t0) * 1000
    chain_debug(
        logger,
        step="llm.chat_completion",
        event="STREAM_OPEN",
        model=model,
        since_start_ms=round(t_headers, 1),
        stream_mode=True,
    )

    content_parts: list[str] = []
    tc_buf: dict[int, dict[str, str]] = {}
    chunk_idx = 0
    first_content_ms: float | None = None

    async for chunk in stream_iter:
        chunk_idx += 1
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            if first_content_ms is None:
                first_content_ms = (time.perf_counter() - t0) * 1000
                chain_debug(
                    logger,
                    step="llm.chat_completion",
                    event="FIRST_CONTENT",
                    ttft_ms=round(first_content_ms, 1),
                    raw_chunks=chunk_idx,
                    stream_mode=True,
                )
            content_parts.append(delta.content)
        if delta.tool_calls:
            for ptc in delta.tool_calls:
                i = ptc.index
                if i not in tc_buf:
                    tc_buf[i] = {"id": "", "name": "", "arguments": ""}
                if ptc.id:
                    tc_buf[i]["id"] = ptc.id
                if ptc.function:
                    if ptc.function.name:
                        tc_buf[i]["name"] = ptc.function.name
                    if ptc.function.arguments:
                        tc_buf[i]["arguments"] += ptc.function.arguments

    full_content = "".join(content_parts) if content_parts else None
    tool_calls_ns: list[Any] = []
    for i in sorted(tc_buf.keys()):
        b = tc_buf[i]
        tool_calls_ns.append(
            SimpleNamespace(
                id=b["id"] or f"call_{i}",
                function=SimpleNamespace(
                    name=b["name"],
                    arguments=b["arguments"],
                ),
            )
        )

    return SimpleNamespace(
        content=full_content,
        tool_calls=tool_calls_ns if tool_calls_ns else None,
    )


# ────────────────────────────────────────────────────────────────────
# chat_completion_stream (真流式 — yield text deltas)
# ────────────────────────────────────────────────────────────────────


async def chat_completion_stream(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str = "agent-model",
) -> AsyncIterator[str]:
    """真流式输出 — yield 每个 text delta，用于 SSE 推送。"""
    t0 = time.perf_counter()
    client = get_client()
    provider = _detect_provider()
    model_name = _resolve_anthropic_model(model) if provider == "anthropic" else _resolve_openai_model(model)

    chain_debug(
        logger,
        step="llm.chat_completion_stream",
        event="START",
        model=model,
        msg_count=len(messages),
        max_tokens=max_tokens,
        **_resolve_llm_route(),
    )

    # 输入脱敏日志
    for msg in messages:
        logger.debug(
            "LLM Stream Input [%s]: %s",
            msg.get("role"),
            redact_sensitive_info(str(msg.get("content", ""))[:200]),
        )

    try:
        if provider == "anthropic":
            system_prompt, anthropic_messages = _normalize_messages_for_anthropic(messages)
            kwargs: dict[str, Any] = {
                "messages": anthropic_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            selected_model = model_name
            try:
                stream_ctx = client.messages.stream(model=selected_model, **kwargs)
            except NotFoundError:
                fallback_model = await _pick_available_anthropic_model(client, model_name)
                if not fallback_model or fallback_model == model_name or not _is_gateway_alias(model):
                    raise
                chain_debug(
                    logger,
                    step="llm.chat_completion_stream",
                    event="MODEL_FALLBACK",
                    from_model=model_name,
                    to_model=fallback_model,
                    provider="anthropic",
                )
                selected_model = fallback_model
                stream_ctx = client.messages.stream(model=selected_model, **kwargs)

            try:
                async with stream_ctx as stream:
                    first_yield_done = False
                    chunk_idx = 0
                    total_out_chars = 0
                    async for text in stream.text_stream:
                        if not text:
                            continue
                        chunk_idx += 1
                        if not first_yield_done:
                            first_yield_done = True
                            ttft_ms = (time.perf_counter() - t0) * 1000
                            chain_debug(
                                logger,
                                step="llm.chat_completion_stream",
                                event="FIRST_YIELD",
                                ttft_ms=round(ttft_ms, 1),
                                raw_chunks=chunk_idx,
                            )
                        total_out_chars += len(text)
                        yield text
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    chain_debug(
                        logger,
                        step="llm.chat_completion_stream",
                        event="END",
                        elapsed_ms=elapsed_ms,
                        model=selected_model,
                        raw_chunks=chunk_idx,
                        yielded_chars=total_out_chars,
                        first_yield=first_yield_done,
                    )
                    return
            except NotFoundError:
                if not _is_gateway_alias(model):
                    raise
                fallback_model = await _pick_available_anthropic_model(client, selected_model)
                if not fallback_model or fallback_model == selected_model:
                    raise
                chain_debug(
                    logger,
                    step="llm.chat_completion_stream",
                    event="MODEL_FALLBACK",
                    from_model=selected_model,
                    to_model=fallback_model,
                    provider="anthropic",
                )
                async with client.messages.stream(model=fallback_model, **kwargs) as stream:
                    first_yield_done = False
                    chunk_idx = 0
                    total_out_chars = 0
                    async for text in stream.text_stream:
                        if not text:
                            continue
                        chunk_idx += 1
                        if not first_yield_done:
                            first_yield_done = True
                            ttft_ms = (time.perf_counter() - t0) * 1000
                            chain_debug(
                                logger,
                                step="llm.chat_completion_stream",
                                event="FIRST_YIELD",
                                ttft_ms=round(ttft_ms, 1),
                                raw_chunks=chunk_idx,
                            )
                        total_out_chars += len(text)
                        yield text
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    chain_debug(
                        logger,
                        step="llm.chat_completion_stream",
                        event="END",
                        elapsed_ms=elapsed_ms,
                        model=fallback_model,
                        raw_chunks=chunk_idx,
                        yielded_chars=total_out_chars,
                        first_yield=first_yield_done,
                    )
                    return

        stream = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        chain_debug(
            logger,
            step="llm.chat_completion_stream",
            event="ERROR",
            msg="before_iterate",
            elapsed_ms=elapsed_ms,
        )
        logger.exception("[LLM] chat_completion_stream create failed")
        raise

    t_http_ms = (time.perf_counter() - t0) * 1000
    chain_debug(
        logger,
        step="llm.chat_completion_stream",
        event="STREAM_OPEN",
        since_start_ms=round(t_http_ms, 1),
    )

    first_yield_done = False
    chunk_idx = 0
    total_out_chars = 0

    try:
        async for chunk in stream:
            chunk_idx += 1
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                text = delta.content
                if text:
                    if not first_yield_done:
                        first_yield_done = True
                        ttft_ms = (time.perf_counter() - t0) * 1000
                        chain_debug(
                            logger,
                            step="llm.chat_completion_stream",
                            event="FIRST_YIELD",
                            ttft_ms=round(ttft_ms, 1),
                            raw_chunks=chunk_idx,
                        )
                    total_out_chars += len(text)
                    yield text
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        chain_debug(
            logger,
            step="llm.chat_completion_stream",
            event="END",
            elapsed_ms=elapsed_ms,
            raw_chunks=chunk_idx,
            yielded_chars=total_out_chars,
            first_yield=first_yield_done,
        )


# ────────────────────────────────────────────────────────────────────
# get_embeddings — 对应 src/llm/client.py 的 LLMClient.get_embeddings()
# ────────────────────────────────────────────────────────────────────


async def get_embeddings(
    input_text: str,
    model: str = "embedding-model",
) -> List[float]:
    """
    获取向量嵌入 — 对应 src/llm/client.py 的 LLMClient.get_embeddings()。

    - 场景 A 使用 gateway 定义的 ``embedding-model`` 虚拟名
    - 场景 B 直连时需传入实际模型名（如 ``text-embedding-3-large``）
    """
    t0 = time.perf_counter()
    client = get_client()
    provider = _detect_provider()
    model_name = _resolve_openai_model(model)

    # 输入脱敏日志
    logger.debug("Embeddings Input (redacted): %s", redact_sensitive_info(input_text[:200]))

    chain_debug(
        logger,
        step="llm.get_embeddings",
        event="START",
        model=model,
        input_len=len(input_text),
        **_resolve_llm_route(),
    )

    try:
        if provider == "anthropic":
            raise RuntimeError(
                "Anthropic provider does not support OpenAI embeddings API in this project. "
                "Please configure OPENAI_API_KEY (or gateway) for embeddings."
            )
        response = await client.embeddings.create(
            input=input_text,
            model=model_name,
        )
        embedding = response.data[0].embedding
    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        chain_debug(
            logger,
            step="llm.get_embeddings",
            event="ERROR",
            elapsed_ms=elapsed_ms,
        )
        logger.exception("[LLM] get_embeddings failed")
        raise

    elapsed_ms = (time.perf_counter() - t0) * 1000
    chain_debug(
        logger,
        step="llm.get_embeddings",
        event="END",
        elapsed_ms=elapsed_ms,
        dimensions=len(embedding),
    )
    return embedding


# ────────────────────────────────────────────────────────────────────
# parse_tool_calls
# ────────────────────────────────────────────────────────────────────


def parse_tool_calls(message: Any) -> list[dict]:
    """Extract tool calls from an LLM response message."""
    if not message.tool_calls:
        chain_debug(logger, step="llm.parse_tool_calls", event="NONE")
        return []
    results = []
    for tc in message.tool_calls:
        try:
            args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, TypeError):
            args = {}
        results.append(
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": args,
            }
        )
    chain_debug(
        logger,
        step="llm.parse_tool_calls",
        event="OK",
        count=len(results),
        names=[r["name"] for r in results],
    )
    return results
