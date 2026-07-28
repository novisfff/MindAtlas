"""JSON content-type and same-origin policy for credential-exchange endpoints."""

from __future__ import annotations

import secrets

from starlette.requests import Request

from app.common.exceptions import ApiException


def require_json_same_origin(request: Request, *, canonical_origin: str) -> None:
    """Reject non-JSON or cross-origin credential submissions before secret checks.

    Order is intentional: content-type first, then Origin, then Sec-Fetch-Site.
    Comparisons use constant-time digests so origin length does not leak via
    short-circuit string equality.
    """
    media_type = request.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if media_type != "application/json":
        raise ApiException(
            status_code=415,
            code=41510,
            message="json_content_type_required",
        )
    origin = request.headers.get("origin", "")
    if not canonical_origin or not secrets.compare_digest(origin, canonical_origin):
        raise ApiException(
            status_code=403,
            code=40312,
            message="same_origin_required",
        )
    if request.headers.get("sec-fetch-site", "").lower() != "same-origin":
        raise ApiException(
            status_code=403,
            code=40312,
            message="same_origin_required",
        )
