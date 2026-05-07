"""Orchestrator — uses LLM with function calling to route to agents."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import AsyncIterator

from agents.base import AgentResult, registry
from common.chain_log import async_step_timer, chain_debug
from config import settings, SCENARIO_AGENT_MAP
from services.llm import chat_completion, chat_completion_stream, parse_tool_calls
from services.workspace_manager import workspace_manager

log = logging.getLogger(__name__)

DEFAULT_CLARIFICATION_REPLY = """您好！我是车辆 FOTA 诊断助手。

为便于分析，请尽量补充：车型/年款、涉及 ECU 或模块、具体现象或报错码、出现问题的时间点、最近一次 OTA 是否成功等。描述越具体，诊断越准确。"""

# Clarification replies must use these markers so internal reasoning stays in Thinking UI, not the chat body.
MARK_THINKING = "<<<THINKING>>>"
MARK_USER = "<<<USER>>>"

_INTERNAL_LINE_HINTS = (
    "根据我的规则",
    "根据规则",
    "何时不要调用",
    "何时必须调用",
    "所以我应该",
    "不要调用任何 Agent",
    "不要发起任何 function",
    "function call",
    "这是一个寒暄",
    "这是一个问候",
    "用户只是",
    "用户输入的是",
    "我需要：",
    "不要使用 <<<",
    "不要省略标记",
    "标记，因为",
    "先友好回复",
    "简短介绍我能",
    "列出 2～4",
    "邀请用户补充",
    # 模型在 USER 段前写的「写作说明 / 草稿标题」，不应展示
    "用户可见正文",
    "让我来写",
    "让我写一个",
    "简洁的回复",
    "列出典型问题引导",
    "用户寒暄问候",
    "无诊断信息，列出",
    "下面写",
    "回复如下",
    "草稿",
)

# Step 1 Thinking 区仅展示简短编排摘要；模型泄露规则复述时用固定文案替代
_THINKING_UI_FALLBACK = (
    "已识别为寒暄或信息不足，未调用诊断 Agent；正在引导用户补充可诊断信息（现象、ECU、错误码或日志）。"
)



def _thinking_leaks_detected(text: str) -> bool:
    """是否像模型自我分析/规则复述，不宜放在 Thinking UI。"""
    if not text or len(text) > 600:
        return True
    if any(h in text for h in _INTERNAL_LINE_HINTS):
        return True
    if re.search(r"^\s*\d+\.\s*先", text, re.MULTILINE):
        return True
    if text.count("\n") > 12:
        return True
    return False


def _thinking_for_step_ui(candidate: str) -> str:
    """Parallel Orchestrator 的 Step 1 result：短、可读、无规则复述。"""
    t = candidate.strip()
    if not t:
        return _THINKING_UI_FALLBACK
    if _thinking_leaks_detected(t):
        return _THINKING_UI_FALLBACK
    if len(t) > 240:
        return t[:240].rstrip() + "…"
    return t


def _raw_orchestrator_content(llm_message) -> str:
    """Full model text (before splitting thinking vs user)."""
    if llm_message is None:
        return ""
    text = (getattr(llm_message, "content", None) or "").strip()
    if not text:
        return ""
    return text


def _is_meta_user_line(line: str) -> bool:
    """单行是否为「写给开发者看的」元说明，而非用户正文。"""
    s = line.strip()
    if not s:
        return False
    if any(h in s for h in _INTERNAL_LINE_HINTS):
        return True
    # 短括号标题：（用户可见正文）
    if re.match(r"^[（(].{0,30}[)）]\s*$", s) and ("正文" in s or "用户" in s or "可见" in s):
        return True
    if re.match(r"^[（(]\s*用户可见", s):
        return True
    return False


def _strip_user_meta_preamble(text: str) -> str:
    """去掉 USER 段开头的元话语、空行，直到出现真实正文行。"""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s == "":
            i += 1
            continue
        if _is_meta_user_line(lines[i]):
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip()


def _strip_model_artifacts(text: str) -> str:
    """Remove delimiter lines if model echoed labels."""
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s in (MARK_THINKING, MARK_USER, MARK_THINKING.strip("<>"), MARK_USER.strip("<>")):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _split_clarification_output(raw: str) -> tuple[str, str]:
    """
    Returns (thinking_for_step_result, user_visible_markdown).
    Prefer explicit markers; else heuristic so self-analysis does not appear in the main bubble.
    """
    raw = raw.strip()
    if not raw:
        return "", ""

    if MARK_USER in raw:
        before, after = raw.split(MARK_USER, 1)
        thinking = before.strip()
        if thinking.startswith(MARK_THINKING):
            thinking = thinking[len(MARK_THINKING) :].strip()
        user = _strip_user_meta_preamble(_strip_model_artifacts(after))
        if user:
            thinking_ui = _thinking_for_step_ui(thinking) if thinking else _THINKING_UI_FALLBACK
            return (thinking_ui, user)

    user_heuristic = _heuristic_user_visible_only(raw)
    internal_guess = _heuristic_internal_only(raw, user_heuristic)
    thinking_ui = _thinking_for_step_ui(internal_guess)
    return (thinking_ui, user_heuristic)


def _heuristic_user_visible_only(text: str) -> str:
    """Try to keep only the greeting/help block for the main chat area."""
    t = text.strip()
    # Start from first line that looks like user-facing assistant reply
    m = re.search(
        r"(?m)^\s*(晚上好|下午好|早上好|中午好|您好|你好|谢谢|哈喽|嗨|Hi[，,\s]|Hello[，,\s])",
        t,
    )
    if m:
        return _strip_user_meta_preamble(t[m.start() :].strip())

    paragraphs = re.split(r"\n\s*\n+", t)
    kept: list[str] = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if any(h in p for h in _INTERNAL_LINE_HINTS):
            continue
        if "标记" in p and ("不要" in p or "使用" in p):
            continue
        if p.startswith("<<<"):
            continue
        if re.match(r"^\d+\.\s+", p) and "Agent" in p and "—" in p:
            kept.append(p)
            continue
        kept.append(p)

    out = _strip_user_meta_preamble("\n\n".join(kept).strip())
    if out:
        return out
    return _strip_user_meta_preamble(t) or t


def _heuristic_internal_only(full: str, user_kept: str) -> str:
    """Short summary for Thinking panel when model ignored markers."""
    if MARK_USER in full or MARK_THINKING in full:
        return ""
    full_s = full.strip()
    if user_kept and user_kept != full_s:
        idx = full_s.find(user_kept)
        if idx > 20:
            prefix = full_s[:idx].strip()
            if len(prefix) > 20:
                return prefix[:2000] + ("…" if len(prefix) > 2000 else "")
    return "当前信息不足以启动诊断 Agent；模型未按分区格式输出，已尽量隐藏内部推理，仅展示面向用户的段落。"


async def _yield_clarification_stream(
    orchestrator_text: str,
) -> AsyncIterator[dict]:
    """Skip Agent Interface + response generator; stream a single user-facing reply."""
    yield {"type": "content_start"}
    yield {"type": "content_delta", "content": orchestrator_text}
    yield {
        "type": "content_complete",
        "sources": [],
        "confidenceLevel": "—",
    }
    yield {"type": "done"}


SYSTEM_PROMPT_TEMPLATE = """你是一个车辆 FOTA（空中固件升级）诊断系统的智能编排器。

