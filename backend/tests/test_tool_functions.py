"""
services/tool_functions.py 单元测试

覆盖：
- read_workspace_file  — 文件存在 / 不存在 / 异常
- append_workspace_notes — workspace 存在 / 不存在
- update_todo_status   — 标记完成 / 取消完成 / 未找到 / 文件缺失
- clip_log_by_time_window — 时间裁剪 / fallback / 空窗口
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from services.tool_functions import (
    append_workspace_notes,
    clip_log_by_time_window,
    read_workspace_file,
    update_todo_status,
)


# ── read_workspace_file ───────────────────────────────────────────────────────

class TestReadWorkspaceFile:
    @pytest.mark.asyncio
    async def test_reads_existing_file(self, tmp_path: Path):
        (tmp_path / "notes.md").write_text("# Notes\nsome content", encoding="utf-8")
        result = await read_workspace_file(str(tmp_path), "notes.md")
        assert result["exists"] is True
        assert result["content"] == "# Notes\nsome content"
        assert result["size_bytes"] > 0
        assert result["filename"] == "notes.md"

    @pytest.mark.asyncio
    async def test_missing_file_returns_not_found(self, tmp_path: Path):
        result = await read_workspace_file(str(tmp_path), "missing.md")
        assert result["exists"] is False
        assert result["content"] is None
        assert result["size_bytes"] == 0

    @pytest.mark.asyncio
    async def test_default_filename_is_notes(self, tmp_path: Path):
        (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
        result = await read_workspace_file(str(tmp_path))
        assert result["exists"] is True

    @pytest.mark.asyncio
    async def test_exception_returns_error_dict(self, tmp_path: Path):
        # 指向一个不可读路径（目录替代文件）
        subdir = tmp_path / "notes.md"
        subdir.mkdir()  # 创建同名目录，读取时会抛出 IsADirectoryError
        result = await read_workspace_file(str(tmp_path), "notes.md")
        assert result["exists"] is False
        assert "error" in result


# ── append_workspace_notes ────────────────────────────────────────────────────

class TestAppendWorkspaceNotes:
    @pytest.mark.asyncio
    async def test_workspace_not_found_returns_failure(self, tmp_path: Path):
        with patch("services.workspace_manager.workspace_manager") as mock_wm:
            mock_wm.get.return_value = None
            result = await append_workspace_notes(str(tmp_path / "ws_abc"), "LogAgent", "内容")
        assert result["success"] is False
        assert result["reason"] == "workspace_not_found"

    @pytest.mark.asyncio
    async def test_successful_append(self, tmp_path: Path):
        fake_ctx = MagicMock()
        with patch("services.workspace_manager.workspace_manager") as mock_wm:
            mock_wm.get.return_value = fake_ctx
            mock_wm.append = AsyncMock(return_value=True)
            result = await append_workspace_notes(str(tmp_path / "ws_abc"), "RcaAgent", "发现问题")
        assert result["success"] is True
        assert result["section"] == "RcaAgent"
        assert result["file"] == "notes.md"

    @pytest.mark.asyncio
    async def test_append_failure_propagated(self, tmp_path: Path):
        fake_ctx = MagicMock()
        with patch("services.workspace_manager.workspace_manager") as mock_wm:
            mock_wm.get.return_value = fake_ctx
            mock_wm.append = AsyncMock(return_value=False)
            result = await append_workspace_notes(str(tmp_path / "ws_abc"), "Agent", "x")
        assert result["success"] is False


# ── update_todo_status ────────────────────────────────────────────────────────

class TestUpdateTodoStatus:
    def _make_todo(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "todo.md"
        p.write_text(content, encoding="utf-8")
        return tmp_path

    @pytest.mark.asyncio
    async def test_marks_item_completed(self, tmp_path: Path):
        self._make_todo(tmp_path, "- [ ] 升级固件\n- [ ] 重启设备\n")
        result = await update_todo_status(str(tmp_path), "升级固件", completed=True)
        assert result["success"] is True
        assert result["new_status"] == "[x]"
        assert "[x] 升级固件" in (tmp_path / "todo.md").read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_marks_item_uncompleted(self, tmp_path: Path):
        self._make_todo(tmp_path, "- [x] 升级完成\n")
        result = await update_todo_status(str(tmp_path), "升级完成", completed=False)
        assert result["success"] is True
        assert result["new_status"] == "[ ]"

    @pytest.mark.asyncio
    async def test_missing_todo_file_returns_failure(self, tmp_path: Path):
        result = await update_todo_status(str(tmp_path), "某任务", completed=True)
        assert result["success"] is False
        assert result["reason"] == "todo.md not found"

    @pytest.mark.asyncio
    async def test_item_not_found_returns_failure(self, tmp_path: Path):
        self._make_todo(tmp_path, "- [ ] 其他任务\n")
        result = await update_todo_status(str(tmp_path), "不存在的任务", completed=True)
        assert result["success"] is False
        assert result["reason"] == "item_not_found_or_already_set"

    @pytest.mark.asyncio
    async def test_already_set_returns_failure(self, tmp_path: Path):
        self._make_todo(tmp_path, "- [x] 已完成任务\n")
        # 再次标记完成 → old_mark "[ ]" 不存在
        result = await update_todo_status(str(tmp_path), "已完成任务", completed=True)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_case_insensitive_match(self, tmp_path: Path):
        self._make_todo(tmp_path, "- [ ] FOTA升级\n")
        result = await update_todo_status(str(tmp_path), "fota升级", completed=True)
        assert result["success"] is True


# ── clip_log_by_time_window ───────────────────────────────────────────────────

_SAMPLE_LOG = """\
[2026-01-15 14:23:00.000][iCGM][INFO] FOTA start
[2026-01-15 14:23:30.000][iCGM][INFO] DOWNLOAD begin
[2026-01-15 14:24:00.000][iCGM][ERROR] EMMC_WRITE_TIMEOUT
[2026-01-15 14:24:05.000][iCGM][WARN] retry 1
[2026-01-15 14:25:00.000][iCGM][INFO] REBOOT triggered
"""


class TestClipLogByTimeWindow:
    @pytest.mark.asyncio
    async def test_clips_within_window(self):
        # 故障时刻 14:24:00，前60s=14:23:00，后30s=14:24:30
        result = await clip_log_by_time_window(
            _SAMPLE_LOG,
            fault_time="2026-01-15 14:24:00",
            before_seconds=60,
            after_seconds=30,
        )
        assert result["fallback"] is False
        assert "EMMC_WRITE_TIMEOUT" in result["clipped_text"]
        assert "retry 1" in result["clipped_text"]
        # 14:25:00 超出窗口，不应出现
        assert "REBOOT triggered" not in result["clipped_text"]
        assert result["matched_lines"] > 0

    @pytest.mark.asyncio
    async def test_excludes_lines_before_window(self):
        # 窗口只取故障时刻后5秒
        result = await clip_log_by_time_window(
            _SAMPLE_LOG,
            fault_time="2026-01-15 14:24:00",
            before_seconds=0,
            after_seconds=5,
        )
        assert result["fallback"] is False
        assert "FOTA start" not in result["clipped_text"]
        assert "EMMC_WRITE_TIMEOUT" in result["clipped_text"]

    @pytest.mark.asyncio
    async def test_invalid_fault_time_fallback(self):
        result = await clip_log_by_time_window(
            _SAMPLE_LOG,
            fault_time="not-a-date",
        )
        assert result["fallback"] is True
        assert result["clipped_text"] != ""

    @pytest.mark.asyncio
    async def test_no_timestamp_lines_fallback(self):
        log_no_ts = "plain line 1\nplain line 2\n"
        result = await clip_log_by_time_window(
            log_no_ts,
            fault_time="2026-01-15 14:24:00",
        )
        assert result["fallback"] is True

    @pytest.mark.asyncio
    async def test_max_lines_truncation(self):
        # 构造 200 行日志
        many_lines = "\n".join(
            f"[2026-01-15 14:24:0{i % 10}.000][ctrl][INFO] line {i}" for i in range(200)
        )
        result = await clip_log_by_time_window(
            many_lines,
            fault_time="2026-01-15 14:24:05",
            before_seconds=60,
            after_seconds=60,
            max_lines=50,
        )
        assert result["matched_lines"] <= 50

    @pytest.mark.asyncio
    async def test_window_start_end_reported(self):
        result = await clip_log_by_time_window(
            _SAMPLE_LOG,
            fault_time="2026-01-15 14:24:00",
            before_seconds=60,
            after_seconds=30,
        )
        assert result["window_start"] == "2026-01-15 14:23:00"
        assert result["window_end"] == "2026-01-15 14:24:30"

    @pytest.mark.asyncio
    async def test_total_lines_reported(self):
        result = await clip_log_by_time_window(_SAMPLE_LOG, fault_time="2026-01-15 14:24:00")
        assert result["total_lines"] == len(_SAMPLE_LOG.splitlines())
