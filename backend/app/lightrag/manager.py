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
import socket
import sys
import threading
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import Any
from urllib.parse import urlparse

from app.config import get_settings
from app.lightrag.errors import LightRagConfigError, LightRagDependencyError, LightRagNotEnabledError
from app.system_settings.runtime_config_service import resolve_runtime_knowledge_graph_config

logger = logging.getLogger(__name__)
_INIT_LOCK = threading.Lock()


@dataclass(frozen=True)
class _OpenAICompatModelConfig:
    api_key: str
    base_url: str
    model: str


def _lightrag_openai_error_text(exc: BaseException) -> str:
    parts: list[str] = []

    message = str(exc).strip()
    if message:
        parts.append(message)

    body = getattr(exc, "body", None)
    if body:
        parts.append(str(body).strip())

    response = getattr(exc, "response", None)
    if response is not None:
        status_text = getattr(response, "text", None)
        if isinstance(status_text, str) and status_text.strip():
            parts.append(status_text.strip())

    return " | ".join(part for part in parts if part)


def _lightrag_openai_status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue

    response = getattr(exc, "response", None)
    if response is None:
        return None

    value = getattr(response, "status_code", None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lightrag_requires_stream_mode(exc: BaseException) -> bool:
    lowered = _lightrag_openai_error_text(exc).lower()
    if "stream must be set to true" not in lowered:
        return False

    status_code = _lightrag_openai_status_code(exc)
    return status_code in (None, 400)


def _lightrag_response_format_maybe_unsupported(exc: BaseException) -> bool:
    lowered = _lightrag_openai_error_text(exc).lower()
    return "response_format" in lowered or "json_object" in lowered or "json_schema" in lowered


def _coerce_stream_chunk_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue
            text_obj = getattr(item, "text", None)
            if isinstance(text_obj, str):
                parts.append(text_obj)
        return "".join(parts)
    return ""


async def _collect_async_text(chunks: AsyncIterator[str]) -> str:
    parts: list[str] = []
    async for chunk in chunks:
        if chunk:
            parts.append(str(chunk))
    return "".join(parts)


async def _keyword_extraction_stream_fallback(
    *,
    client_factory: Callable[..., Any],
    model: str,
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict[str, Any]] | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    use_azure: bool = False,
    azure_deployment: str | None = None,
    api_version: str | None = None,
    **kwargs: Any,
) -> str:
    history = list(history_messages or [])
    messages = kwargs.pop("messages", None)
    if messages is None:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})

    kwargs.pop("hashing_kv", None)
    kwargs.pop("keyword_extraction", None)
    kwargs.pop("stream", None)

    client_configs = dict(kwargs.pop("openai_client_configs", {}) or {})
    timeout = kwargs.pop("timeout", None)
    if timeout is not None:
        kwargs["timeout"] = timeout

    response_format = kwargs.get("response_format")
    if response_format is None:
        kwargs["response_format"] = {"type": "json_object"}
        response_format = kwargs["response_format"]

    kwargs["stream"] = True

    client = client_factory(
        api_key=api_key,
        base_url=base_url,
        use_azure=use_azure,
        azure_deployment=azure_deployment,
        api_version=api_version,
        timeout=timeout,
        client_configs=client_configs,
    )
    api_model = azure_deployment if use_azure and azure_deployment else model
    response = None

    async def _send(api_kwargs: dict[str, Any]):
        return await client.chat.completions.create(model=api_model, messages=messages, **api_kwargs)

    try:
        try:
            response = await _send(kwargs)
        except Exception as exc:
            if response_format is None or not _lightrag_response_format_maybe_unsupported(exc):
                raise
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("response_format", None)
            response = await _send(retry_kwargs)

        parts: list[str] = []
        async for chunk in response:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            text = _coerce_stream_chunk_text(getattr(delta, "content", None))
            if text:
                parts.append(text)
        return "".join(parts)
    finally:
        if response is not None and hasattr(response, "aclose"):
            aclose = getattr(response, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception:
                    logger.debug("lightrag stream fallback response close failed", exc_info=True)
        close = getattr(client, "close", None)
        if callable(close):
            try:
                await close()
            except Exception:
                logger.debug("lightrag stream fallback client close failed", exc_info=True)


async def _openai_complete_with_stream_compat(
    *,
    complete_func: Callable[..., Awaitable[Any]],
    client_factory: Callable[..., Any],
    model: str,
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict[str, Any]] | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    **kwargs: Any,
) -> Any:
    try:
        return await complete_func(
            model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            base_url=base_url,
            api_key=api_key,
            **kwargs,
        )
    except Exception as exc:
        requested_stream = kwargs.get("stream") is True
        if requested_stream or not _lightrag_requires_stream_mode(exc):
            raise

        logger.info(
            "lightrag openai retry_with_stream model=%s base_url=%s keyword_extraction=%s",
            model,
            _redact_url(base_url or ""),
            bool(kwargs.get("keyword_extraction")),
        )

        if kwargs.get("keyword_extraction"):
            return await _keyword_extraction_stream_fallback(
                client_factory=client_factory,
                model=model,
                prompt=prompt,
                system_prompt=system_prompt,
                history_messages=history_messages,
                base_url=base_url,
                api_key=api_key,
                **kwargs,
            )

        retry_kwargs = dict(kwargs)
        retry_kwargs["stream"] = True
        streamed = await complete_func(
            model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            base_url=base_url,
            api_key=api_key,
            **retry_kwargs,
        )
        if hasattr(streamed, "__aiter__"):
            return await _collect_async_text(streamed)
        return streamed


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
    # Keep user-provided scheme as-is.
    #
    # NOTE: Using `neo4j://` enables routing. In containerized single-instance deployments,
    # routing can break if Neo4j advertises an address not reachable from the client container,
    # leading to long hangs/timeouts during initialization. Prefer `bolt://` unless you
    # intentionally run a routed/cluster setup.
    return (uri or "").strip()