你的职责：
1. 分析用户的问题，理解他们遇到的车辆故障或需要的技术支持
2. 在信息足够时，从可用的诊断 Agent 中选择合适的 Agent，并编写具体的分析任务与关键词
3. 在信息不足时，通过多轮对话澄清：友好回复并引导用户补充现象、ECU、错误码、时间、操作步骤等

当前可用的诊断 Agent：
{agent_descriptions}

规则（重要）：
- **何时不要调用 Agent**：用户只是寒暄/问候、闲聊、或尚未提供可诊断的实质信息（无具体现象、无报错、无涉及模块/ECU、无日志或场景描述）时，**不要**发起任何 function call，直接用自然、简洁的中文回复用户，并列出 2～4 条你可以分析的典型问题类型，邀请对方补充。
- **何时必须调用 Agent**：用户已描述可分析的 FOTA/刷写/日志/工单类问题时，应使用 function call；可同时调用多个 Agent（并行）。
- 为每个 Agent 写清楚 task、并从问题中提取 keywords（ECU 名称如 iCGM, MPU, MCU, IPK, IVI, T-BOX、错误码、时间戳等）。
- 若问题明显超出车辆诊断范围，可先简短说明边界；仍希望给出参考时，可调用一个 Agent，并在 task 中写明「用户问题可能超出系统范围，请尝试提供相关建议」。
- 在对话历史中若用户已在前几轮补充了关键信息，应结合历史判断是否可以开始调用 Agent。

