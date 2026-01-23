"""LightRAG runtime manager (Phase 5).

Goals:
- Single source of truth for LightRAG initialization (extract from Indexer).
- Process-level singleton for LightRAG instance.
- Explicit AI key injection strategy (env_only | env_or_db).

Notes:
- Imports lightrag-hku lazily to keep base app importable without the optional dependency.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from functools import lru_cache, partial

from app.config import get_settings
from app.lightrag.errors import LightRagConfigError, LightRagDependencyError, LightRagNotEnabledError

logger = logging.getLogger(__name__)
_INIT_LOCK = threading.Lock()


@dataclass(frozen=True)
class _OpenAICompatModelConfig:
    api_key: str
    base_url: str
    model: str


def _first_non_empty(*values: str | None) -> str:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _parse_openai_compat_model_spec(raw: str | None, *, label: str) -> tuple[str | None, str | None, str | None]:
    """Parse an OpenAI-compatible model spec.

    Accepts:
    - Plain string model name: "gpt-4o-mini"
    - JSON object with keys (case-insensitive):
        MODEL/model, HOST/host/base_url, KEY/key/api_key
    Returns (model, base_url, api_key), each optional.
    """
    s = (raw or "").strip()
    if not s:
        return None, None, None
    if not s.lstrip().startswith("{"):
        return s, None, None

    try:
        data = json.loads(s)
    except Exception as e:
        raise LightRagConfigError(f"{label} is not valid JSON") from e
    if not isinstance(data, dict):
        raise LightRagConfigError(f"{label} must be a JSON object")

    normalized: dict[str, object] = {}
    for k, v in data.items():
        if isinstance(k, str):
            normalized[k.lower()] = v

    def _get_str(*keys: str) -> str | None:
        for key in keys:
            v = normalized.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    model = _get_str("model", "name")
    base_url = _get_str("host", "base_url", "baseurl", "api_base", "api_base_url", "openai_api_base")
    api_key = _get_str("key", "api_key", "apikey", "openai_api_key")
    return model, base_url, api_key


def _normalize_neo4j_uri(uri: str) -> str:
    u = (uri or "").strip()
    if u.startswith("bolt://"):
        return u.replace("bolt://", "neo4j://", 1)
    return u


def _normalize_rerank_url(raw: str | None) -> str:
    url = (raw or "").strip().rstrip("/")
    if not url:
        return ""
    # Common OpenAI-compatible base url endswith /v1; rerank endpoint is /v1/rerank.
    if url.endswith("/v1"):
        return url + "/rerank"
    return url


def _try_resolve_db_model_binding(*, model_type: str) -> _OpenAICompatModelConfig | None:
    try:
        from app.ai_registry.runtime import resolve_openai_compat_config
        from app.database import SessionLocal
    except Exception:
        return None

    try:
        db = SessionLocal()
        try:
            cfg = resolve_openai_compat_config(db, component="lightrag", model_type=model_type)  # type: ignore[arg-type]
        finally:
            db.close()
    except Exception:
        return None

    if not cfg:
        return None

    model = _first_non_empty(getattr(cfg, "model", None))
    base_url = _first_non_empty(getattr(cfg, "base_url", None))
    api_key = _first_non_empty(getattr(cfg, "api_key", None))
    if not (model and base_url and api_key):
        return None

    return _OpenAICompatModelConfig(api_key=api_key, base_url=base_url, model=model)


def _resolve_llm_config() -> _OpenAICompatModelConfig:
    settings = get_settings()

    # 0) Parse model specs (supports JSON {MODEL,HOST,KEY}).
    cfg_model, cfg_host, cfg_key = _parse_openai_compat_model_spec(
        getattr(settings, "lightrag_llm_model", None),
        label="LIGHTRAG_LLM_MODEL",
    )
    env_model, env_host, env_key = _parse_openai_compat_model_spec(os.environ.get("LLM_MODEL"), label="LLM_MODEL")

    source = (getattr(settings, "lightrag_ai_key_source", "env_or_db") or "env_or_db").strip().lower()

    db_cfg = None if source == "env_only" else _try_resolve_db_model_binding(model_type="llm")

    # LLM model is DB-first (default binding), then config/env.
    model = _first_non_empty(getattr(db_cfg, "model", None), cfg_model, env_model, getattr(settings, "ai_model", None))
    if not model:
        raise LightRagConfigError("LLM model missing (DB/LIGHTRAG_LLM_MODEL/LLM_MODEL/AI_MODEL)")

    llm_host = _first_non_empty(getattr(settings, "lightrag_llm_host", None), cfg_host, env_host)
    base_url = _first_non_empty(
        llm_host,
        getattr(db_cfg, "base_url", None),
        os.environ.get("OPENAI_API_BASE"),
        getattr(settings, "ai_base_url", None),
        "https://api.openai.com/v1",
    )

    llm_key = _first_non_empty(getattr(settings, "lightrag_llm_key", None), cfg_key, env_key)
    api_key = _first_non_empty(llm_key, getattr(settings, "ai_api_key", None), os.environ.get("OPENAI_API_KEY"), getattr(db_cfg, "api_key", None))
    if not api_key:
        raise LightRagConfigError("OpenAI API key missing (LIGHTRAG_LLM_KEY/AI_API_KEY/OPENAI_API_KEY/DB)")

    return _OpenAICompatModelConfig(api_key=api_key, base_url=base_url, model=model)


def _resolve_embedding_config(*, llm: _OpenAICompatModelConfig) -> _OpenAICompatModelConfig:
    settings = get_settings()

    cfg_model, cfg_host, cfg_key = _parse_openai_compat_model_spec(
        getattr(settings, "lightrag_embedding_model", None),
        label="LIGHTRAG_EMBEDDING_MODEL",
    )
    env_model, env_host, env_key = _parse_openai_compat_model_spec(os.environ.get("EMBEDDING_MODEL"), label="EMBEDDING_MODEL")

    # Embedding configuration is always read from config/env (no DB binding lookup).
    model = _first_non_empty(cfg_model, env_model, "text-embedding-3-small")
    base_url = _first_non_empty(getattr(settings, "lightrag_embedding_host", None), cfg_host, env_host, llm.base_url)
    api_key = _first_non_empty(getattr(settings, "lightrag_embedding_key", None), cfg_key, env_key, llm.api_key)
    if not api_key:
        raise LightRagConfigError("Embedding API key missing (LIGHTRAG_EMBEDDING_KEY/AI_API_KEY/OPENAI_API_KEY)")
    return _OpenAICompatModelConfig(api_key=api_key, base_url=base_url, model=model)


def _apply_runtime_env(*, llm: _OpenAICompatModelConfig, embedding: _OpenAICompatModelConfig) -> None:
    settings = get_settings()

    # Neo4j (LightRAG uses NEO4J_USERNAME env var, while our settings use NEO4J_USER)
    neo4j_uri = _normalize_neo4j_uri(settings.neo4j_uri)
    neo4j_user = (settings.neo4j_user or "").strip()
    neo4j_password = (settings.neo4j_password or "").strip()
    neo4j_database = (getattr(settings, "neo4j_database", "") or "").strip()

    if not neo4j_uri or not neo4j_user:
        raise LightRagConfigError("Neo4j is not configured (NEO4J_URI/NEO4J_USER)")
    if not neo4j_password:
        raise LightRagConfigError("Neo4j password missing (NEO4J_PASSWORD)")
    if not neo4j_database:
        raise LightRagConfigError("Neo4j database missing (NEO4J_DATABASE)")

    os.environ["NEO4J_URI"] = neo4j_uri
    os.environ["NEO4J_USERNAME"] = neo4j_user
    os.environ["NEO4J_PASSWORD"] = neo4j_password
    os.environ["NEO4J_DATABASE"] = neo4j_database

    # OpenAI-compatible env vars used by lightrag-hku internals
    os.environ["OPENAI_API_KEY"] = llm.api_key
    os.environ["OPENAI_API_BASE"] = llm.base_url
    # Ensure these are plain model names even if user provided JSON specs.
    os.environ["LLM_MODEL"] = llm.model
    os.environ["EMBEDDING_MODEL"] = embedding.model


def _create_and_init_rag():
    settings = get_settings()
    if not settings.lightrag_enabled:
        raise LightRagNotEnabledError("LightRAG is not enabled (LIGHTRAG_ENABLED=false)")

    from app.lightrag.runtime import get_lightrag_runtime

    runtime = get_lightrag_runtime()

    def _init_in_runtime():
        llm = _resolve_llm_config()
        embedding = _resolve_embedding_config(llm=llm)
        _apply_runtime_env(llm=llm, embedding=embedding)

        working_dir = (getattr(settings, "lightrag_working_dir", "") or "").strip() or "./lightrag_storage"
        workspace = (getattr(settings, "lightrag_workspace", "") or "").strip()
        graph_storage = (getattr(settings, "lightrag_graph_storage", "") or "").strip() or "Neo4JStorage"
        embedding_dim = int(getattr(settings, "lightrag_embedding_dim", 1536) or 1536)

        try:
            from lightrag import LightRAG
            from lightrag.llm.openai import openai_complete_if_cache, openai_embed
            from lightrag.utils import EmbeddingFunc
        except ImportError as e:
            raise LightRagDependencyError("lightrag-hku is not installed") from e

        async def llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs) -> str:
            return await openai_complete_if_cache(
                llm.model,
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages,
                base_url=llm.base_url,
                api_key=llm.api_key,
                **kwargs,
            )

        embedding_func = EmbeddingFunc(
            embedding_dim=embedding_dim,
            model_name=embedding.model,
            max_token_size=8192,
            send_dimensions=True,
            func=partial(
                openai_embed.func,
                model=embedding.model,
                base_url=embedding.base_url,
                api_key=embedding.api_key,
            ),
        )

        rag_kwargs = {
            "working_dir": working_dir,
            "graph_storage": graph_storage,
            "embedding_func": embedding_func,
            "llm_model_func": llm_model_func,
        }
        if workspace:
            rag_kwargs["namespace"] = workspace

        # Language for internal LightRAG prompts (summary/entity extraction).
        # Upstream env var name is SUMMARY_LANGUAGE; we prefer per-instance addon_params.
        summary_language = _first_non_empty(
            getattr(settings, "lightrag_summary_language", None),
            os.environ.get("SUMMARY_LANGUAGE"),
        )
        if summary_language:
            rag_kwargs["addon_params"] = {"language": summary_language}

        # Optional rerank model (standard rerank API; commonly provided by vLLM/LiteLLM proxies).
        # If configured, we enable rerank by default; otherwise disable it explicitly.
        rerank_model = _first_non_empty(
            getattr(settings, "lightrag_rerank_model", None),
            os.environ.get("RERANK_MODEL"),
        )
        rerank_host = _first_non_empty(
            getattr(settings, "lightrag_rerank_host", None),
            os.environ.get("RERANK_BINDING_HOST"),
        )
        rerank_key = _first_non_empty(
            getattr(settings, "lightrag_rerank_key", None),
            os.environ.get("RERANK_BINDING_API_KEY"),
        )
        rerank_url = _normalize_rerank_url(rerank_host)
        rerank_enabled = bool(rerank_model and rerank_url)
        os.environ["RERANK_BY_DEFAULT"] = "true" if rerank_enabled else "false"

        if rerank_enabled:
            from app.lightrag.rerank_client import RerankConfig, build_standard_rerank_model_func

            rerank_timeout_sec = float(getattr(settings, "lightrag_rerank_timeout_sec", 15.0) or 15.0)
            rerank_request_format = _first_non_empty(
                getattr(settings, "lightrag_rerank_request_format", None),
                os.environ.get("RERANK_BINDING"),
            ).strip().lower() or "standard"
            rerank_enable_chunking = bool(getattr(settings, "lightrag_rerank_enable_chunking", False) or False)
            rerank_max_tokens_per_doc = int(getattr(settings, "lightrag_rerank_max_tokens_per_doc", 480) or 480)
            min_rerank_score = float(getattr(settings, "lightrag_min_rerank_score", 0.0) or 0.0)

            rag_kwargs["rerank_model_func"] = build_standard_rerank_model_func(
                RerankConfig(
                    model=rerank_model,
                    base_url=rerank_url,
                    api_key=rerank_key or None,
                    timeout_sec=rerank_timeout_sec,
                    request_format=rerank_request_format,
                    enable_chunking=rerank_enable_chunking,
                    max_tokens_per_doc=max(1, rerank_max_tokens_per_doc),
                )
            )
            rag_kwargs["min_rerank_score"] = max(0.0, min_rerank_score)

        rag = LightRAG(**rag_kwargs)

        init_timeout_sec = float(getattr(settings, "lightrag_init_timeout_sec", 120.0) or 120.0)
        started = time.perf_counter()
        loop = runtime.loop
        loop.run_until_complete(asyncio.wait_for(rag.initialize_storages(), timeout=init_timeout_sec))
        logger.info("lightrag initialized", extra={"elapsed_ms": int((time.perf_counter() - started) * 1000)})
        return rag

    import asyncio

    return runtime.call(_init_in_runtime, timeout_sec=float(getattr(settings, "lightrag_init_timeout_sec", 120.0) or 120.0) + 10.0)


@lru_cache(maxsize=1)
def get_rag():
    """Get LightRAG singleton instance for the current process."""
    # Double-locking: lru_cache prevents duplicate work; this lock keeps initialization linearized and explicit.
    with _INIT_LOCK:
        return _create_and_init_rag()


def reset_lightrag_singletons_for_tests() -> None:
    """Test hook to clear in-process caches."""
    try:
        get_rag.cache_clear()
    except Exception:
        pass