def _normalize_rerank_url(raw: str | None) -> str:
    url = (raw or "").strip().rstrip("/")
    if not url:
        return ""
    # Common OpenAI-compatible base url endswith /v1; rerank endpoint is /v1/rerank.
    if url.endswith("/v1"):
        return url + "/rerank"
    return url


def _redact_url(url: str) -> str:
    """Redact secrets from a URL for logging."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            return url
        port = f":{parsed.port}" if parsed.port else ""
        scheme = parsed.scheme or ""
        path = parsed.path or ""
        return f"{scheme}://{host}{port}{path}".rstrip("/")
    except Exception:
        return url


def _tcp_preflight(*, name: str, url: str, timeout_sec: float) -> None:
    """Best-effort TCP connectivity check (DNS + connect)."""
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        logger.warning("lightrag preflight %s skipped (invalid url=%s)", name, _redact_url(url))
        return
    port = parsed.port
    if port is None:
        port = 443 if (parsed.scheme or "").lower() == "https" else 80

    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info("lightrag preflight %s ok (host=%s port=%s elapsed_ms=%s)", name, host, port, elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "lightrag preflight %s failed (host=%s port=%s elapsed_ms=%s error=%s)",
            name,
            host,
            port,
            elapsed_ms,
            f"{type(exc).__name__}: {(str(exc) or repr(exc))}",
        )


def _bool_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _step_logger(*, prefix: str):
    started = time.perf_counter()
    last = started

    def _log(step: str) -> None:
        nonlocal last
        now = time.perf_counter()
        elapsed_total_ms = int((now - started) * 1000)
        elapsed_step_ms = int((now - last) * 1000)
        logger.info("%s%s (elapsed_total_ms=%s elapsed_step_ms=%s)", prefix, step, elapsed_total_ms, elapsed_step_ms)
        last = now

    return _log


def _dump_stacks(*, label: str) -> None:
    if not _bool_env("LIGHTRAG_DUMP_STACK_ON_TIMEOUT"):
        return
    try:
        current_frames = sys._current_frames()  # noqa: SLF001
        thread_names = {t.ident: t.name for t in threading.enumerate() if t.ident is not None}

        lines: list[str] = [f"\n--- {label} (threads={len(current_frames)}) ---"]
        for thread_id, frame in current_frames.items():
            name = thread_names.get(thread_id, "<unknown>")
            lines.append(f"\n# Thread: {name} (id={thread_id})")
            lines.extend(traceback.format_stack(frame))
        logger.warning("%s", "".join(lines))
    except Exception:
        logger.exception("failed to dump stacks (label=%s)", label)


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


def _resolve_embedding_config(*, llm: _OpenAICompatModelConfig, knowledge_graph_config=None) -> _OpenAICompatModelConfig:
    settings = get_settings()
    source = (getattr(settings, "lightrag_ai_key_source", "env_or_db") or "env_or_db").strip().lower()

    cfg_model, cfg_host, cfg_key = _parse_openai_compat_model_spec(
        getattr(settings, "lightrag_embedding_model", None),
        label="LIGHTRAG_EMBEDDING_MODEL",
    )
    env_model, env_host, env_key = _parse_openai_compat_model_spec(os.environ.get("EMBEDDING_MODEL"), label="EMBEDDING_MODEL")

    db_cfg = None if source == "env_only" else _try_resolve_db_model_binding(model_type="embedding")
    model = _first_non_empty(
        getattr(knowledge_graph_config, "embedding_model_name", None),
        getattr(db_cfg, "model", None),
        cfg_model,
        env_model,
        "text-embedding-3-small",
    )
    base_url = _first_non_empty(
        getattr(knowledge_graph_config, "embedding_host", None),
        getattr(settings, "lightrag_embedding_host", None),
        cfg_host,
        env_host,
        getattr(db_cfg, "base_url", None),
        llm.base_url,
    )
    api_key = _first_non_empty(
        getattr(knowledge_graph_config, "embedding_api_key", None),
        getattr(settings, "lightrag_embedding_key", None),
        cfg_key,
        env_key,
        getattr(db_cfg, "api_key", None),
        llm.api_key,
    )
    if not api_key:
        raise LightRagConfigError("Embedding API key missing (LIGHTRAG_EMBEDDING_KEY/AI_API_KEY/OPENAI_API_KEY/DB)")
    return _OpenAICompatModelConfig(api_key=api_key, base_url=base_url, model=model)


def _apply_runtime_env(
    *,
    llm: _OpenAICompatModelConfig,
    embedding: _OpenAICompatModelConfig,
    knowledge_graph_config,
) -> None:
    # Neo4j (LightRAG uses NEO4J_USERNAME env var, while our settings use NEO4J_USER)
    neo4j_uri = _normalize_neo4j_uri(knowledge_graph_config.neo4j_uri)
    neo4j_user = (knowledge_graph_config.neo4j_user or "").strip()
    neo4j_password = (knowledge_graph_config.neo4j_password or "").strip()
    neo4j_database = (knowledge_graph_config.neo4j_database or "").strip()

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
    knowledge_graph_config = resolve_runtime_knowledge_graph_config()
    if not knowledge_graph_config.enabled:
        raise LightRagNotEnabledError("Knowledge graph is not enabled")
    if not knowledge_graph_config.configured:
        raise LightRagConfigError("Knowledge graph configuration is incomplete")

    from app.lightrag.runtime import get_lightrag_runtime

    runtime = get_lightrag_runtime()

    def _init_in_runtime():
        step = _step_logger(prefix="lightrag init: ")
        step("start")

        step("resolve llm config")
        llm = _resolve_llm_config()
        step("resolve embedding config")
        embedding = _resolve_embedding_config(llm=llm, knowledge_graph_config=knowledge_graph_config)
        step("apply runtime env")
        _apply_runtime_env(
            llm=llm,
            embedding=embedding,
            knowledge_graph_config=knowledge_graph_config,
        )
        step("resolve local settings")

        working_dir = (getattr(settings, "lightrag_working_dir", "") or "").strip() or "./lightrag_storage"
        workspace = (knowledge_graph_config.workspace or "").strip()
        graph_storage = (knowledge_graph_config.graph_storage or "").strip() or "Neo4JStorage"
        embedding_dim = int(
            getattr(knowledge_graph_config, "embedding_dim", 0)
            or getattr(settings, "lightrag_embedding_dim", 1536)
            or 1536
        )

        logger.info(
            "lightrag init config neo4j_uri=%s graph_storage=%s working_dir=%s workspace=%s llm_model=%s llm_base=%s embedding_model=%s embedding_base=%s embedding_dim=%s",
            _redact_url(os.environ.get("NEO4J_URI", "")),
            graph_storage,
            working_dir,
            workspace,
            llm.model,
            _redact_url(llm.base_url),
            embedding.model,
            _redact_url(embedding.base_url),
            embedding_dim,
        )
        step("preflight")

        preflight_enabled = _bool_env("LIGHTRAG_PREFLIGHT_ENABLED")
        if preflight_enabled:
            timeout_sec = float(os.environ.get("LIGHTRAG_PREFLIGHT_TIMEOUT_SEC") or 5.0)
            _tcp_preflight(name="neo4j", url=os.environ.get("NEO4J_URI", ""), timeout_sec=timeout_sec)
            _tcp_preflight(name="llm", url=llm.base_url, timeout_sec=timeout_sec)
            _tcp_preflight(name="embedding", url=embedding.base_url, timeout_sec=timeout_sec)

        step("import lightrag")
        try:
            from lightrag import LightRAG
            from lightrag.llm.openai import create_openai_async_client, openai_complete_if_cache, openai_embed
            from lightrag.utils import EmbeddingFunc
        except ImportError as e:
            raise LightRagDependencyError("lightrag-hku is not installed") from e

        step("build llm/embedding funcs")
        async def llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs) -> str | AsyncIterator[str]:
            return await _openai_complete_with_stream_compat(
                complete_func=openai_complete_if_cache,
                client_factory=create_openai_async_client,
                model=llm.model,
                prompt=prompt,
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

        step("build rag kwargs")
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
            getattr(knowledge_graph_config, "summary_language", None),
            os.environ.get("SUMMARY_LANGUAGE"),
        )
        if summary_language:
            rag_kwargs["addon_params"] = {"language": summary_language}

        # Optional rerank model (standard rerank API; commonly provided by vLLM/LiteLLM proxies).
        # If configured, we enable rerank by default; otherwise disable it explicitly.
        rerank_model = _first_non_empty(
            getattr(knowledge_graph_config, "rerank_model", None),
            os.environ.get("RERANK_MODEL"),
        )
        rerank_host = _first_non_empty(
            getattr(knowledge_graph_config, "rerank_host", None),
            os.environ.get("RERANK_BINDING_HOST"),
        )
        rerank_key = _first_non_empty(
            getattr(knowledge_graph_config, "rerank_api_key", None),
            os.environ.get("RERANK_BINDING_API_KEY"),
        )
        rerank_url = _normalize_rerank_url(rerank_host)
        rerank_enabled = bool(rerank_model and rerank_url)
        os.environ["RERANK_BY_DEFAULT"] = "true" if rerank_enabled else "false"

        if rerank_enabled:
            step("build rerank model func")
            from app.lightrag.rerank_client import RerankConfig, build_standard_rerank_model_func

            rerank_timeout_sec = float(getattr(settings, "lightrag_rerank_timeout_sec", 15.0) or 15.0)
            rerank_request_format = _first_non_empty(
                getattr(knowledge_graph_config, "rerank_request_format", None),
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

        step("create LightRAG instance start")
        rag = LightRAG(**rag_kwargs)
        step("create LightRAG instance done")

        init_timeout_sec = float(getattr(settings, "lightrag_init_timeout_sec", 120.0) or 120.0)
        started = time.perf_counter()
        loop = runtime.loop
        try:
            step(f"initialize_storages start (timeout_sec={init_timeout_sec})")
            loop.run_until_complete(asyncio.wait_for(rag.initialize_storages(), timeout=init_timeout_sec))
        except asyncio.TimeoutError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            # NOTE: Some deployments may filter ERROR; keep this visible at WARNING too.
            logger.warning(
                "lightrag initialize_storages timed out (timeout_sec=%s elapsed_ms=%s)",
                init_timeout_sec,
                elapsed_ms,
            )
            _dump_stacks(label="lightrag initialize_storages timeout")
            raise TimeoutError(f"initialize_storages timed out after {init_timeout_sec}s") from exc
        except Exception:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.exception("lightrag initialize_storages failed (elapsed_ms=%s)", elapsed_ms)
            raise
        else:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info("lightrag initialized (elapsed_ms=%s)", elapsed_ms)
            step("initialize_storages done")
        return rag

    import asyncio

    init_timeout_sec = float(getattr(settings, "lightrag_init_timeout_sec", 120.0) or 120.0)
    runtime_timeout_sec = init_timeout_sec + 10.0
    try:
        return runtime.call(_init_in_runtime, timeout_sec=runtime_timeout_sec)
    except TimeoutError as exc:
        # Distinguish the two different timeout sources:
        # - our explicit initialize_storages timeout (message contains "initialize_storages timed out")
        # - Future.result(timeout=...) timeout from runtime.call (often empty message)
        msg = (str(exc) or "").strip()
        if "initialize_storages timed out" in msg:
            raise
        _dump_stacks(label="lightrag runtime.call timeout")
        raise TimeoutError(f"lightrag runtime init timed out after {runtime_timeout_sec}s") from exc


@lru_cache(maxsize=1)
def get_rag():
    """Get LightRAG singleton instance for the current process."""
    # Double-locking: lru_cache prevents duplicate work; this lock keeps initialization linearized and explicit.
    settings = get_settings()
    knowledge_graph_config = resolve_runtime_knowledge_graph_config()
    if not knowledge_graph_config.enabled:
        raise LightRagNotEnabledError("Knowledge graph is not enabled")
    if not knowledge_graph_config.configured:
        raise LightRagConfigError("Knowledge graph configuration is incomplete")
    init_timeout_sec = float(getattr(settings, "lightrag_init_timeout_sec", 120.0) or 120.0)
    lock_timeout_sec = init_timeout_sec + 10.0
    wait_started = time.perf_counter()
    acquired = _INIT_LOCK.acquire(timeout=lock_timeout_sec)
    wait_ms = int((time.perf_counter() - wait_started) * 1000)
    if not acquired:
        logger.warning(
            "lightrag init lock timeout",
            extra={
                "lock_timeout_sec": lock_timeout_sec,
                "wait_ms": wait_ms,
            },
        )
        _dump_stacks(label="lightrag init lock timeout")
        raise TimeoutError(f"lightrag init lock timed out after {lock_timeout_sec}s")
    if wait_ms >= 1000:
        logger.info("lightrag init lock waited", extra={"wait_ms": wait_ms})
    try:
        return _create_and_init_rag()
    finally:
        _INIT_LOCK.release()


def reset_lightrag_singletons_for_tests() -> None:
    """Test hook to clear in-process caches."""
    try:
        get_rag.cache_clear()
    except Exception:
        pass
