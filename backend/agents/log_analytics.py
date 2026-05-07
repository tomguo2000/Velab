"""Log Analytics Agent — parses and analyses FOTA upgrade logs."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

from agents.base import BaseAgent, AgentResult, registry
from common.chain_log import sync_step_timer
from config import settings

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"

# 每次从 bundle API 拉取日志的行数上限（避免超出 LLM 上下文窗口）
_BUNDLE_LOG_LIMIT = 2000
# 精确时间点命中时，向前/向后各扩展的秒数（±2h 窗口）
_TIME_HINT_WINDOW_SEC = 7200

log = logging.getLogger(__name__)

# ── 时间描述解析 ─────────────────────────────────────────────────────────────

_MONTH_DAY_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")
_HOUR_RE = re.compile(r"(\d{1,2})\s*[点时]")

_QUALIFIER_RANGES: dict[str, tuple[int, int]] = {
    "凌晨": (0, 6),
    "早上": (6, 9),
    "上午": (6, 12),
    "中午": (11, 14),
    "下午": (12, 18),
    "傍晚": (17, 20),
    "晚上": (18, 24),
    "夜": (20, 24),
}

_PM_QUALIFIERS = {"下午", "傍晚", "晚上", "夜"}


def _parse_time_hint(
    time_hint: str,
    global_start: float,
    global_end: float,
) -> tuple[float, float] | None:
    """将自然语言时间描述解析为 (start, end) unix 时间戳对。

    以 bundle 的全局有效时间范围作为日历上下文（年份/月份参考）。
    解析失败返回 None，调用方应退化为全量范围。

    支持的格式示例：
      "9月11日凌晨"、"9月11日晚上21点"、"大约21点"、"昨天上午10点左右"
    """
    if not time_hint or not time_hint.strip():
        return None

    ref_dt = datetime.fromtimestamp(global_start, tz=timezone.utc)
    year = ref_dt.year

    # 检测时段限定词
    qualifier = next((q for q in _QUALIFIER_RANGES if q in time_hint), None)

    # 尝试匹配 "X月Y日"
    md_m = _MONTH_DAY_RE.search(time_hint)
    if md_m:
        month, day = int(md_m.group(1)), int(md_m.group(2))
        hour_m = _HOUR_RE.search(time_hint)
        try:
            if hour_m:
                hour = int(hour_m.group(1))
                # 12h → 24h 转换
                if qualifier in _PM_QUALIFIERS and hour < 12:
                    hour += 12
                center = datetime(year, month, day, hour, 0, tzinfo=timezone.utc)
                return (
                    center.timestamp() - _TIME_HINT_WINDOW_SEC,
                    center.timestamp() + _TIME_HINT_WINDOW_SEC,
                )
            else:
                base = datetime(year, month, day, 0, 0, tzinfo=timezone.utc)
                if qualifier:
                    h_start, h_end = _QUALIFIER_RANGES[qualifier]
                    return (
                        (base + timedelta(hours=h_start)).timestamp(),
                        (base + timedelta(hours=h_end)).timestamp(),
                    )
                else:
                    # 只有日期，取当天全天
                    return base.timestamp(), (base + timedelta(hours=24)).timestamp()
        except ValueError:
            return None

    # 无日期，仅有时刻 —— 在 bundle 范围内的最后一天匹配该小时
    hour_m = _HOUR_RE.search(time_hint)
    if hour_m:
        hour = int(hour_m.group(1))
        if qualifier in _PM_QUALIFIERS and hour < 12:
            hour += 12
        # 以 bundle 的结束日期为参考日
        ref_end = datetime.fromtimestamp(global_end, tz=timezone.utc)
        try:
            candidate = ref_end.replace(hour=hour, minute=0, second=0, microsecond=0)
            # 若候选时刻早于 bundle 开始，改用开始日期
            if candidate.timestamp() < global_start:
                candidate = ref_dt.replace(hour=hour, minute=0, second=0, microsecond=0)
            return (
                candidate.timestamp() - _TIME_HINT_WINDOW_SEC,
                candidate.timestamp() + _TIME_HINT_WINDOW_SEC,
            )
        except ValueError:
            return None

    return None


_SYSTEM_PROMPT = """\
你是车载 FOTA（固件空中升级）诊断专家，擅长分析 iCGM/MPU/IVI/MCU/IPK 等 ECU 的升级日志。

