"""
Tool Use 函数实现 — 供 Agent 调用的 workspace 操作工具。

旧的 DiagnosisEvent 时间线/上下文/阶段查询函数已随旧解析管线一并移除；
日志事件查询请走 log_pipeline 的 /api/bundles/{id}/events 与 /logs。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 匹配标准日志行时间戳：[2026-01-01 12:34:56.789][ctrl][level] msg
# 也兼容不带毫秒的格式
_TS_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
)


def _parse_log_ts(line: str) -> Optional[datetime]:
    """从日志行首的 [timestamp] 中解析出 datetime（naive UTC）。"""
    m = _TS_RE.match(line)
    if not m:
        return None
    raw = m.group(1).replace("T", " ")
    # 去掉超过 6 位的微秒部分（strptime 只支持最多 6 位）
    dot_pos = raw.rfind(".")
    if dot_pos != -1:
        raw = raw[: dot_pos + 7]  # 保留 .xxxxxx
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


async def clip_log_by_time_window(
    log_content: str,
    fault_time: str,
    before_seconds: int = 60,
    after_seconds: int = 30,
    max_lines: int = 500,
) -> Dict[str, Any]:
    """
    按时间窗口裁剪日志，返回故障时刻前后的上下文行。

    Args:
        log_content: 原始日志文本（多行字符串，支持 NDJSON 或结构化文本行）
        fault_time:  故障时刻，ISO 8601 格式 (e.g. "2026-01-15 14:23:45")
        before_seconds: 故障时刻之前保留的秒数（默认 60）
        after_seconds:  故障时刻之后保留的秒数（默认 30）
        max_lines:   结果最多返回多少行（防止超出 LLM 上下文）

    Returns:
        {
            "clipped_text": str,        # 裁剪后的日志文本
            "total_lines": int,         # 原始行数
            "matched_lines": int,       # 匹配行数
            "window_start": str,        # 窗口起始时间
            "window_end": str,          # 窗口结束时间
            "fallback": bool,           # True 表示未能按时间裁剪，返回全部内容
        }
    """
    lines = log_content.splitlines()
    total_lines = len(lines)

    # 解析 fault_time
    fault_dt: Optional[datetime] = None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            fault_dt = datetime.strptime(fault_time.strip(), fmt)
            break
        except ValueError:
            continue

    if fault_dt is None:
        logger.warning("clip_log_by_time_window: cannot parse fault_time=%r, returning full content", fault_time)
        clipped = "\n".join(lines[:max_lines])
        return {
            "clipped_text": clipped,
            "total_lines": total_lines,
            "matched_lines": len(lines[:max_lines]),
            "window_start": "",
            "window_end": "",
            "fallback": True,
        }

    window_start = fault_dt - timedelta(seconds=before_seconds)
    window_end = fault_dt + timedelta(seconds=after_seconds)

    matched: List[str] = []
    last_ts: Optional[datetime] = None

    for line in lines:
        ts = _parse_log_ts(line)
        if ts is not None:
            last_ts = ts
        # 若解析不到时间戳，沿用上一行时间戳（连续的日志行可能不带独立时间）
        effective_ts = last_ts
        if effective_ts is None:
            continue
        if window_start <= effective_ts <= window_end:
            matched.append(line)

    # 如果时间裁剪后完全为空（日志时间戳格式不匹配），退化返回全部
    fallback = len(matched) == 0
    if fallback:
        logger.warning(
            "clip_log_by_time_window: no lines matched window %s ~ %s, returning full content",
            window_start,
            window_end,
        )
        matched = lines

    # 截断到 max_lines
    matched = matched[:max_lines]

    return {
        "clipped_text": "\n".join(matched),
        "total_lines": total_lines,
        "matched_lines": len(matched),
        "window_start": window_start.strftime("%Y-%m-%d %H:%M:%S"),
        "window_end": window_end.strftime("%Y-%m-%d %H:%M:%S"),
        "fallback": fallback,
    }


async def read_workspace_file(
    workspace_path: str,
    filename: str = "notes.md",
) -> Dict[str, Any]:
    """读取工作区文件（focus.md / notes.md / todo.md）以理解全局上下文。"""
    file_path = Path(workspace_path) / filename
    try:
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            return {
                "filename": filename,
                "content": content,
                "exists": True,
                "size_bytes": len(content.encode("utf-8")),
            }
        return {
            "filename": filename,
            "content": None,
            "exists": False,
            "size_bytes": 0,
        }
    except Exception as e:
        logger.warning("read_workspace_file failed: %s", e)
        return {
            "filename": filename,
            "content": None,
            "exists": False,
            "size_bytes": 0,
            "error": str(e),
        }


async def append_workspace_notes(
    workspace_path: str,
    agent_name: str,
    content: str,
) -> Dict[str, Any]:
    """向工作区 notes.md 追加分析发现，按 Agent section 隔离。"""
    from services.workspace_manager import workspace_manager

    ws_dir = Path(workspace_path)
    task_id = ws_dir.name

    ctx = workspace_manager.get(task_id)
    if ctx is None:
        logger.warning("Workspace not found for task_id=%s, skipping notes append", task_id)
        return {"success": False, "file": "notes.md", "section": agent_name, "reason": "workspace_not_found"}

    success = await workspace_manager.append(ctx, "notes.md", agent_name, content)
    return {"success": success, "file": "notes.md", "section": agent_name}


async def update_todo_status(
    workspace_path: str,
    item_text: str,
    completed: bool = True,
) -> Dict[str, Any]:
    """更新工作区 todo.md 中匹配 ``item_text`` 的复选框状态。"""
    todo_path = Path(workspace_path) / "todo.md"
    new_mark = "[x]" if completed else "[ ]"
    old_mark = "[ ]" if completed else "[x]"

    try:
        if not todo_path.exists():
            return {"success": False, "item": item_text, "reason": "todo.md not found"}

        content = todo_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        updated = False

        for i, line in enumerate(lines):
            if item_text.lower() in line.lower() and old_mark in line:
                lines[i] = line.replace(old_mark, new_mark, 1)
                updated = True
                break

        if updated:
            todo_path.write_text("\n".join(lines), encoding="utf-8")
            return {"success": True, "item": item_text, "new_status": new_mark}
        else:
            return {"success": False, "item": item_text, "reason": "item_not_found_or_already_set"}

    except Exception as e:
        logger.warning("update_todo_status failed: %s", e)
        return {"success": False, "item": item_text, "error": str(e)}
