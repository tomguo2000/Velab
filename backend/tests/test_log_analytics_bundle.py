"""
Unit tests for LogAnalyticsAgent bundle_id integration.

Tests cover:
- execute() with bundle_id in context → _load_logs_from_bundle() called
- execute() without bundle_id        → _load_logs() fallback (data/logs/)
- _load_logs_from_bundle() 404 → falls back gracefully
- _load_logs_from_bundle() no valid_time_range → falls back
- _load_logs_from_bundle() HTTP error → falls back
- _load_logs_from_bundle() keyword filter on NDJSON lines
- _load_logs_from_bundle() network exception → falls back
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from agents.log_analytics import LogAnalyticsAgent


@pytest.fixture
def agent() -> LogAnalyticsAgent:
    return LogAnalyticsAgent()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ndjson(records: list[dict]) -> str:
    return "\n".join(json.dumps(r) for r in records)


def _bundle_status_ok(t_start: str = "2025-09-15T08:00:00Z", t_end: str = "2025-09-15T10:00:00Z") -> dict:
    return {
        "status": "done",
        "progress": 1.0,
        "valid_time_range_by_controller": {
            "iCGM": {"start": t_start, "end": t_end},
        },
    }


# ---------------------------------------------------------------------------
# _load_logs_from_bundle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_bundle_success(agent: LogAnalyticsAgent) -> None:
    """Returns formatted text from NDJSON when bundle exists and has valid_time_range."""
    records = [
        {"ts": "2025-09-15T09:00:00Z", "controller": "iCGM", "level": "ERROR", "msg": "eMMC write timeout"},
        {"ts": "2025-09-15T09:01:00Z", "controller": "MPU",  "level": "INFO",  "msg": "Download complete"},
    ]
    ndjson_text = _make_ndjson(records)

    mock_st_resp = MagicMock(status_code=200)
    mock_st_resp.json.return_value = _bundle_status_ok()
    mock_log_resp = MagicMock(status_code=200, text=ndjson_text)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=[mock_st_resp, mock_log_resp])

    with patch("agents.log_analytics.httpx.AsyncClient", return_value=mock_client):
        result = await agent._load_logs_from_bundle("test-bundle-id", None)

    assert "bundle:test-bundle-id" in result
    assert "eMMC write timeout" in result
    assert "Download complete" in result


@pytest.mark.asyncio
async def test_load_bundle_404_fallback(agent: LogAnalyticsAgent) -> None:
    """Falls back to data/logs/ when bundle 404."""
    mock_st_resp = MagicMock(status_code=404)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_st_resp)

    with patch("agents.log_analytics.httpx.AsyncClient", return_value=mock_client):
        with patch.object(agent, "_load_logs", return_value="fallback-content") as mock_fallback:
            result = await agent._load_logs_from_bundle("missing-bundle", None)

    mock_fallback.assert_called_once()
    assert result == "fallback-content"


@pytest.mark.asyncio
async def test_load_bundle_no_time_range_fallback(agent: LogAnalyticsAgent) -> None:
    """Falls back when bundle has no valid_time_range (still processing)."""
    mock_st_resp = MagicMock(status_code=200)
    mock_st_resp.json.return_value = {"status": "running", "progress": 0.5, "valid_time_range_by_controller": {}}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_st_resp)

    with patch("agents.log_analytics.httpx.AsyncClient", return_value=mock_client):
        with patch.object(agent, "_load_logs", return_value="mock-fallback") as mock_fallback:
            result = await agent._load_logs_from_bundle("in-progress-bundle", None)

    mock_fallback.assert_called_once()
    assert result == "mock-fallback"


@pytest.mark.asyncio
async def test_load_bundle_logs_api_error_fallback(agent: LogAnalyticsAgent) -> None:
    """Falls back when the logs endpoint returns non-200."""
    mock_st_resp = MagicMock(status_code=200)
    mock_st_resp.json.return_value = _bundle_status_ok()
    mock_log_resp = MagicMock(status_code=500, text="Internal Server Error")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=[mock_st_resp, mock_log_resp])

    with patch("agents.log_analytics.httpx.AsyncClient", return_value=mock_client):
        with patch.object(agent, "_load_logs", return_value="mock-fallback") as mock_fallback:
            result = await agent._load_logs_from_bundle("error-bundle", None)

    mock_fallback.assert_called_once()
    assert result == "mock-fallback"


@pytest.mark.asyncio
async def test_load_bundle_network_exception_fallback(agent: LogAnalyticsAgent) -> None:
    """Falls back gracefully when network raises an exception."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=ConnectionError("network unreachable"))

    with patch("agents.log_analytics.httpx.AsyncClient", return_value=mock_client):
        with patch.object(agent, "_load_logs", return_value="mock-fallback") as mock_fallback:
            result = await agent._load_logs_from_bundle("offline-bundle", None)

    mock_fallback.assert_called_once()
    assert result == "mock-fallback"


@pytest.mark.asyncio
async def test_load_bundle_keyword_filter(agent: LogAnalyticsAgent) -> None:
    """Only returns lines matching keywords when keywords are provided."""
    records = [
        {"ts": "T1", "controller": "iCGM", "level": "ERROR", "msg": "eMMC write timeout"},
        {"ts": "T2", "controller": "MPU",  "level": "INFO",  "msg": "Download complete OK"},
    ]
    ndjson_text = _make_ndjson(records)

    mock_st_resp = MagicMock(status_code=200)
    mock_st_resp.json.return_value = _bundle_status_ok()
    mock_log_resp = MagicMock(status_code=200, text=ndjson_text)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=[mock_st_resp, mock_log_resp])

    with patch("agents.log_analytics.httpx.AsyncClient", return_value=mock_client):
        result = await agent._load_logs_from_bundle("kw-bundle", ["emmc"])

    assert "eMMC write timeout" in result
    assert "Download complete" not in result