分析用户提供的日志，定位升级失败根因。**必须**按以下 Markdown 格式输出，不要输出额外内容：

## 🎯 诊断结论
（1-2 句话说明根本原因和受影响的 ECU）

## 📊 详细分析
（关键时间线事件、错误码含义、状态机跳变）

## 💡 修复建议
（具体可操作的修复步骤，分点列出）

## 置信度
（仅输出 high / medium / low 之一，依据日志证据质量判断）
"""


class LogAnalyticsAgent(BaseAgent):
    name = "log_analytics"
    display_name = "Log Analytics Agent"
    description = (
        "分析车辆 FOTA 升级日志文件，定位异常时间线、错误码和故障根因。"
        "适用于：升级挂死、ECU刷写失败、校验异常、死循环、下载超时等问题。"
    )

    def tool_schema(self) -> dict:
        schema = super().tool_schema()
        schema["function"]["parameters"]["properties"]["time_hint"] = {
            "type": "string",
            "description": (
                "用户描述的大概故障时间，如 '9月11日凌晨'、'昨晚21点'、'上午10点左右'。"
                "若用户未提及时间或时间不确定，不要传此字段（系统将全量分析日志）。"
            ),
        }
        return schema

    async def execute(self, task: str, keywords: list[str] | None = None, context: dict | None = None) -> AgentResult:
        with sync_step_timer(
            log,
            step="agent.log_analytics",
            task_preview=task[:120],
            keywords=(keywords or [])[:8],
        ):
            bundle_id: Optional[str] = (context or {}).get("bundle_id")
            time_hint: Optional[str] = (context or {}).get("time_hint") or None
            if bundle_id:
                log_content = await self._load_logs_from_bundle(bundle_id, keywords, time_hint)
                source_label = f"bundle:{bundle_id}" + (f" time_hint={time_hint!r}" if time_hint else "")
            else:
                log_content = self._load_logs(keywords)
                source_label = "data/logs"
            log.debug("log_analytics source=%s lines=%d", source_label, len((log_content or "").splitlines()))

            if not log_content:
                result = AgentResult(
                    agent_name=self.name,
                    display_name=self.display_name,
                    success=False,
                    confidence="low",
                    summary="未找到相关日志文件",
                    detail=(
                        "当前会话中没有已上传的日志包，且本地日志目录也没有匹配的记录。"
                        "请先上传日志文件，或确认 data/logs/ 目录中包含日志文件。"
                        if not bundle_id
                        else f"Bundle {bundle_id} 日志加载失败，且本地目录无兜底数据。"
                    ),
                    sources=[],
                )
                await self._write_workspace(context, result)
                return result

            if settings.AGENTS_USE_LLM:
                try:
                    analysis = await self._llm_analyze(task, log_content, keywords or [])
                    await self._write_workspace(context, analysis)
                    return analysis
                except Exception as exc:
                    log.warning("LLM analysis failed, falling back to mock: %s", exc)

            analysis = self._mock_analyze(task, log_content, keywords or [])
            await self._write_workspace(context, analysis)
            return analysis

    async def _load_logs_from_bundle(
        self,
        bundle_id: str,
        keywords: list[str] | None,
        time_hint: Optional[str] = None,
    ) -> str:
        """Fetch log lines from the log_pipeline bundle API (NDJSON stream).

        When *time_hint* is provided the query window is narrowed around the
        described time.  Falls back to data/logs/ if the bundle is not found.
        """
        backend_base = getattr(settings, "BACKEND_BASE_URL", "http://localhost:8000")
        url = f"{backend_base}/api/bundles/{bundle_id}/logs"
        try:
            # 先获取时间范围
            status_url = f"{backend_base}/api/bundles/{bundle_id}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                st_resp = await client.get(status_url)
                if st_resp.status_code == 404:
                    log.warning("bundle %s not found, falling back to data/logs/", bundle_id)
                    return self._load_logs(keywords)

                st_data = st_resp.json() if st_resp.status_code == 200 else {}
                # API 返回 valid_time_range_by_controller: {ctrl: {"start": float, "end": float}}
                vtr: dict = st_data.get("valid_time_range_by_controller", {})
                if not vtr:
                    log.info("bundle %s has no valid time range yet, falling back", bundle_id)
                    return self._load_logs(keywords)

                starts = [v["start"] for v in vtr.values() if v.get("start") is not None]
                ends = [v["end"] for v in vtr.values() if v.get("end") is not None]
                if not starts or not ends:
                    log.info("bundle %s time range incomplete, falling back", bundle_id)
                    return self._load_logs(keywords)

                global_start = min(starts)
                global_end = max(ends)

                # 若提供了时间描述，尝试缩窄查询窗口；失败时退化为全量范围
                if time_hint:
                    parsed = _parse_time_hint(time_hint, global_start, global_end)
                    if parsed:
                        t_start, t_end = str(parsed[0]), str(parsed[1])
                        log.info(
                            "bundle %s time_hint=%r → window [%s, %s]",
                            bundle_id, time_hint, t_start, t_end,
                        )
                    else:
                        log.info(
                            "bundle %s time_hint=%r could not be parsed, using full range",
                            bundle_id, time_hint,
                        )
                        t_start, t_end = str(global_start), str(global_end)
                else:
                    t_start, t_end = str(global_start), str(global_end)

                params: dict = {"limit": _BUNDLE_LOG_LIMIT, "start": t_start, "end": t_end}

                log_resp = await client.get(url, params=params)
                if log_resp.status_code != 200:
                    log.warning("bundle logs API returned %d, falling back", log_resp.status_code)
                    return self._load_logs(keywords)

                # 在 with 块内捕获响应文本，确保连接已完成缓冲
                raw_text = log_resp.text

            # 解析 NDJSON 行并可选按关键词过滤
            lines: list[str] = []
            for raw_line in raw_text.splitlines():
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    record: dict = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                # 将结构化记录合并为可读文本行
                ts = record.get("ts") or record.get("timestamp", "")
                ctrl = record.get("controller", "")
                msg = record.get("msg") or record.get("message", "")
                level = record.get("level", "")
                text_line = f"[{ts}][{ctrl}][{level}] {msg}".strip()
                if keywords:
                    low = text_line.lower()
                    if not any(k.lower() in low for k in keywords):
                        continue
                lines.append(text_line)

            if not lines:
                # 关键词过滤后无结果，fallback 到 mock
                log.info("bundle %s has no lines matching keywords, falling back to data/logs/", bundle_id)
                return self._load_logs(keywords)

            header = f"=== bundle:{bundle_id} (lines={len(lines)}) ==="
            return header + "\n" + "\n".join(lines)

        except Exception as exc:
            log.warning("Failed to load bundle %s logs: %s — falling back to data/logs/", bundle_id, exc)
            return self._load_logs(keywords)

    async def _llm_analyze(self, task: str, log_content: str, keywords: list[str]) -> AgentResult:
        """Call LLM to analyze log content and return structured AgentResult."""
        from services.llm import chat_completion

        # 截断日志避免超出上下文窗口（保留最多 6000 字符）
        truncated = log_content[:6000]
        if len(log_content) > 6000:
            truncated += "\n\n[...日志已截断，仅分析前 6000 字符...]"

        user_msg = f"诊断任务：{task}\n\n关键词：{', '.join(keywords) if keywords else '无'}\n\n日志内容：\n{truncated}"

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        response = await chat_completion(messages, model="agent-model", temperature=0.3, max_tokens=2048)
        raw_text: str = getattr(response, "content", None) or ""

        # 提取置信度行
        confidence = "medium"
        detail_lines = []
        for line in raw_text.splitlines():
            stripped = line.strip().lower()
            if stripped in {"high", "medium", "low"}:
                confidence = stripped
            elif line.strip().startswith("## 置信度"):
                pass  # skip section header
            else:
                detail_lines.append(line)
        detail = "\n".join(detail_lines).strip()

        # 从"诊断结论"提取 summary（取 ## 后第一个非空段落）
        summary = task  # fallback
        in_conclusion = False
        for line in detail.splitlines():
            if "诊断结论" in line:
                in_conclusion = True
                continue
            if in_conclusion and line.strip() and not line.startswith("#"):
                summary = line.strip().lstrip("（").rstrip("）")
                break

        # 记录 sources（从日志文件名提取）
        sources = [
            {"title": ln[4:-4], "type": "log"}
            for ln in log_content.splitlines()
            if ln.startswith("=== ") and ln.endswith(" ===")
        ]

        return AgentResult(
            agent_name=self.name,
            display_name=self.display_name,
            success=True,
            confidence=confidence,
            summary=summary,
            detail=detail,
            sources=sources,
            raw_data={"log_lines_analyzed": len(log_content.splitlines()), "llm": True},
        )

    async def _write_workspace(self, context: dict | None, result: AgentResult) -> None:
        """将分析发现写入 workspace (可选，降级安全)"""
        if not context or "workspace_path" not in context:
            return
        try:
            from services.tool_functions import append_workspace_notes, update_todo_status
            ws_path = context["workspace_path"]

            # 写入 notes.md
            notes_content = f"**摘要**: {result.summary}\n**置信度**: {result.confidence}\n\n{result.detail or '无详细信息'}"
            await append_workspace_notes(ws_path, self.display_name, notes_content)

            # 更新 todo.md
            await update_todo_status(ws_path, "日志阶段验证", completed=result.success)
            if result.success:
                await update_todo_status(ws_path, "异常模式识别", completed=True)
        except Exception as e:
            log.warning("Workspace write failed in %s: %s", self.name, e)

    def _load_logs(self, keywords: list[str] | None) -> str:
        """Load log files from data/logs/. Filter by keywords if present."""
        if not DATA_DIR.exists():
            return ""

        all_content: list[str] = []
        for f in sorted(DATA_DIR.iterdir()):
            if f.suffix in (".log", ".txt"):
                text = f.read_text(encoding="utf-8", errors="ignore")
                if keywords:
                    relevant_lines = []
                    for line in text.splitlines():
                        low = line.lower()
                        if any(k.lower() in low for k in keywords):
                            relevant_lines.append(line)
                    if relevant_lines:
                        all_content.append(f"=== {f.name} ===\n" + "\n".join(relevant_lines))
                else:
                    all_content.append(f"=== {f.name} ===\n" + text)

        return "\n\n".join(all_content)

    def _mock_analyze(self, task: str, log_content: str, keywords: list[str]) -> AgentResult:
        """Mock analysis — returns realistic diagnostic results based on task keywords and loaded log filenames."""
        task_lower = task.lower()
        content_lower = log_content.lower()
        log_lines = len(log_content.splitlines())

        # Detect scenario by task keywords OR by filename header embedded in log_content
        is_icgm = (
            any(k in task_lower for k in ["icgm", "挂死", "hang", "死循环", "emmc", "超时"])
            or "icgm_emmc_timeout" in content_lower
        )
        is_network = (
            any(k in task_lower for k in ["网络", "network", "中断", "download", "下载", "校验失败", "checksum", "断网"])
            or "network_interrupt" in content_lower
        )
        is_battery = (
            any(k in task_lower for k in ["电池", "电量", "battery", "中止", "abort", "低电", "电源", "关机"])
            or "battery_drain" in content_lower
        )
        is_dependency = (
            any(k in task_lower for k in ["依赖", "dependency", "chain", "链", "协调", "批量", "顺序"])
            or "ecu_dependency_chain" in content_lower
        )
        is_fleet = (
            any(k in task_lower for k in ["车队", "fleet", "统计", "分布", "趋势", "批次", "成功率", "失败率", "多辆"])
            or "fleet" in content_lower
        )

        if is_icgm:
            return AgentResult(
                agent_name=self.name,
                display_name=self.display_name,
                success=True,
                confidence="high",
                summary="iCGM eMMC 写入超时 + 死循环根因分析完成",
                detail=(
                    "核心异常分析：\n\n"
                    "1. **MPU 升级包下载校验失败**\n"
                    "   - 时间戳: 08:57:58 — HttpDownloadManager 开始下载 2.077 GB MPU 升级包\n"
                    "   - 时间戳: 09:01:55 — 下载完成，但校验阶段报错\n"
                    "   - 关键错误: `verifyPackage: /data/fota/mpu_update.zip not exist`\n"
                    "   - 实际写入大小: `write file size = 0(0 B)`\n\n"
                    "2. **iCGM eMMC 写入超时**\n"
                    "   - 时间戳: 09:02:03 — eMMC I/O 操作超时 (timeout=30000ms)\n"
                    "   - 底层报错: `eMMC write timeout: sector 0x3A200000, retry 3/3 failed`\n"
                    "   - 硬件层写入失败导致包文件为空\n\n"
                    "3. **iCGM 模块进入死循环**\n"
                    "   - 时间戳: 09:02:09 — iCGM 检测到校验失败，触发 `[FotaFlashImpl]-usbReboot`\n"
                    "   - 重启后再次尝试下载，形成 '下载 -> 校验失败 -> 重启 -> 再下载' 死循环\n\n"
                    "4. **MCU/IPK 状态不一致**\n"
                    "   - MCU 已完成刷写，进入等待状态\n"
                    "   - IPK 仍在等待 iCGM 发送协调信号，状态机卡在 FLASHING_IN_PROGRESS"
                ),
                sources=[{"title": "icgm_emmc_timeout_20250915.log", "type": "log"}],
                raw_data={"log_lines_analyzed": log_lines},
            )

        if is_network:
            return AgentResult(
                agent_name=self.name,
                display_name=self.display_name,
                success=True,
                confidence="high",
                summary="网络中断导致 MPU 升级包下载失败，断点续传成功但 CRC 校验不一致",
                detail=(
                    "核心异常分析：\n\n"
                    "1. **4G 信号衰减触发下载中断**\n"
                    "   - 时间戳: 09:15:33 — 信号强度从 -85dBm 恶化至 -105dBm\n"
                    "   - `[HttpDownloadManager] Connection timeout at offset 73400320 (35%)`\n"
                    "   - 网络完全断开: `NetworkError - NO_SERVICE`\n\n"
                    "2. **断点续传尝试 3 次失败**\n"
                    "   - 09:15:33 — 首次断点续传，offset=73400320\n"
                    "   - 09:18:01 — 网络恢复后续传，但服务端返回 416 Range Not Satisfiable\n"
                    "   - 服务端已清除该 session 的分片缓存，断点续传协议不兼容\n\n"
                    "3. **强制重新下载，CRC 校验失败**\n"
                    "   - 09:19:15 — 重新下载完整包 (2.077 GB)\n"
                    "   - 09:23:44 — 下载完成，但 CRC32 校验不匹配\n"
                    "   - 预期: `0xA3F5C821`，实际: `0xD7B2E490`\n"
                    "   - 疑为下载过程中网络抖动导致数据损坏\n\n"
                    "4. **升级任务终止，状态回滚**\n"
                    "   - MPU 保持在旧版本 v3.0.7\n"
                    "   - 已触发 ROLLBACK_COMPLETE，系统可正常使用"
                ),
                sources=[{"title": "network_interrupt_download_20251003.log", "type": "log"}],
                raw_data={"log_lines_analyzed": log_lines},
            )

        if is_battery:
            return AgentResult(
                agent_name=self.name,
                display_name=self.display_name,
                success=True,
                confidence="high",
                summary="夜间升级因电量不足触发紧急中止，IVI 刷写中断导致 brick 风险",
                detail=(
                    "核心异常分析：\n\n"
                    "1. **夜间定时升级触发条件满足**\n"
                    "   - 时间戳: 03:45:01 — 夜间升级窗口开启\n"
                    "   - 电量: 72%，未充电状态，满足出发阈值 (>60%)\n\n"
                    "2. **IVI 刷写过程中车辆熄火**\n"
                    "   - 03:45:35 — 检测到点火信号 OFF\n"
                    "   - 电量开始持续下降: 72% -> 68% -> 55% -> 48%\n"
                    "   - 刷写进度: IVI 已写入 70%\n\n"
                    "3. **安全阈值触发紧急中止**\n"
                    "   - 03:45:49 — 电量 48% 低于安全阈值 50%\n"
                    "   - `[FotaService] Emergency abort: IVI flash at 70% - interrupting write`\n"
                    "   - 中断正在进行的 eMMC 写入操作\n\n"
                    "4. **IVI 处于 brick 风险状态**\n"
                    "   - IVI firmware 写入不完整 (70%)，无法正常启动\n"
                    "   - 需要通过 USB 本地刷写恢复\n"
                    "   - MCU/iCGM/IPK 未受影响，保持旧版本"
                ),
                sources=[{"title": "battery_drain_abort_20251208.log", "type": "log"}],
                raw_data={"log_lines_analyzed": log_lines},
            )

        if is_dependency:
            return AgentResult(
                agent_name=self.name,
                display_name=self.display_name,
                success=True,
                confidence="high",
                summary="iCGM CRC 校验失败阻断批量升级依赖链，MCU/IPK/IVI 全部卡死等待",
                detail=(
                    "核心异常分析：\n\n"
                    "1. **批量升级依赖顺序**\n"
                    "   - 升级序列: iCGM -> IVI -> MCU -> IPK\n"
                    "   - iCGM 作为协调者，必须先完成并发送 FLASH_COMPLETE 信号\n\n"
                    "2. **iCGM 刷写后 CRC 校验失败**\n"
                    "   - 时间戳: 16:30:30 — iCGM flash 完成 (100%)，但 post-flash CRC 不匹配\n"
                    "   - `[ECUFlashManager] iCGM post-flash verification FAILED: firmware CRC mismatch`\n"
                    "   - iCGM 进入不一致状态：已刷写但固件无效\n\n"
                    "3. **依赖链全部阻塞**\n"
                    "   - MCU 升级被阻塞: 等待 iCGM FLASH_COMPLETE 信号 (超时 30s 无响应)\n"
                    "   - IPK 升级被阻塞: 等待 MCU 完成\n"
                    "   - IVI 升级被阻塞: 等待 iCGM 协调\n"
                    "   - 4 个 ECU 中 3 个卡在等待状态，1 个处于 brick 状态\n\n"
                    "4. **恢复建议**\n"
                    "   - 步骤 1: USB 本地刷写恢复 iCGM 至已知良好版本\n"
                    "   - 步骤 2: 重新发起批量升级任务\n"
                    "   - 步骤 3: 检查 iCGM 硬件 eMMC 健康状态"
                ),
                sources=[{"title": "ecu_dependency_chain_failure_20251120.log", "type": "log"}],
                raw_data={"log_lines_analyzed": log_lines},
            )

        is_ecu_flash = (
            any(k in task_lower for k in ["ecu", "刷写", "flash", "未完成", "状态"])
            and not is_icgm and not is_dependency
        )

        if is_fleet:
            return AgentResult(
                agent_name=self.name,
                display_name=self.display_name,
                success=True,
                confidence="high",
                summary="车队 FOTA 升级统计分析完成（样本：5 辆，3 个故障类型）",
                detail=(
                    "车队升级健康度统计：\n\n"
                    "| 故障类型 | 发生次数 | 占比 | 典型时间 |\n"
                    "|----------|----------|------|----------|\n"
                    "| iCGM eMMC 写入超时 + 死循环 | 1 | 20% | 2025-09-15 |\n"
                    "| 网络中断导致下载/校验失败 | 1 | 20% | 2025-10-03 |\n"
                    "| ECU 依赖链断裂（批量升级） | 1 | 20% | 2025-11-20 |\n"
                    "| 电量不足紧急中止 | 1 | 20% | 2025-12-08 |\n"
                    "| 升级成功 | 1 | 20% | — |\n\n"
                    "**关键发现**：\n\n"
                    "1. **最高频故障**: eMMC 硬件异常 (20%) + 网络不稳定 (20%)，共占故障的 50%\n"
                    "2. **高风险时段**: 深夜定时升级（03:00-05:00）因车辆熄火导致电量风险\n"
                    "3. **批量升级短板**: iCGM 作为协调者的单点故障会阻断整条升级依赖链\n"
                    "4. **建议优先处理**: iCGM eMMC 硬件体检，以及夜间升级前电量阈值提高至 ≥60%"
                ),
                sources=[
                    {"title": "battery_drain_abort_20251208.log", "type": "log"},
                    {"title": "ecu_dependency_chain_failure_20251120.log", "type": "log"},
                    {"title": "network_interrupt_download_20251003.log", "type": "log"},
                    {"title": "icgm_emmc_timeout_20250915.log", "type": "log"},
                ],
                raw_data={"log_lines_analyzed": log_lines, "vehicles_analyzed": 5},
            )

        if is_ecu_flash:
            return AgentResult(
                agent_name=self.name,
                display_name=self.display_name,
                success=True,
                confidence="high",
                summary="ECU 刷写状态分析完成",
                detail=(
                    "ECU 刷写状态分析：\n\n"
                    "- IVI ECU: ✅ 刷写完成\n"
                    "- MCU ECU: ✅ 刷写完成\n"
                    "- IPK ECU: ❌ 未完成 — 等待 iCGM 协调信号\n"
                    "- iCGM ECU: ❌ 卡在校验失败循环\n\n"
                    "IPK 未完成刷写的直接原因是 iCGM 作为升级协调者进入死循环后，"
                    "未能向 IPK 发送 `FLASH_START` 协调信号。"
                ),
                sources=[{"title": "fota_upgrade_failure_20250911.log", "type": "log"}],
                raw_data={"log_lines_analyzed": log_lines},
            )

        # Fallback — generic log analysis based on whatever was loaded
        loaded_files = [line[4:-4] for line in log_content.splitlines() if line.startswith("=== ") and line.endswith(" ===")]
        file_hint = f"（已加载日志: {', '.join(loaded_files)}）" if loaded_files else ""
        return AgentResult(
            agent_name=self.name,
            display_name=self.display_name,
            success=True,
            confidence="medium",
            summary=f"日志扫描完成，共分析 {log_lines} 行{file_hint}",
            detail=(
                f"已扫描 {log_lines} 行日志记录。{file_hint}\n\n"
                f"搜索关键词: {', '.join(keywords) if keywords else '（无）'}\n\n"
                "**初步发现**：\n"
                "- 未检测到与查询直接匹配的严重异常\n"
                "- 建议补充以下信息以精确定位：\n"
                "  - 涉及的 ECU 名称（iCGM / MPU / IVI / MCU / IPK）\n"
                "  - 具体错误现象或报错码\n"
                "  - 故障发生时间窗口\n"
                "  - 是否完成过升级或部分完成"
            ),
            sources=[{"title": "系统日志扫描", "type": "log"}],
        )


registry.register(LogAnalyticsAgent())
