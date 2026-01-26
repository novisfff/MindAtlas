from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RerankConfig:
    model: str
    base_url: str
    api_key: str | None
    timeout_sec: float
    request_format: str  # "standard" | "aliyun"
    enable_chunking: bool
    max_tokens_per_doc: int


def _chunk_documents_for_rerank(
    documents: list[str],
    *,
    max_tokens: int,
    overlap_tokens: int = 32,
    tokenizer_model: str = "gpt-4o-mini",
) -> tuple[list[str], list[int]]:
    if overlap_tokens >= max_tokens:
        overlap_tokens = max(0, max_tokens - 1)

    try:
        from lightrag.utils import TiktokenTokenizer

        tokenizer = TiktokenTokenizer(model_name=tokenizer_model)
        chunked_docs: list[str] = []
        doc_indices: list[int] = []
        for idx, doc in enumerate(documents):
            tokens = tokenizer.encode(doc)
            if len(tokens) <= max_tokens:
                chunked_docs.append(doc)
                doc_indices.append(idx)
                continue
            start = 0
            while start < len(tokens):
                end = min(start + max_tokens, len(tokens))
                chunk_text = tokenizer.decode(tokens[start:end])
                chunked_docs.append(chunk_text)
                doc_indices.append(idx)
                if end >= len(tokens):
                    break
                start = end - overlap_tokens
        return chunked_docs, doc_indices
    except Exception:
        # Fallback: approximate 1 token ~= 4 chars.
        max_chars = max(1, int(max_tokens) * 4)
        overlap_chars = max(0, int(overlap_tokens) * 4)
        chunked_docs = []
        doc_indices = []
        for idx, doc in enumerate(documents):
            if len(doc) <= max_chars:
                chunked_docs.append(doc)
                doc_indices.append(idx)
                continue
            start = 0
            while start < len(doc):
                end = min(start + max_chars, len(doc))
                chunked_docs.append(doc[start:end])
                doc_indices.append(idx)
                if end >= len(doc):
                    break
                start = max(0, end - overlap_chars)
        return chunked_docs, doc_indices


def _aggregate_chunk_scores(
    *,
    chunk_results: list[dict[str, Any]],
    doc_indices: list[int],
    num_original_docs: int,
) -> list[dict[str, Any]]:
    # Aggregate by max relevance_score.
    scores = [0.0] * num_original_docs
    for r in chunk_results:
        try:
            idx = int(r.get("index"))
            score = float(r.get("relevance_score"))
        except Exception:
            continue
        if 0 <= idx < len(doc_indices):
            orig = doc_indices[idx]
            if 0 <= orig < num_original_docs:
                scores[orig] = max(scores[orig], score)
    out = [{"index": i, "relevance_score": s} for i, s in enumerate(scores) if s > 0.0]
    out.sort(key=lambda x: x["relevance_score"], reverse=True)
    return out


def _normalize_rerank_results(payload: Any) -> list[dict[str, Any]]:
    # Expected: [{"index": int, "relevance_score": float}, ...]
    if not isinstance(payload, list):
        return []
    out: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        idx = item.get("index", item.get("document_index"))
        score = item.get("relevance_score", item.get("score", item.get("relevanceScore")))
        try:
            out.append({"index": int(idx), "relevance_score": float(score)})
        except Exception:
            continue
    return out


def build_standard_rerank_model_func(cfg: RerankConfig) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    async def _rerank(*, query: str, documents: list[str], top_n: int | None = None, **_: Any) -> list[dict[str, Any]]:
        import httpx

        q = (query or "").strip()
        if not q or not documents:
            return []

        started = time.perf_counter()
        original_documents = documents
        doc_indices: list[int] | None = None
        api_top_n = top_n

        if cfg.enable_chunking:
            documents, doc_indices = _chunk_documents_for_rerank(documents, max_tokens=max(1, int(cfg.max_tokens_per_doc)))
            # When chunking is enabled, disable API-level top_n to preserve doc-level semantics.
            api_top_n = None

        request_format = (cfg.request_format or "standard").strip().lower()
        logger.info(
            "lightrag rerank call",
            extra={
                "provider": request_format,
                "model": cfg.model,
                "base_url": cfg.base_url,
                "query_len": len(q),
                "docs": len(original_documents),
                "docs_sent": len(documents),
                "top_n": top_n,
                "enable_chunking": cfg.enable_chunking,
                "timeout_sec": cfg.timeout_sec,
            },
        )
        if request_format == "aliyun":
            body: dict[str, Any] = {"model": cfg.model, "input": {"query": q, "documents": documents}, "parameters": {}}
            if api_top_n is not None:
                body["parameters"]["top_n"] = int(api_top_n)
            body["parameters"]["return_documents"] = False
        else:
            body = {"model": cfg.model, "query": q, "documents": documents}
            if api_top_n is not None:
                body["top_n"] = int(api_top_n)

        headers = {"content-type": "application/json"}
        if cfg.api_key:
            headers["authorization"] = f"Bearer {cfg.api_key}"

        try:
            timeout = httpx.Timeout(float(cfg.timeout_sec or 0.0) or 15.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(cfg.base_url, headers=headers, json=body)
                resp.raise_for_status()
                try:
                    data = resp.json()
                except Exception as exc:
                    raise RuntimeError(f"Invalid rerank response JSON: {resp.text[:500]}") from exc
        except Exception:
            logger.exception(
                "lightrag rerank failed",
                extra={
                    "provider": request_format,
                    "model": cfg.model,
                    "base_url": cfg.base_url,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            raise

        results_payload = None
        if isinstance(data, dict):
            # Common shapes:
            # - {"results": [...]}
            # - {"output": {"results": [...]}}  (aliyun-like)
            # - {"data": [...]}                (some proxies)
            results_payload = data.get("results")
            if results_payload is None:
                results_payload = (data.get("output") or {}).get("results")
            if results_payload is None:
                results_payload = data.get("data")

        results = _normalize_rerank_results(results_payload)
        if cfg.enable_chunking and doc_indices:
            results = _aggregate_chunk_scores(
                chunk_results=results,
                doc_indices=doc_indices,
                num_original_docs=len(original_documents),
            )
            if top_n is not None:
                results = results[: int(top_n)]

        logger.info(
            "lightrag rerank done",
            extra={
                "provider": request_format,
                "model": cfg.model,
                "base_url": cfg.base_url,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "returned": len(results),
            },
        )
        return results

    return _rerank
