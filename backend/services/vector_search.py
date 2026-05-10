"""
向量检索服务 — Jira 工单和文档的语义搜索

两种运行模式：
1. Baseline（无 LLM）：使用 TF-IDF + 余弦相似度做文本匹配
2. Embedding（需 LLM）：使用 OpenAI embedding 模型生成向量后检索，支持预计算索引持久化

作者：FOTA 诊断平台团队
创建时间：2026-04-06
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from pathlib import Path
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple

EMBED_INDEX_FORMAT_VERSION = 1
_EMBED_INDEX_CACHE: Dict[str, dict[str, Any]] = {}

logger = logging.getLogger(__name__)


class VectorSearchService:
    """
    向量检索服务

    在没有 embedding model 时，使用 TF-IDF baseline 提供基本的语义匹配能力。
    当 embedding API 可用后，切换到向量余弦相似度检索。
    """

    def __init__(self, use_embeddings: bool = False):
        """
        Args:
            use_embeddings: 是否使用 embedding 模型（需 API Key）
        """
        self.use_embeddings = use_embeddings
        self._idf_cache: Dict[str, float] = {}
        self._doc_vectors: List[Tuple[str, Dict[str, float], Dict[str, Any]]] = []
        # Embedding 模式存储：(text_preview, float_vector, metadata)
        self._embed_vectors: List[Tuple[str, List[float], Dict[str, Any]]] = []

    # ── 公共接口 ──

    async def index_documents(self, documents: List[Dict[str, Any]], text_field: str = "text") -> int:
        """
        索引文档集合

        Args:
            documents: 文档列表，每个文档需包含 text_field 字段
            text_field: 文本字段名

        Returns:
            索引的文档数量
        """
        if self.use_embeddings:
            return await self._index_with_embeddings(documents, text_field)
        return self._index_with_tfidf(documents, text_field)

    async def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.1,
    ) -> List[Dict[str, Any]]:
        """
        搜索相关文档

        Args:
            query: 查询文本
            top_k: 返回前 K 个结果
            min_score: 最低相似度阈值

        Returns:
            按相似度降序排列的搜索结果
        """
        if self.use_embeddings:
            return await self._search_with_embeddings(query, top_k, min_score)
        return self._search_with_tfidf(query, top_k, min_score)

    def search_jira_issues(
        self,
        query: str,
        tickets: List[Dict[str, Any]],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        搜索 Jira 工单

        Args:
            query: 查询文本
            tickets: 工单列表
            top_k: 返回前 K 个结果

        Returns:
            相关工单列表（含相似度分数）
        """
        # 将工单转为检索文档
        docs = []
        for t in tickets:
            text = f"{t.get('key', '')} {t.get('summary', '')} {t.get('description', '')} {t.get('resolution', '')}"
            docs.append({"text": text, "metadata": t})

        self._index_with_tfidf(docs, "text")
        results = self._search_with_tfidf(query, top_k, min_score=0.05)

        return [
            {**r["metadata"], "similarity_score": r["score"], "retrieval_mode": r.get("retrieval_mode", "tfidf")}
            for r in results
        ]

    def search_documents(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        搜索技术文档

        Args:
            query: 查询文本
            documents: 文档列表
            top_k: 返回前 K 个结果

        Returns:
            相关文档列表（含相似度分数）
        """
        docs = []
        for d in documents:
            text = f"{d.get('title', '')} {d.get('excerpt', '')} {d.get('content', '')}"
            docs.append({"text": text, "metadata": d})

        self._index_with_tfidf(docs, "text")
        results = self._search_with_tfidf(query, top_k, min_score=0.05)

        return [
            {**r["metadata"], "similarity_score": r["score"], "retrieval_mode": r.get("retrieval_mode", "tfidf")}
            for r in results
        ]

    # ── TF-IDF Baseline 实现 ──

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """中英文混合分词"""
        # 英文按单词分割，中文按字/词分割
        text = text.lower()
        # 英文单词
        en_tokens = re.findall(r'[a-z][a-z0-9_\-]*(?:\.[a-z0-9]+)*', text)
        # 中文字符序列（简单的 bigram）
        cn_chars = re.findall(r'[\u4e00-\u9fff]+', text)
        cn_tokens = []
        for segment in cn_chars:
            for i in range(len(segment) - 1):
                cn_tokens.append(segment[i:i+2])
            if len(segment) == 1:
                cn_tokens.append(segment)

        return en_tokens + cn_tokens

    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        """计算词频 (TF)"""
        counter = Counter(tokens)
        total = len(tokens) or 1
        return {term: count / total for term, count in counter.items()}

    def _compute_idf(self, doc_tokens_list: List[List[str]]) -> Dict[str, float]:
        """计算逆文档频率 (IDF)"""
        n_docs = len(doc_tokens_list)
        if n_docs == 0:
            return {}

        df: Dict[str, int] = {}
        for tokens in doc_tokens_list:
            seen = set(tokens)
            for term in seen:
                df[term] = df.get(term, 0) + 1

        return {
            term: math.log((n_docs + 1) / (freq + 1)) + 1
            for term, freq in df.items()
        }

    def _tfidf_vector(self, tf: Dict[str, float], idf: Dict[str, float]) -> Dict[str, float]:
        """计算 TF-IDF 向量"""
        return {term: tf_val * idf.get(term, 1.0) for term, tf_val in tf.items()}

    @staticmethod
    def _cosine_similarity(v1: Dict[str, float], v2: Dict[str, float]) -> float:
        """计算余弦相似度"""
        common_keys = set(v1.keys()) & set(v2.keys())
        if not common_keys:
            return 0.0

        dot_product = sum(v1[k] * v2[k] for k in common_keys)
        norm1 = math.sqrt(sum(v ** 2 for v in v1.values()))
        norm2 = math.sqrt(sum(v ** 2 for v in v2.values()))

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)

    def _index_with_tfidf(self, documents: List[Dict[str, Any]], text_field: str) -> int:
        """使用 TF-IDF 索引文档"""
        self._doc_vectors = []

        all_tokens = []
        for doc in documents:
            tokens = self._tokenize(doc.get(text_field, ""))
            all_tokens.append(tokens)

        self._idf_cache = self._compute_idf(all_tokens)

        for doc, tokens in zip(documents, all_tokens):
            tf = self._compute_tf(tokens)
            tfidf = self._tfidf_vector(tf, self._idf_cache)
            self._doc_vectors.append((doc.get(text_field, "")[:200], tfidf, doc.get("metadata", doc)))

        logger.debug("TF-IDF indexed %d documents", len(self._doc_vectors))
        return len(self._doc_vectors)

    def _search_with_tfidf(
        self, query: str, top_k: int, min_score: float
    ) -> List[Dict[str, Any]]:
        """使用 TF-IDF 搜索"""
        query_tokens = self._tokenize(query)
        query_tf = self._compute_tf(query_tokens)
        query_vec = self._tfidf_vector(query_tf, self._idf_cache)

        scored = []
        for text_preview, doc_vec, metadata in self._doc_vectors:
            score = self._cosine_similarity(query_vec, doc_vec)
            if score >= min_score:
                scored.append({
                    "score": round(score, 4),
                    "text_preview": text_preview,
                    "metadata": metadata,
                    "retrieval_mode": "tfidf",
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    # ── Embedding 实现 ──

    @staticmethod
    def _cosine_similarity_float(v1: List[float], v2: List[float]) -> float:
        """两个 float 向量的余弦相似度。"""
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    async def _index_with_embeddings(self, documents: List[Dict[str, Any]], text_field: str) -> int:
        """使用 OpenAI embedding 模型批量索引文档（并发请求）。"""
        from services.llm import get_embeddings

        self._embed_vectors = []
        texts = [doc.get(text_field, "")[:8000] for doc in documents]
        tasks = [get_embeddings(text) for text in texts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for doc, vec in zip(documents, results):
            if isinstance(vec, Exception):
                logger.warning("Embedding failed for doc '%s': %s", doc.get(text_field, "")[:60], vec)
                continue
            preview = doc.get(text_field, "")[:200]
            meta = doc.get("metadata", doc)
            self._embed_vectors.append((preview, vec, meta))

        logger.info("Embedding indexed %d/%d documents", len(self._embed_vectors), len(documents))
        return len(self._embed_vectors)

    async def _search_with_embeddings(
        self, query: str, top_k: int, min_score: float
    ) -> List[Dict[str, Any]]:
        """使用 embedding 相似度搜索；若索引为空则 fallback 到 TF-IDF。"""
        if not self._embed_vectors:
            logger.warning("Embedding index empty, falling back to TF-IDF")
            results = self._search_with_tfidf(query, top_k, min_score)
            for item in results:
                item["retrieval_mode"] = "tfidf_fallback"
            return results

        from services.llm import get_embeddings
        try:
            query_vec = await get_embeddings(query[:8000])
        except Exception as exc:
            logger.warning("Failed to embed query, falling back to TF-IDF: %s", exc)
            results = self._search_with_tfidf(query, top_k, min_score)
            for item in results:
                item["retrieval_mode"] = "tfidf_fallback"
            return results

        scored = []
        for text_preview, doc_vec, metadata in self._embed_vectors:
            score = self._cosine_similarity_float(query_vec, doc_vec)
            if score >= min_score:
                scored.append({
                    "score": round(score, 4),
                    "text_preview": text_preview,
                    "metadata": metadata,
                    "retrieval_mode": "embedding",
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    # ── Embedding 索引持久化 ──

    def save_embed_index(self, path: Path) -> int:
        """将内存中的 embedding 向量序列化到 JSON 文件，供下次启动直接加载。

        使用 .partial + os.replace() 原子写入，防止进程崩溃导致文件损坏。
        """
        import os
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": EMBED_INDEX_FORMAT_VERSION,
            "count": len(self._embed_vectors),
            "items": [
                {"preview": preview, "vector": vec, "metadata": meta}
                for preview, vec, meta in self._embed_vectors
            ],
        }
        partial_path = path.with_suffix(path.suffix + ".partial")
        partial_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(partial_path, path)
        logger.info("Saved %d embedding vectors to %s", len(self._embed_vectors), path)
        return len(self._embed_vectors)

    def load_embed_index(self, path: Path) -> int:
        """从 JSON 文件加载预计算的 embedding 向量。"""
        cache_key = str(path.resolve())
        if cache_key in _EMBED_INDEX_CACHE:
            cached = _EMBED_INDEX_CACHE[cache_key]
            self._embed_vectors = [
                (item["preview"], item["vector"], item["metadata"])
                for item in cached["items"]
            ]
            logger.info("Loaded %d embedding vectors from cache for %s", len(self._embed_vectors), path)
            return len(self._embed_vectors)
        if not path.exists():
            return 0
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            items = payload
        else:
            if payload.get("version") != EMBED_INDEX_FORMAT_VERSION:
                logger.warning("Unsupported embedding index version in %s", path)
                return 0
            items = payload.get("items", [])
        validated_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if "preview" not in item or "vector" not in item or "metadata" not in item:
                continue
            validated_items.append(item)
        self._embed_vectors = [
            (item["preview"], item["vector"], item["metadata"])
            for item in validated_items
        ]
        _EMBED_INDEX_CACHE[cache_key] = {"version": EMBED_INDEX_FORMAT_VERSION, "items": validated_items}
        logger.info("Loaded %d embedding vectors from %s", len(self._embed_vectors), path)
        return len(self._embed_vectors)

    # ── Async 高级接口（供 Agent 直接使用）──

    async def async_search_jira_issues(
        self,
        query: str,
        tickets: List[Dict[str, Any]],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Embedding 模式搜索 Jira 工单：先批量 embed 工单文本，再 embed 查询做相似度排序。
        结果格式与 search_jira_issues 相同。
        """
        docs = []
        for t in tickets:
            text = (
                f"{t.get('key', '')} {t.get('summary', '')} "
                f"{t.get('description', '')} {t.get('resolution', '')}"
            )
            docs.append({"text": text, "metadata": t})

        await self._index_with_embeddings(docs, "text")
        results = await self._search_with_embeddings(query, top_k, min_score=0.3)
        return [
            {**r["metadata"], "similarity_score": r["score"], "retrieval_mode": r.get("retrieval_mode", "embedding")}
            for r in results
        ]

    async def async_search_documents(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Embedding 模式搜索技术文档。
        结果格式与 search_documents 相同。
        """
        docs = []
        for d in documents:
            text = f"{d.get('title', '')} {d.get('excerpt', '')} {d.get('content', '')}"
            docs.append({"text": text, "metadata": d})

        await self._index_with_embeddings(docs, "text")
        results = await self._search_with_embeddings(query, top_k, min_score=0.3)
        return [
            {**r["metadata"], "similarity_score": r["score"], "retrieval_mode": r.get("retrieval_mode", "embedding")}
            for r in results
        ]


# 全局单例：默认由配置控制；运行时可复用同一服务实例
# 注意：embedding 模式是否开启由 Settings.AGENTS_USE_EMBEDDINGS 决定。
vector_service = VectorSearchService()
