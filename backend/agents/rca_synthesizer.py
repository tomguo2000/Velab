"""RCA Synthesizer Agent — synthesizes root cause analysis from multiple agent results."""

from __future__ import annotations

import logging
from typing import List

from agents.base import BaseAgent, AgentResult, registry
from common.chain_log import sync_step_timer
from config import settings

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
你是车载 FOTA 系统根因综合分析专家。你收到了来自多个专项分析 Agent 的分析结果（日志分析、历史工单、技术文档），\
需要综合所有证据给出权威的根因分析报告。

**必须**按以下 Markdown 格式输出，语言简洁专业：

## 🎯 诊断结论
（1-3 句话，说明根本原因、受影响的 ECU、故障链路）

## 📊 多路证据汇总
（从各 Agent 结论中提炼关键证据，用 • 分点列出，标明来源）

## 💡 修复建议
（优先级排序的可操作步骤，分为"立即处理"和"长期优化"两类）

## 📚 证据来源
（列出引用的工单 ID、文档名称、日志文件）

## 置信度
（仅输出 high / medium / low，综合所有 Agent 的置信度判断）
"""


class RCASynthesizerAgent(BaseAgent):
    name = "rca_synthesizer"
    display_name = "RCA Synthesizer"
    description = (
        "综合多个Agent的分析结果，生成最终的根因分析报告。"
        "汇总日志分析、历史案例、技术文档等多路证据，计算置信度，给出诊断结论和修复建议。"
    )

    async def execute(
        self, 
        task: str, 
        keywords: list[str] | None = None, 
        context: dict | None = None
    ) -> AgentResult:
        """
        Synthesize RCA from multiple agent results.
        
        Args:
            task: User's diagnostic query
            keywords: Extracted keywords
            context: Should contain 'agent_results' - list of AgentResult from other agents
        """
        with sync_step_timer(
            log,
            step="agent.rca_synthesizer",
            task_preview=task[:120],
        ):
            # Extract agent results from context
            agent_results: List[AgentResult] = []
            if context and "agent_results" in context:
                agent_results = context["agent_results"]
            
            if not agent_results:
                return AgentResult(
                    agent_name=self.name,
                    display_name=self.display_name,
                    success=False,
                    confidence="low",
                    summary="无法生成根因分析",
                    detail="没有收到其他Agent的分析结果，无法进行综合分析。",
                    sources=[],
                )
            
            # Read workspace notes for supplementary context
            workspace_notes = self._read_workspace_notes(context)

            # 优先使用 LLM 综合，失败则降级到规则综合
            if settings.AGENTS_USE_LLM:
                try:
                    return await self._llm_synthesize(task, agent_results, workspace_notes)
                except Exception as exc:
                    log.warning("RCA LLM synthesis failed, falling back to rule-based: %s", exc)

            # Fallback: rule-based synthesis
            return self._synthesize_results(task, agent_results, workspace_notes)
    
    async def _llm_synthesize(self, task: str, agent_results: List[AgentResult], workspace_notes: str) -> AgentResult:
        """Use LLM to generate authoritative RCA from all agent results."""
        from services.llm import chat_completion

        # 构建各 Agent 结论的摘要文本
        results_text_parts = []
        all_sources = []
        for r in agent_results:
            if r.success:
                results_text_parts.append(
                    f"### {r.display_name}\n"
                    f"**置信度**: {r.confidence}\n"
                    f"**摘要**: {r.summary}\n\n"
                    f"{(r.detail or '')[:1500]}"
                )
                all_sources.extend(r.sources or [])

        if not results_text_parts:
            raise ValueError("No successful agent results to synthesize")

        results_text = "\n\n---\n\n".join(results_text_parts)
        notes_section = f"\n\n**工作区补充记录**：\n{workspace_notes[:800]}" if workspace_notes else ""

        user_msg = (
            f"诊断任务：{task}\n\n"
            f"各专项 Agent 分析结果如下：\n\n{results_text}"
            f"{notes_section}\n\n"
            "请综合以上所有证据，给出完整的根因分析报告。"
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        response = await chat_completion(messages, model="synthesizer-model", temperature=0.3, max_tokens=2048)
        llm_text: str = getattr(response, "content", None) or ""
        if not llm_text.strip():
            raise ValueError("Empty LLM response")

        # 提取置信度
        confidence = self._calculate_confidence([r for r in agent_results if r.success])
        for line in llm_text.splitlines():
            stripped = line.strip().lower()
            if stripped in {"high", "medium", "low"}:
                confidence = stripped
                break

        # 提取 summary（诊断结论后第一个非空行）
        summary = f"综合分析完成 - 基于 {len([r for r in agent_results if r.success])} 个 Agent 的分析结果"
        in_conclusion = False
        for line in llm_text.splitlines():
            if "诊断结论" in line:
                in_conclusion = True
                continue
            if in_conclusion and line.strip() and not line.startswith("#"):
                summary = line.strip()[:120]
                break

        return AgentResult(
            agent_name=self.name,
            display_name=self.display_name,
            success=True,
            confidence=confidence,
            summary=summary,
            detail=llm_text.strip(),
            sources=all_sources,
            raw_data={
                "agent_count": len(agent_results),
                "successful_count": len([r for r in agent_results if r.success]),
                "llm": True,
            },
        )

    def _read_workspace_notes(self, context: dict | None) -> str:
        """读取工作区 notes.md 作为补充推理上下文（可选，降级安全）"""
        if not context or "workspace_path" not in context:
            return ""
        try:
            from pathlib import Path
            notes_path = Path(context["workspace_path"]) / "notes.md"
            if notes_path.exists():
                content = notes_path.read_text(encoding="utf-8")
                log.debug("Workspace notes loaded: %d chars", len(content))
                return content
        except Exception as e:
            log.warning("Failed to read workspace notes: %s", e)
        return ""
    
    def _synthesize_results(self, task: str, agent_results: List[AgentResult], workspace_notes: str = "") -> AgentResult:
        """Synthesize multiple agent results into a comprehensive RCA."""
        
        # Collect successful results
        successful_results = [r for r in agent_results if r.success]
        
        if not successful_results:
            return AgentResult(
                agent_name=self.name,
                display_name=self.display_name,
                success=False,
                confidence="low",
                summary="所有Agent分析均未成功",
                detail="各个分析Agent均未能找到相关信息或分析失败。建议：\n"
                       "1. 检查日志文件是否已上传\n"
                       "2. 提供更具体的故障描述\n"
                       "3. 补充ECU名称、错误码等关键信息",
                sources=[],
            )
        
        # Calculate overall confidence
        confidence = self._calculate_confidence(successful_results)
        
        # Build comprehensive analysis
        detail_parts = []
        all_sources = []
        
        # Add executive summary
        detail_parts.append("## 🎯 诊断结论\n")
        detail_parts.append(self._generate_executive_summary(successful_results))
        detail_parts.append("\n")
        
        # Add detailed analysis from each agent
        detail_parts.append("## 📊 详细分析\n")
        for result in successful_results:
            detail_parts.append(f"### {result.display_name}\n")
            detail_parts.append(f"**置信度**: {result.confidence}\n")
            detail_parts.append(f"**摘要**: {result.summary}\n\n")
            detail_parts.append(result.detail)
            detail_parts.append("\n\n")
            
            # Collect sources
            if result.sources:
                all_sources.extend(result.sources)
        
        # Add recommendations
        detail_parts.append("## 💡 修复建议\n")
        detail_parts.append(self._generate_recommendations(successful_results))
        
        # Validate citation references
        citation_warnings = self._validate_citations(all_sources, successful_results)
        
        # Add evidence references
        if all_sources:
            detail_parts.append("\n\n## 📚 证据来源\n")
            for idx, source in enumerate(all_sources, 1):
                source_type = source.get("type", "unknown")
                title = source.get("title", "未知来源")
                detail_parts.append(f"{idx}. [{source_type.upper()}] {title}\n")
        
        if citation_warnings:
            detail_parts.append("\n\n> ⚠️ **引用校验提示**\n")
            for w in citation_warnings:
                detail_parts.append(f"> - {w}\n")
        
        return AgentResult(
            agent_name=self.name,
            display_name=self.display_name,
            success=True,
            confidence=confidence,
            summary=f"综合分析完成 - 基于{len(successful_results)}个Agent的分析结果",
            detail="\n".join(detail_parts),
            sources=all_sources,
            raw_data={
                "agent_count": len(agent_results),
                "successful_count": len(successful_results),
                "confidence_scores": [r.confidence for r in successful_results],
                "citation_warnings": citation_warnings,
            }
        )
    
    def _calculate_confidence(self, results: List[AgentResult]) -> str:
        """Calculate overall confidence based on individual agent confidences."""
        if not results:
            return "low"
        
        # Map confidence levels to scores
        confidence_map = {"high": 3, "medium": 2, "low": 1}
        
        scores = [confidence_map.get(r.confidence, 1) for r in results]
        avg_score = sum(scores) / len(scores)
        
        # Convert back to confidence level
        if avg_score >= 2.5:
            return "high"
        elif avg_score >= 1.5:
            return "medium"
        else:
            return "low"
    
    def _generate_executive_summary(self, results: List[AgentResult]) -> str:
        """Generate executive summary from agent results."""
        summaries = []
        
        for result in results:
            if result.agent_name == "log_analytics":
                summaries.append(f"**日志分析**: {result.summary}")
            elif result.agent_name == "jira_knowledge":
                summaries.append(f"**历史案例**: {result.summary}")
            else:
                summaries.append(f"**{result.display_name}**: {result.summary}")
        
        if not summaries:
            return "未能生成诊断结论。"
        
        return "\n".join(summaries)
    
    def _generate_recommendations(self, results: List[AgentResult]) -> str:
        """Generate actionable recommendations based on analysis."""
        recommendations = []
        
        # Extract recommendations from agent results
        for result in results:
            detail_lower = result.detail.lower()
            
            # Look for common patterns in analysis
            if "校验失败" in result.detail or "verify" in detail_lower:
                recommendations.append(
                    "1. **升级包校验机制优化**\n"
                    "   - 增加校验失败重试上限(建议max=3)\n"
                    "   - 实现校验失败后的自动回退机制\n"
                    "   - 增强文件完整性检查(MD5/SHA256)"
                )
            
            if "死循环" in result.detail or "循环" in result.detail:
                recommendations.append(
                    "2. **状态机保护机制**\n"
                    "   - 为关键状态添加超时保护\n"
                    "   - 实现异常状态的自动退出机制\n"
                    "   - 增加状态转换日志记录"
                )
            
            if "ecu" in detail_lower or "刷写" in result.detail:
                recommendations.append(
                    "3. **ECU刷写流程优化**\n"
                    "   - 优化ECU刷写顺序和依赖关系\n"
                    "   - 增加独立的超时保护机制\n"
                    "   - 实现刷写失败的回退策略"
                )
        
        # Add generic recommendations if none found
        if not recommendations:
            recommendations.append(
                "1. 建议收集更多日志信息进行深入分析\n"
                "2. 检查系统配置和环境参数\n"
                "3. 参考历史类似案例的修复方案"
            )
        
        return "\n\n".join(recommendations)

    @staticmethod
    def _validate_citations(
        all_sources: list[dict],
        agent_results: list,
    ) -> list[str]:
        """
        引用 ID 断言验证

        检查综合报告中引用的 source 是否来自有效的 Agent 结果，
        以及是否存在孤立引用、重复引用或缺失必要字段。

        Args:
            all_sources: 合并后的所有引用来源
            agent_results: 各 Agent 的执行结果

        Returns:
            list[str]: 验证警告列表（空列表表示全部通过）
        """
        warnings = []

        # 1. 检查引用完整性 — 每个 source 必须有 title 和 type
        for idx, src in enumerate(all_sources, 1):
            if not src.get("title"):
                warnings.append(f"引用 #{idx} 缺少 title 字段")
            if not src.get("type"):
                warnings.append(f"引用 #{idx} 缺少 type 字段")

        # 2. 检查引用来源一致性 — source 必须来自某个 agent 的 result
        agent_source_titles = set()
        for ar in agent_results:
            for s in (ar.sources or []):
                agent_source_titles.add(s.get("title", ""))

        for idx, src in enumerate(all_sources, 1):
            title = src.get("title", "")
            if title and title not in agent_source_titles:
                warnings.append(
                    f"引用 #{idx}「{title}」未在任何 Agent 结果中找到对应来源"
                )

        # 3. 检查重复引用
        seen_titles = set()
        for idx, src in enumerate(all_sources, 1):
            title = src.get("title", "")
            if title in seen_titles:
                warnings.append(f"引用 #{idx}「{title}」重复出现")
            seen_titles.add(title)

        # 4. 检查无引用的 Agent — 某个 Agent 成功但无 source 引用
        for ar in agent_results:
            if ar.success and not ar.sources:
                warnings.append(
                    f"Agent「{ar.display_name}」分析成功但未提供引用来源"
                )

        return warnings


registry.register(RCASynthesizerAgent())
