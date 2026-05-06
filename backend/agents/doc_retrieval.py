"""Doc Retrieval Agent — 技术文档检索 Agent

从离线技术文档库中检索与故障相关的文档片段，
提供技术规范、流程说明、最佳实践等参考信息。

当前使用 TF-IDF baseline 进行文本匹配，
后续可切换为 embedding 向量检索。

作者：FOTA 诊断平台团队
创建时间：2026-04-06
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agents.base import BaseAgent, AgentResult, registry
from common.chain_log import sync_step_timer
from config import settings
from services.vector_search import vector_service

log = logging.getLogger(__name__)

# 文档数据目录
DOC_DIR = Path(__file__).resolve().parent.parent / "data" / "docs"
JIRA_DIR = Path(__file__).resolve().parent.parent / "data" / "jira_mock"

_SYSTEM_PROMPT = """\
你是 FOTA 技术文档专家。根据检索到的技术规范和文档片段，提炼与当前故障直接相关的技术要点。

**必须**按以下 Markdown 格式输出：

## 📖 技术规范要点
（与故障直接相关的规范条款或设计约定，分点列出）

## 🔍 文档支撑分析
（文档中的技术细节如何解释当前故障现象）

## ✅ 最佳实践建议
（文档中记录的最佳实践或已知解决方案）
"""


class DocRetrievalAgent(BaseAgent):
    name = "doc_retrieval"
    display_name = "Document Retrieval Agent"
    description = (
        "从技术文档库中检索与故障相关的文档片段，"
        "包括FOTA规范、刷写流程文档、故障处理手册等。"
        "适用于：需要参考技术规范、流程说明或最佳实践的场景。"
    )

    async def execute(
        self,
        task: str,
        keywords: list[str] | None = None,
        context: dict | None = None,
    ) -> AgentResult:
        with sync_step_timer(
            log,
            step="agent.doc_retrieval",
            task_preview=task[:120],
            keywords=(keywords or [])[:8],
        ):
            # 加载文档库
            documents = self._load_documents()

            if not documents:
                result = AgentResult(
                    agent_name=self.name,
                    display_name=self.display_name,
                    success=False,
                    confidence="low",
                    summary="文档库为空",
                    detail="未找到任何技术文档。请确认文档文件已放置在 data/docs/ 目录中。",
                    sources=[],
                )
                await self._write_workspace(context, result)
                return result

            # 使用向量检索服务搜索
            query = task
            if keywords:
                query = f"{task} {' '.join(keywords)}"

            results = vector_service.search_documents(query, documents, top_k=5)

            if not results:
                result = AgentResult(
                    agent_name=self.name,
                    display_name=self.display_name,
                    success=False,
                    confidence="low",
                    summary="未找到相关文档",
                    detail="在文档库中未检索到与查询相关的技术文档。",
                    sources=[],
                )
                await self._write_workspace(context, result)
                return result

            # 构建结果
            detail_parts = ["**检索到的技术文档片段：**\n"]
            sources = []

            for i, doc in enumerate(results, 1):
                title = doc.get("title", f"文档 {i}")
                excerpt = doc.get("excerpt", doc.get("content", ""))[:300]
                score = doc.get("similarity_score", 0)

                detail_parts.append(
                    f"### {i}. 《{title}》\n"
                    f"**相关度**: {score:.0%}\n\n"
                    f"{excerpt}\n"
                )
                sources.append({
                    "title": title,
                    "type": "document",
                    "url": "#",
                })

            result = AgentResult(
                agent_name=self.name,
                display_name=self.display_name,
                success=True,
                confidence="medium" if results[0].get("similarity_score", 0) > 0.3 else "low",
                summary=f"检索到 {len(results)} 份相关技术文档",
                detail="\n".join(detail_parts),
                sources=sources,
                raw_data={"matched_count": len(results)},
            )

            # LLM 总结层：将检索片段提炼为叙述性技术分析
            if settings.AGENTS_USE_LLM:
                try:
                    result = await self._llm_summarize(task, result)
                except Exception as exc:
                    log.warning("Doc LLM summarize failed, keeping retrieval result: %s", exc)

            await self._write_workspace(context, result)
            return result

    async def _llm_summarize(self, task: str, retrieval_result: AgentResult) -> AgentResult:
        """Use LLM to synthesize technical insights from retrieved document snippets."""
        from services.llm import chat_completion

        user_msg = (
            f"当前故障描述：{task}\n\n"
            f"检索到的技术文档片段：\n{retrieval_result.detail[:3000]}\n\n"
            "请根据上述文档片段，提炼与当前故障相关的技术要点。"
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        response = await chat_completion(messages, model="agent-model", temperature=0.3, max_tokens=1024)
        llm_text: str = getattr(response, "content", None) or ""
        if not llm_text.strip():
            return retrieval_result

        return AgentResult(
            agent_name=retrieval_result.agent_name,
            display_name=retrieval_result.display_name,
            success=True,
            confidence=retrieval_result.confidence,
            summary=retrieval_result.summary,
            detail=llm_text.strip(),
            sources=retrieval_result.sources,
            raw_data={**(retrieval_result.raw_data or {}), "llm": True},
        )

    async def _write_workspace(self, context: dict | None, result: AgentResult) -> None:
        """将文档检索结果写入 workspace (可选，降级安全)"""
        if not context or "workspace_path" not in context:
            return
        try:
            from services.tool_functions import append_workspace_notes, update_todo_status
            ws_path = context["workspace_path"]

            notes_content = f"**摘要**: {result.summary}\n**置信度**: {result.confidence}\n\n{result.detail or '无结果'}"
            await append_workspace_notes(ws_path, self.display_name, notes_content)

            await update_todo_status(ws_path, "技术文档匹配", completed=result.success)
        except Exception as e:
            log.warning("Workspace write failed in %s: %s", self.name, e)

    def _load_documents(self) -> list[dict]:
        """加载文档库"""
        docs = []

        # 从 docs 目录加载 JSON 文档索引
        doc_index = DOC_DIR / "index.json"
        if doc_index.exists():
            try:
                docs.extend(json.loads(doc_index.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, IOError):
                pass

        # 从 jira_mock 目录加载技术文档
        jira_docs = JIRA_DIR / "documents.json"
        if jira_docs.exists():
            try:
                docs.extend(json.loads(jira_docs.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, IOError):
                pass

        # 扫描 docs 目录中的文本文件，对大文档使用滑动窗口切块提升召回率
        if DOC_DIR.exists():
            from services.doc_chunker import DocumentChunker
            chunker = DocumentChunker(
                chunk_size=settings.DOC_CHUNK_SIZE,
                chunk_overlap=settings.DOC_CHUNK_OVERLAP,
            )
            for f in DOC_DIR.iterdir():
                if f.suffix in (".txt", ".md"):
                    try:
                        content = f.read_text(encoding="utf-8", errors="ignore")
                        if len(content) <= settings.DOC_CHUNK_INLINE_THRESHOLD:
                            # 短文档直接作为单条记录
                            docs.append({
                                "title": f.stem,
                                "content": content,
                                "excerpt": content[:300],
                            })
                        else:
                            # 大文档：滑动窗口切块，每块作为独立检索单元
                            chunks = chunker.chunk_text(
                                content,
                                title=f.stem,
                                doc_path=str(f),
                                strategy="sliding_window",
                            )
                            for chunk in chunks:
                                docs.append({
                                    "title": f"{f.stem} [chunk {chunk.chunk_index + 1}/{chunk.total_chunks}]",
                                    "content": chunk.content,
                                    "excerpt": chunk.content[:300],
                                })
                    except IOError:
                        pass

        # 内置文档（保底）
        if not docs:
            docs = _BUILTIN_DOCS

        return docs


_BUILTIN_DOCS = [
    {
        "title": "FOTA状态机流程及异常场景处理技术要点2023Q3.pdf",
        "excerpt": (
            "详细描述了 FOTA 升级状态机的各个状态转换、异常场景处理流程，"
            "包括下载失败回退、校验失败重试、刷写超时保护等机制。"
            "状态转换顺序：INIT → VERSION_CHECK → DOWNLOAD → VERIFY → INSTALL → REBOOT → COMPLETE。"
            "异常处理要求：每个阶段必须在超时时间内完成，否则自动回退。"
        ),
    },
    {
        "title": "集中式升级刷写流程异常链路复盘2023-09",
        "excerpt": (
            "复盘了 2023年9月期间多起集中式升级刷写异常案例，包括 iCGM 死循环、IPK 超时、"
            "MCU 状态不一致等问题的根因分析和修复方案。"
            "关键发现：iCGM 作为升级协调者缺少全局超时保护，导致下游 ECU 无限等待。"
        ),
    },
    {
        "title": "FOTA客户端下载管理器设计文档v3.2",
        "excerpt": (
            "HttpDownloadManager 的架构设计，包括断点续传、文件完整性校验(MD5/SHA256)、"
            "磁盘空间预检、并发下载控制等。关键参数：write_buffer_size=4MB, "
            "verify_timeout=120s, max_retry=3。"
        ),
    },
    {
        "title": "ECU 刷写顺序与依赖关系规范 v2.0",
        "excerpt": (
            "定义了车载各 ECU 的刷写顺序和依赖关系。iCGM 作为升级协调者优先刷写，"
            "MCU/IPK 依赖 iCGM 发出的 FLASH_START 信号。T-BOX 负责云端通信和状态上报。"
            "刷写顺序：iCGM → IVI → MCU → IPK。每个 ECU 支持独立回退。"
        ),
    },
    {
        "title": "FOTA 升级包校验规范",
        "excerpt": (
            "升级包完整性校验流程：1) 下载完成后检查文件大小 2) 计算 SHA-256 哈希 "
            "3) 与服务端签名比对 4) 校验通过后写入安装分区。"
            "校验失败处理：最多重试 3 次，超限后标记为 FAILED 并上报。"
        ),
    },
]


registry.register(DocRetrievalAgent())