@pytest.mark.asyncio
async def test_load_bundle_keyword_no_match_fallback(agent: LogAnalyticsAgent) -> None:
    """Falls back to data/logs/ when keyword filter eliminates all lines."""
    records = [
        {"ts": "T1", "controller": "iCGM", "level": "INFO", "msg": "All OK"},
    ]
    ndjson_text = _make_ndjson(records)

    mock_st_resp = MagicMock(status_code=200)
    mock_st_resp.json.return_value = _bundle_status_ok()
    mock_log_resp = MagicMock(status_code=200, text=ndjson_text)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=[mock_st_resp, mock_log_resp])

    with patch("agents.log_analytics.httpx.AsyncClient", return_value=mock_client):
        with patch.object(agent, "_load_logs", return_value="mock-fallback") as mock_fallback:
            result = await agent._load_logs_from_bundle("kw-bundle", ["emmc", "timeout"])

    mock_fallback.assert_called_once()
    assert result == "mock-fallback"


# ---------------------------------------------------------------------------
# execute() routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_with_bundle_id_calls_bundle_loader(agent: LogAnalyticsAgent) -> None:
    """execute() routes to _load_logs_from_bundle when context contains bundle_id."""
    with patch.object(agent, "_load_logs_from_bundle", return_value="bundle-log-content") as mock_bundle:
        with patch.object(agent, "_mock_analyze") as mock_analyze:
            mock_analyze.return_value = MagicMock(success=True, confidence="high", summary="ok", detail="", sources=[], agent_name="log_analytics", display_name="Log Analytics Agent")
            with patch("agents.log_analytics.settings") as mock_settings:
                mock_settings.AGENTS_USE_LLM = False
                await agent.execute(
                    task="分析日志",
                    context={"bundle_id": "abc-123", "workspace_path": "/tmp/ws"},
                )

    mock_bundle.assert_called_once_with("abc-123", None, None)
    mock_analyze.assert_called_once()


@pytest.mark.asyncio
async def test_execute_without_bundle_id_calls_load_logs(agent: LogAnalyticsAgent) -> None:
    """execute() falls back to _load_logs when no bundle_id in context."""
    with patch.object(agent, "_load_logs", return_value="mock-log-content") as mock_load:
        with patch.object(agent, "_mock_analyze") as mock_analyze:
            mock_analyze.return_value = MagicMock(success=True, confidence="high", summary="ok", detail="", sources=[], agent_name="log_analytics", display_name="Log Analytics Agent")
            with patch("agents.log_analytics.settings") as mock_settings:
                mock_settings.AGENTS_USE_LLM = False
                await agent.execute(task="分析日志", context=None)

    mock_load.assert_called_once_with(None)
    mock_analyze.assert_called_once()


@pytest.mark.asyncio
async def test_execute_empty_bundle_content_returns_failure(agent: LogAnalyticsAgent) -> None:
    """execute() returns a low-confidence failure when bundle returns empty content."""
    with patch.object(agent, "_load_logs_from_bundle", return_value=""):
        with patch("agents.log_analytics.settings") as mock_settings:
            mock_settings.AGENTS_USE_LLM = False
            result = await agent.execute(
                task="无日志",
                context={"bundle_id": "empty-bundle"},
            )

    assert result.success is False
    assert result.confidence == "low"
    assert "bundle" in result.detail.lower() or "上传" in result.detail


# ---------------------------------------------------------------------------
# orchestrate() — bundle_id injection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_passes_bundle_id_to_agent_context() -> None:
    """orchestrate() injects bundle_id from parameter into agent_context."""
    from agents.orchestrator import orchestrate
    from unittest.mock import AsyncMock as AM, patch as p

    collected_contexts: list[dict] = []

    async def fake_execute(task, keywords=None, context=None):
        if context:
            collected_contexts.append(dict(context))
        from agents.base import AgentResult
        return AgentResult(
            agent_name="log_analytics",
            display_name="Log Analytics Agent",
            success=True,
            confidence="high",
            summary="ok",
        )

    fake_agent = MagicMock()
    fake_agent.name = "log_analytics"
    fake_agent.display_name = "Log Analytics Agent"
    fake_agent.execute = AM(side_effect=fake_execute)

    with p("agents.orchestrator.chat_completion") as mock_llm, \
         p("agents.orchestrator.parse_tool_calls") as mock_parse, \
         p("agents.orchestrator.workspace_manager") as mock_ws, \
         p("agents.orchestrator.settings") as mock_cfg, \
         p("agents.orchestrator.registry") as mock_reg:

        mock_cfg.WORKSPACE_ENABLED = False
        mock_cfg.ORCHESTRATOR_STREAM = False
        mock_cfg.AGENTS_USE_LLM = False

        mock_reg.get_tools_schema.return_value = []
        mock_reg.get_agent_descriptions.return_value = ""
        mock_reg.get.return_value = fake_agent

        mock_llm.return_value = MagicMock()
        mock_parse.return_value = [
            {"id": "call-1", "name": "log_analytics", "arguments": {"task": "分析日志", "keywords": []}}
        ]

        events = []
        async for ev in orchestrate("请分析日志", "fota-diagnostic", bundle_id="bundle-xyz"):
            events.append(ev)

    # At least one context should carry the bundle_id
    assert any(ctx.get("bundle_id") == "bundle-xyz" for ctx in collected_contexts), \
        f"bundle_id not found in collected_contexts: {collected_contexts}"

