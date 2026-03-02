from __future__ import annotations

import json
import mimetypes
import os
import ssl
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from uuid import uuid4

from app.common.ssrf import validate_url_ssrf
from app.config import get_settings

_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_BODY_TYPES = {"none", "json", "raw", "x-www-form-urlencoded", "form-data"}
_FORM_DATA_TYPES = {"text", "file"}
_AUTH_TYPES = {"none", "bearer", "api_key"}


class _SSRFSafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url_ssrf(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class HttpRequestResult:
    body: str
    status_code: int
    headers: dict[str, Any]
    ok: bool
    error_message: str
    response: str


def _normalize_method(raw: Any) -> str:
    method = str(raw or "GET").strip().upper() or "GET"
    return method if method in _HTTP_METHODS else "GET"


def _normalize_body_type(raw: Any) -> str:
    body_type = str(raw or "none").strip().lower() or "none"
    return body_type if body_type in _BODY_TYPES else "none"


def _normalize_auth_type(raw: Any) -> str:
    auth_type = str(raw or "none").strip().lower() or "none"
    return auth_type if auth_type in _AUTH_TYPES else "none"


def _normalize_query_pairs(rows: list[dict[str, Any]] | None) -> list[tuple[str, str]]:
    if not isinstance(rows, list):
        return []
    pairs: list[tuple[str, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        enabled = item.get("enabled", True)
        if isinstance(enabled, bool) and not enabled:
            continue
        key = str(item.get("key", "") or "").strip()
        if not key:
            continue
        value = item.get("value")
        pairs.append((key, "" if value is None else str(value)))
    return pairs


def _normalize_form_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        enabled = item.get("enabled", True)
        if isinstance(enabled, bool) and not enabled:
            continue
        key = str(item.get("key", "") or "").strip()
        if not key:
            continue
        value = item.get("value")
        value_text = "" if value is None else str(value)
        row_type = str(item.get("type", "text") or "text").strip().lower() or "text"
        if row_type not in _FORM_DATA_TYPES:
            row_type = "text"
        normalized.append(
            {
                "key": key,
                "value": value_text,
                "type": row_type,
            }
        )
    return normalized


def _normalize_header_map(rows: list[dict[str, Any]] | None) -> dict[str, str]:
    if not isinstance(rows, list):
        return {}
    headers: dict[str, str] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        enabled = item.get("enabled", True)
        if isinstance(enabled, bool) and not enabled:
            continue
        key = str(item.get("key", "") or "").strip()
        if not key:
            continue
        value = item.get("value")
        headers[key] = "" if value is None else str(value)
    return headers


def _append_query_params(url: str, query_pairs: list[tuple[str, str]]) -> str:
    if not query_pairs:
        return url
    parsed = urlsplit(url)
    existing = parse_qsl(parsed.query, keep_blank_values=True)
    merged = existing + query_pairs
    next_query = urlencode(merged, doseq=True)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, next_query, parsed.fragment))


def _build_http_opener(*, verify_ssl: bool):
    if verify_ssl:
        return build_opener(_SSRFSafeRedirectHandler)
    insecure_context = ssl._create_unverified_context()
    return build_opener(_SSRFSafeRedirectHandler, HTTPSHandler(context=insecure_context))


def _has_header(headers: dict[str, str], header_name: str) -> bool:
    expected = header_name.strip().lower()
    return any(str(key).strip().lower() == expected for key in headers.keys())


def _escape_quotes(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _resolve_file_part_payload(value_text: str, key: str) -> tuple[bytes, str]:
    candidate = value_text.strip()
    if candidate and os.path.isfile(candidate):
        file_name = os.path.basename(candidate) or f"{key}.bin"
        with open(candidate, "rb") as handle:
            return handle.read(), file_name
    fallback_name = f"{key}.bin"
    return value_text.encode("utf-8"), fallback_name


def _build_multipart_form_data(rows: list[dict[str, str]]) -> tuple[bytes, str]:
    boundary = f"----MindAtlasBoundary{uuid4().hex}"
    boundary_bytes = boundary.encode("utf-8")
    body_parts: list[bytes] = []

    for row in rows:
        key = row["key"]
        value_text = row["value"]
        row_type = row.get("type", "text")
        body_parts.append(b"--" + boundary_bytes + b"\r\n")
        if row_type == "file":
            payload, file_name = _resolve_file_part_payload(value_text, key)
            mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            disposition = (
                f'Content-Disposition: form-data; name="{_escape_quotes(key)}"; '
                f'filename="{_escape_quotes(file_name)}"\r\n'
            )
            body_parts.append(disposition.encode("utf-8"))
            body_parts.append(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
            body_parts.append(payload)
            body_parts.append(b"\r\n")
            continue

        disposition = f'Content-Disposition: form-data; name="{_escape_quotes(key)}"\r\n\r\n'
        body_parts.append(disposition.encode("utf-8"))
        body_parts.append(value_text.encode("utf-8"))
        body_parts.append(b"\r\n")

    body_parts.append(b"--" + boundary_bytes + b"--\r\n")
    return b"".join(body_parts), f"multipart/form-data; boundary={boundary}"


def _build_request_body(
    *,
    method: str,
    body_type: str,
    json_body_template: str,
    raw_body_template: str,
    form_body: list[dict[str, Any]] | None,
) -> tuple[bytes | None, str | None]:
    if method in {"GET", "DELETE"}:
        return None, None
    if body_type == "none":
        return None, None
    if body_type == "json":
        body_text = json_body_template or ""
        return body_text.encode("utf-8"), "application/json"
    if body_type == "raw":
        body_text = raw_body_template or ""
        return body_text.encode("utf-8"), "text/plain; charset=utf-8"
    if body_type == "x-www-form-urlencoded":
        pairs = _normalize_query_pairs(form_body)
        body_text = urlencode(pairs, doseq=True)
        return body_text.encode("utf-8"), "application/x-www-form-urlencoded"
    if body_type == "form-data":
        rows = _normalize_form_rows(form_body)
        return _build_multipart_form_data(rows)
    return None, None


def _coerce_timeout_ms(timeout_ms: Any) -> int:
    settings = get_settings()
    default_timeout = max(1, int(getattr(settings, "workflow_http_request_timeout_ms", 15000) or 15000))
    max_timeout = max(default_timeout, int(getattr(settings, "workflow_http_request_max_timeout_ms", 60000) or 60000))
    try:
        parsed = int(timeout_ms)
    except Exception:
        parsed = default_timeout
    return max(1, min(max_timeout, parsed))


def _coerce_retry_settings(
    *,
    retry_enabled: Any,
    max_retries: Any,
    retry_interval_ms: Any,
) -> tuple[bool, int, int]:
    settings = get_settings()
    enabled = bool(retry_enabled)
    max_allowed = max(0, int(getattr(settings, "workflow_http_request_max_retries", 5) or 5))
    try:
        retries = int(max_retries)
    except Exception:
        retries = 2
    retries = max(0, min(max_allowed, retries))
    try:
        interval = int(retry_interval_ms)
    except Exception:
        interval = 200
    interval = max(0, min(5000, interval))
    return enabled, retries, interval


def _response_body_max_bytes() -> int:
    settings = get_settings()
    value = int(getattr(settings, "workflow_http_request_max_response_bytes", 524288) or 524288)
    return max(1024, value)


def _read_response_body_limited(stream: Any, *, max_bytes: int) -> bytes:
    remaining = max_bytes
    chunks: list[bytes] = []
    while remaining > 0:
        chunk = stream.read(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _decode_response_body(raw_body: bytes, headers_obj: Any) -> str:
    charset = "utf-8"
    try:
        if headers_obj is not None and hasattr(headers_obj, "get_content_charset"):
            charset = headers_obj.get_content_charset() or "utf-8"
    except Exception:
        charset = "utf-8"
    try:
        return raw_body.decode(charset, errors="replace")
    except Exception:
        return raw_body.decode("utf-8", errors="replace")


def _build_status_error_message(status_code: int, reason: Any) -> str:
    reason_text = str(reason or "").strip()
    return f"HTTP {status_code}{f' {reason_text}' if reason_text else ''}"


def execute_http_request(
    *,
    method: Any,
    url: Any,
    headers: list[dict[str, Any]] | None = None,
    query_params: list[dict[str, Any]] | None = None,
    body_type: Any = "none",
    json_body_template: Any = "",
    raw_body_template: Any = "",
    form_body: list[dict[str, Any]] | None = None,
    auth_type: Any = "none",
    bearer_token: Any = "",
    api_key_in: Any = "header",
    api_key_name: Any = "X-API-Key",
    api_key_value: Any = "",
    timeout_ms: Any = None,
    retry_enabled: Any = False,
    max_retries: Any = 2,
    retry_interval_ms: Any = 200,
    verify_ssl: Any = True,
) -> HttpRequestResult:
    method_normalized = _normalize_method(method)
    url_text = str(url or "").strip()
    if not url_text:
        raise ValueError("http_request url is required")

    headers_map = _normalize_header_map(headers)
    query_pairs = _normalize_query_pairs(query_params)
    auth_mode = _normalize_auth_type(auth_type)
    if auth_mode == "bearer":
        token = str(bearer_token or "")
        if token:
            headers_map["Authorization"] = f"Bearer {token}"
    elif auth_mode == "api_key":
        key_name = str(api_key_name or "X-API-Key").strip() or "X-API-Key"
        key_value = str(api_key_value or "")
        key_in = str(api_key_in or "header").strip().lower()
        if key_value:
            if key_in == "query":
                query_pairs.append((key_name, key_value))
            else:
                headers_map[key_name] = key_value

    final_url = _append_query_params(url_text, query_pairs)
    validate_url_ssrf(final_url)

    body_data, content_type = _build_request_body(
        method=method_normalized,
        body_type=_normalize_body_type(body_type),
        json_body_template=str(json_body_template or ""),
        raw_body_template=str(raw_body_template or ""),
        form_body=form_body,
    )
    if content_type and not _has_header(headers_map, "Content-Type"):
        headers_map["Content-Type"] = content_type

    timeout_ms_final = _coerce_timeout_ms(timeout_ms)
    timeout_sec = max(0.001, timeout_ms_final / 1000.0)
    retry_enabled_final, max_retries_final, retry_interval_ms_final = _coerce_retry_settings(
        retry_enabled=retry_enabled,
        max_retries=max_retries,
        retry_interval_ms=retry_interval_ms,
    )
    max_attempts = 1 + (max_retries_final if retry_enabled_final else 0)
    verify_ssl_final = bool(verify_ssl) if isinstance(verify_ssl, bool) else True
    max_body_bytes = _response_body_max_bytes()

    for attempt in range(max_attempts):
        request = Request(final_url, data=body_data, headers=headers_map, method=method_normalized)
        opener = _build_http_opener(verify_ssl=verify_ssl_final)
        try:
            with opener.open(request, timeout=timeout_sec) as response:
                status_code = int(response.getcode() or 0)
                raw_body = _read_response_body_limited(response, max_bytes=max_body_bytes)
                body_text = _decode_response_body(raw_body, response.headers)
                response_headers = dict(response.headers.items())
                ok = 200 <= status_code < 300
                error_message = "" if ok else _build_status_error_message(status_code, getattr(response, "reason", ""))
                return HttpRequestResult(
                    body=body_text,
                    status_code=status_code,
                    headers=response_headers,
                    ok=ok,
                    error_message=error_message,
                    response=body_text,
                )
        except HTTPError as exc:
            status_code = int(exc.code or 0)
            if (
                retry_enabled_final
                and status_code >= 500
                and attempt < max_attempts - 1
            ):
                if retry_interval_ms_final > 0:
                    time.sleep(retry_interval_ms_final / 1000.0)
                continue

            raw_body = b""
            try:
                raw_body = _read_response_body_limited(exc, max_bytes=max_body_bytes)
            except Exception:
                raw_body = b""
            body_text = _decode_response_body(raw_body, exc.headers)
            response_headers = dict(exc.headers.items()) if exc.headers is not None else {}
            return HttpRequestResult(
                body=body_text,
                status_code=status_code,
                headers=response_headers,
                ok=False,
                error_message=_build_status_error_message(status_code, exc.reason),
                response=body_text,
            )
        except (URLError, TimeoutError, ssl.SSLError, OSError) as exc:
            if retry_enabled_final and attempt < max_attempts - 1:
                if retry_interval_ms_final > 0:
                    time.sleep(retry_interval_ms_final / 1000.0)
                continue
            reason = getattr(exc, "reason", exc)
            raise RuntimeError(f"http_request transport failed: {reason}") from exc

    raise RuntimeError("http_request transport failed: exhausted retries")