**当你不调用任何 Agent、需要直接回复用户时（寒暄、信息不足、引导补充），必须严格使用下面格式（不要省略两行标记）：**

<<<THINKING>>>
（**不超过 60 字**的一句话：仅说明「用户意图类型 + 未调 Agent 的原因」，例如：「用户寒暄，无诊断信息，引导补充现象与 ECU。」**禁止**写「根据规则」「何时不要调用」「所以我应该」、禁止复述本提示中的条款、禁止讨论 <<<THINKING>>>/<<<USER>>> 标记本身。）
<<<USER>>>
（直接写对用户说的话：自然中文；可列表与适量 emoji。**禁止**出现「（用户可见正文）」「让我写一个简洁的回复」「用户寒暄问候…列出典型问题引导」等写作说明或草稿标题；从第一句问候/正文开始写。）

说明：界面上 **Thinking 仅展示 THINKING 这一短句**；**USER 为对话正文**。若违反格式，系统会丢弃冗长推理。"""


async def orchestrate(
    user_message: str,
    scenario_id: str,
    conversation_history: list[dict] | None = None,
    bundle_id: str | None = None,
) -> AsyncIterator[dict]:
    """
    Main orchestration flow. Yields SSE events as dicts:
      {"type": "step_start"|"step_progress"|"step_complete"|"content_start"|"content_delta"|"content_complete"|"done", ...}
    
    Args:
        bundle_id: 可选的日志包 ID。若传入则 LogAnalyticsAgent 会优先分析该 bundle 的真实日志。
                   如果未传入，则尝试从 conversation_history 中自动提取最近一条 upload_summary。
    """
    t_pipeline = time.perf_counter()
    chain_debug(
        log,
        step="orchestrate",
        event="START",
        scenario_id=scenario_id,
        user_len=len(user_message),
        history_turns=len(conversation_history or []),
    )

    # 若调用方未传入 bundle_id，则扫描对话历史自动提取最近一条 upload_summary 消息
    if not bundle_id and conversation_history:
        import re as _re
        _UUID_RE = _re.compile(
            r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$",
            _re.IGNORECASE,
        )
        for msg in reversed(conversation_history):
            if isinstance(msg, dict) and msg.get("role") == "system":
                content = msg.get("content")
                if isinstance(content, dict) and content.get("systemKind") == "upload_summary":
                    candidate = content.get("bundleId")
                    if isinstance(candidate, str) and _UUID_RE.match(candidate):
                        bundle_id = candidate
                        chain_debug(log, step="orchestrate", event="BUNDLE_FROM_HISTORY", bundle_id=bundle_id)
                        break

    # Determine which agents are available for this scenario
    agent_names = SCENARIO_AGENT_MAP.get(scenario_id, ["log_analytics"])
    tools_schema = registry.get_tools_schema(agent_names)
    agent_descriptions = registry.get_agent_descriptions(agent_names)

    # Create workspace for this diagnostic task (graceful degradation)
    task_id = f"diag-{uuid.uuid4().hex[:12]}"
    ws_ctx = None
    workspace_path = None
    if settings.WORKSPACE_ENABLED:
        ws_ctx = workspace_manager.create(
            task_id=task_id,
            user_query=user_message,
            scenario_id=scenario_id,
        )
        if ws_ctx:
            workspace_path = str(ws_ctx.workspace_dir)
            chain_debug(log, step="orchestrate.workspace", event="CREATED", task_id=task_id)

    # Step 1: Orchestrator decides which agents to call via function calling
    yield {
        "type": "step_start",
        "step": {
            "stepNumber": 1,
            "agentName": "Parallel Orchestrator",
            "status": "running",
            "statusText": "Parallely Orchestrating...",
        },
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(agent_descriptions=agent_descriptions)},
    ]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    tool_calls: list[dict]
    llm_response = None
    try:
        llm_response = await chat_completion(
            messages,
            tools=tools_schema,
            stream=settings.ORCHESTRATOR_STREAM,
            model="router-model",
        )
        tool_calls = parse_tool_calls(llm_response)
        chain_debug(
            log,
            step="orchestrate.router",
            event="PARSED",
            tool_call_count=len(tool_calls),
            agent_names_allowed=agent_names,
        )
    except Exception as e:
        chain_debug(
            log,
            step="orchestrate.router",
            event="FALLBACK_NO_LLM",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
        log.exception("[Orchestrator] LLM unavailable, using fallback routing")
        tool_calls = [
            {"id": f"fallback_{name}", "name": name, "arguments": {"task": user_message, "keywords": []}}
            for name in agent_names
            if registry.get(name) is not None
        ]
        llm_response = None

    # No tool calls: clarification / greeting / insufficient detail — do not run agents or template final response
    if not tool_calls:
        chain_debug(log, step="orchestrate.branch", event="CLARIFICATION_ONLY")
        raw = _raw_orchestrator_content(llm_response)
        thinking_text, user_text = _split_clarification_output(raw)
        orchestrator_text = user_text or DEFAULT_CLARIFICATION_REPLY
        step_result = thinking_text if thinking_text else _THINKING_UI_FALLBACK
        yield {
            "type": "step_complete",
            "step": {
                "stepNumber": 1,
                "agentName": "Parallel Orchestrator",
                "status": "completed",
                "statusText": "Parallely Orchestrating...",
                "result": step_result,
            },
        }
        async for ev in _yield_clarification_stream(orchestrator_text):
            yield ev
        chain_debug(
            log,
            step="orchestrate",
            event="DONE",
            path="clarification",
            elapsed_ms=(time.perf_counter() - t_pipeline) * 1000,
        )
        return

    else:
        chain_debug(
            log,
            step="orchestrate.branch",
            event="AGENTS_AND_RESPONSE",
            planned_tools=len(tool_calls),
        )
        # Show orchestrator plan
        plan_lines = []
        for tc in tool_calls:
            agent = registry.get(tc["name"])
            display = agent.display_name if agent else tc["name"]
            task_preview = tc["arguments"].get("task", "")[:100]
            plan_lines.append(f"{display}: {task_preview}")

        yield {
            "type": "step_complete",
            "step": {
                "stepNumber": 1,
                "agentName": "Parallel Orchestrator",
                "status": "completed",
                "statusText": "Parallely Orchestrating...",
                "result": "\n".join(plan_lines),
            },
        }

        # Step 2+: Execute agents in parallel
        agent_tasks = []
        step_offset = 2
        for i, tc in enumerate(tool_calls):
            agent = registry.get(tc["name"])
            if agent is None:
                continue
            step_num = step_offset + i
            agent_tasks.append((step_num, agent, tc["arguments"]))

        # Emit step_start for each agent
        for step_num, agent, _ in agent_tasks:
            yield {
                "type": "step_start",
                "step": {
                    "stepNumber": step_num,
                    "agentName": agent.display_name,
                    "status": "running",
                    "statusText": _get_agent_status_text(agent.name),
                },
            }

        # No valid agents resolved (e.g. hallucinated tool names) — Step 1 already completed with plan
        if not agent_tasks:
            chain_debug(log, step="orchestrate.agents", event="SKIPPED_NO_VALID_AGENT")
            async for ev in _yield_clarification_stream(
                "系统未能识别编排器返回的诊断步骤。请用更具体的故障现象、ECU 名称或错误码重新描述，或稍后重试。"
            ):
                yield ev
            chain_debug(
                log,
                step="orchestrate",
                event="DONE",
                path="invalid_tools",
                elapsed_ms=(time.perf_counter() - t_pipeline) * 1000,
            )
            return

        # Execute all agents concurrently
        async def _run_agent(agent, args):
            # Inject workspace_path, bundle_id and time_hint into agent context
            agent_context: dict = {}
            if workspace_path:
                agent_context["workspace_path"] = workspace_path
            if bundle_id:
                agent_context["bundle_id"] = bundle_id
            raw_time_hint = args.get("time_hint")
            if isinstance(raw_time_hint, str) and raw_time_hint.strip():
                agent_context["time_hint"] = raw_time_hint.strip()
            return await agent.execute(
                task=args.get("task", ""),
                keywords=args.get("keywords"),
                context=agent_context if agent_context else None,
            )

        async with async_step_timer(
            log,
            step="orchestrate.agents_parallel",
            parallel_count=len(agent_tasks),
            names=[a.name for _, a, _ in agent_tasks],
        ):
            results = await asyncio.gather(
                *[_run_agent(agent, args) for _, agent, args in agent_tasks],
                return_exceptions=True,
            )

        agent_results: list[AgentResult] = []
        for (step_num, agent, _), result in zip(agent_tasks, results):
            if isinstance(result, Exception):
                ar = AgentResult(
                    agent_name=agent.name,
                    display_name=agent.display_name,
                    success=False,
                    confidence="low",
                    summary=f"Agent 执行出错: {result}",
                )
            else:
                ar = result
            agent_results.append(ar)

            # Emit workspace_update SSE events (if workspace was created)
            if workspace_path:
                for ws_event in _build_workspace_sse_events(
                    workspace_path=workspace_path,
                    agent_display_name=agent.display_name,
                ):
                    yield ws_event

            yield {
                "type": "step_complete",
                "step": {
                    "stepNumber": step_num,
                    "agentName": agent.display_name,
                    "status": "completed",
                    "statusText": _get_agent_status_text(agent.name),
                    "result": ar.detail if ar.detail else ar.summary,
                },
            }

        # Step N: RCA Synthesizer (仅当场景明确包含 rca_synthesizer 时运行)
        if len(agent_results) > 0 and "rca_synthesizer" in agent_names:
            synthesizer_step_num = 2 + len(tool_calls)
            synthesizer = registry.get("rca_synthesizer")
            
            if synthesizer:
                yield {
                    "type": "step_start",
                    "step": {
                        "stepNumber": synthesizer_step_num,
                        "agentName": synthesizer.display_name,
                        "status": "running",
                        "statusText": "Synthesizing Root Cause Analysis...",
                    },
                }
                
                try:
                    # Build context with agent_results + workspace_path
                    rca_context = {"agent_results": agent_results}
                    if workspace_path:
                        rca_context["workspace_path"] = workspace_path
                    
                    synthesizer_result = await synthesizer.execute(
                        task=user_message,
                        keywords=None,
                        context=rca_context,
                    )
                    
                    # Add synthesizer result to agent_results
                    agent_results.append(synthesizer_result)
                    
                    yield {
                        "type": "step_complete",
                        "step": {
                            "stepNumber": synthesizer_step_num,
                            "agentName": synthesizer.display_name,
                            "status": "completed",
                            "statusText": "Synthesizing Root Cause Analysis...",
                            "result": synthesizer_result.detail if synthesizer_result.detail else synthesizer_result.summary,
                        },
                    }
                except Exception as e:
                    log.exception("[Orchestrator] RCA Synthesizer failed")
                    yield {
                        "type": "step_complete",
                        "step": {
                            "stepNumber": synthesizer_step_num,
                            "agentName": synthesizer.display_name,
                            "status": "completed",
                            "statusText": "Synthesizing Root Cause Analysis...",
                            "result": f"综合分析失败: {str(e)}",
                        },
                    }

    # Final step: Response Generator
    final_step_num = 2 + len(tool_calls) + (1 if len(agent_results) > 0 and "rca_synthesizer" in agent_names else 0) if tool_calls else 2
    yield {
        "type": "step_start",
        "step": {
            "stepNumber": final_step_num,
            "agentName": "Agent Interface",
            "status": "running",
            "statusText": "Generating the Final Response...",
        },
    }

    # Generate structured response
    yield {
        "type": "step_complete",
        "step": {
            "stepNumber": final_step_num,
            "agentName": "Agent Interface",
            "status": "completed",
            "statusText": "Generating the Final Response...",
        },
    }

    # Stream the final response
    yield {"type": "content_start"}

    all_sources = []
    for ar in agent_results:
        all_sources.extend(ar.sources)

    chain_debug(
        log,
        step="orchestrate.final_response",
        event="STREAM_BEGIN",
        agent_results=len(agent_results),
    )
    async with async_step_timer(log, step="orchestrate.final_response", msg="generate_final_response"):
        async for chunk in generate_final_response(user_message, agent_results):
            yield chunk

    confidence = "高" if any(r.confidence == "high" for r in agent_results) else "中" if agent_results else "低"
    yield {
        "type": "content_complete",
        "sources": all_sources,
        "confidenceLevel": confidence,
    }
    yield {"type": "done"}

    # Cleanup workspace
    if ws_ctx:
        workspace_manager.cleanup(task_id, archive=False)
        chain_debug(log, step="orchestrate.workspace", event="CLEANED", task_id=task_id)

    chain_debug(
        log,
        step="orchestrate",
        event="DONE",
        path="full_pipeline",
        elapsed_ms=(time.perf_counter() - t_pipeline) * 1000,
    )


async def generate_final_response(
    user_message: str,
    agent_results: list[AgentResult],
) -> AsyncIterator[dict]:
    """Generate the final structured response using LLM or templates."""
    chain_debug(
        log,
        step="response_generator",
        event="START",
        agents=len(agent_results),
        user_len=len(user_message),
    )

    # Build context from agent results
    agent_context = ""
    for ar in agent_results:
        agent_context += f"\n### {ar.display_name} 分析结果 (置信度: {ar.confidence})\n"
        agent_context += f"{ar.detail or ar.summary}\n"

    source_list = []
    for ar in agent_results:
        for s in ar.sources:
            source_list.append(f"- {s['title']} ({s['type']})")
    source_text = "\n".join(source_list) if source_list else "无"

    messages = [
        {
            "role": "system",
            "content": RESPONSE_GENERATOR_PROMPT,
        },
        {
            "role": "user",
            "content": f"用户问题: {user_message}\n\n各 Agent 分析结果:\n{agent_context}\n\n引用来源:\n{source_text}",
        },
    ]


    try:
        accumulated = ""
        async for delta in chat_completion_stream(messages, max_tokens=4096):
            accumulated += delta
            yield {"type": "content_delta", "content": delta}
        chain_debug(
            log,
            step="response_generator",
            event="LLM_STREAM_OK",
            out_chars=len(accumulated),
        )
    except Exception as e:
        chain_debug(
            log,
            step="response_generator",
            event="LLM_FALLBACK_TEMPLATE",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
        log.exception("[ResponseGenerator] LLM unavailable, using template fallback")
        content = _build_fallback_response(user_message, agent_results)
        # Stream the fallback content by lines to preserve formatting
        lines = content.split('\n')
        for line in lines:
            if line.strip():  # Only send non-empty lines
                yield {"type": "content_delta", "content": line + '\n'}
                import asyncio as _aio
                await _aio.sleep(0.01)
            else:
                # Send empty lines to preserve paragraph breaks
                yield {"type": "content_delta", "content": '\n'}


RESPONSE_GENERATOR_PROMPT = """你是一个车辆 FOTA 诊断助手（角色: Technician）。
根据各诊断 Agent 的分析结果，生成结构化的最终回复。

回复必须严格按照以下格式：

## 信息分析

主要来源：[列出数据来源]
置信度：[高/中/低]

---

## 技术解答

### 关键发现

[核心结论，1-2句话]

### 具体过程

[详细的技术分析，包含时间线、错误码、因果关系]

---

## ⚠️ 安全提示

[与车辆安全相关的注意事项]

---

## 建议措施

[编号列表，包含具体的修复/处理建议]

---

规则：
- 用中文回复
- 保留所有技术术语（ECU名称、函数名、错误码等）用英文
- 引用 Agent 分析中的具体数据（时间戳、字节数、状态码等）
- 如果 Agent 未找到相关信息（置信度低），坦诚告知用户，并给出细化问题的建议
- 不要编造 Agent 未提供的数据"""


def _get_agent_status_text(agent_name: str) -> str:
    status_map = {
        "log_analytics": "Reading the logs and analyzing...",
        "jira_knowledge": "Retrieved existing relevant Jira tickets and documents...",
        "vehicle_timing": "Analyzing vehicle timing messages...",
        "mobile_app_logs": "Parsing mobile app logs...",
    }
    return status_map.get(agent_name, "Processing...")


def _build_fallback_response(user_message: str, agent_results: list[AgentResult]) -> str:
    """Build a structured response without LLM when it is unavailable."""
    if not agent_results:
        return (
            "暂时无法基于诊断 Agent 的数据生成正式报告（当前无有效分析结果或服务异常）。\n\n"
            "请补充具体现象、报错信息或日志线索后再试；若问题持续，请稍后重试。"
        )

    sources = []
    for ar in agent_results:
        for s in ar.sources:
            sources.append(s["title"])
    source_text = "、".join(sources) if sources else "系统日志"

    has_high = any(r.confidence == "high" for r in agent_results)
    confidence = "高" if has_high else "中" if agent_results else "低"

    parts = [f"## 信息分析\n\n主要来源：{source_text}\n置信度：{confidence}\n\n---\n\n## 技术解答\n"]

    for ar in agent_results:
        if ar.detail:
            parts.append(f"### {ar.display_name} 分析结果\n\n{ar.detail}\n")
        elif ar.summary:
            parts.append(f"### {ar.display_name}\n\n{ar.summary}\n")

    parts.append("\n---\n\n## ⚠️ 安全提示\n\n- 在故障完全排除前，请勿再次触发 OTA 升级\n- 建议由专业技术人员通过诊断工具确认 ECU 固件版本一致性\n")
    parts.append("\n---\n\n## 建议措施\n\n1. 通过诊断工具检查各 ECU 当前固件版本和状态\n2. 如有异常，联系技术支持获取修复方案\n3. 参考引用文档中的最佳实践进行处理\n")

    if sources:
        parts.append("\n---\n\n**引用来源：**\n")
        for s in sources:
            parts.append(f"- [{s}](#)")

    return "\n".join(parts)


def _build_workspace_sse_events(
    workspace_path: str,
    agent_display_name: str,
) -> list[dict]:
    """
    读取 workspace 文件，为前端生成 workspace_update SSE 事件列表。

    解析策略：
    - todo.md: 提取 [x] 和 [ ] 行，生成 checklist 事件
    - notes.md: 提取该 Agent section 的前几行作为摘要事件

    所有 I/O 异常均静默处理（降级）。

    Args:
        workspace_path: 工作区根目录路径
        agent_display_name: Agent 的显示名称（用于 notes.md section 匹配）

    Returns:
        list[dict]: SSE 事件列表，每项为 {"type": "workspace_update", ...}
    """
    from pathlib import Path
    events: list[dict] = []

    try:
        ws_dir = Path(workspace_path)

        # ── todo.md: emit completed checklist items ──
        todo_path = ws_dir / "todo.md"
        if todo_path.exists():
            for line in todo_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("- [x]") or stripped.startswith("- [ ]"):
                    # 只发射已完成的项目（进度感更强）
                    if stripped.startswith("- [x]"):
                        change = stripped[2:]  # "[x] 日志阶段验证"
                        events.append({
                            "type": "workspace_update",
                            "file": "todo.md",
                            "agent": agent_display_name,
                            "change": change,
                        })

        # ── notes.md: emit 该 Agent section 的摘要行 ──
        notes_path = ws_dir / "notes.md"
        if notes_path.exists():
            content = notes_path.read_text(encoding="utf-8")
            section_marker = f"## {agent_display_name}"
            if section_marker in content:
                # 提取 section 内容（到下一个 ## 或文件末尾）
                start = content.index(section_marker) + len(section_marker)
                rest = content[start:]
                next_section = rest.find("\n## ")
                section_body = rest[:next_section] if next_section >= 0 else rest

                # 提取摘要行（**摘要**: ...）
                for line in section_body.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("**摘要**:") or stripped.startswith("**Summary**:"):
                        snippet = stripped.split(":", 1)[-1].strip()[:80]
                        if snippet:
                            events.append({
                                "type": "workspace_update",
                                "file": "notes.md",
                                "agent": agent_display_name,
                                "change": snippet,
                            })
                        break

    except Exception as e:
        log.debug("_build_workspace_sse_events failed (non-critical): %s", e)

    return events
