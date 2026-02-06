from __future__ import annotations

_OPENAI_COMPAT_USER_AGENT = "MindAtlas/1.0"


def build_openai_compat_client_headers() -> dict[str, str]:
    """Headers passed to OpenAI SDK client (without auth)."""
    return {
        "Accept": "application/json",
        "User-Agent": _OPENAI_COMPAT_USER_AGENT,
    }


def build_openai_compat_request_headers(api_key: str) -> dict[str, str]:
    """Headers for direct HTTP requests to OpenAI-compatible APIs."""
    return {
        "content-type": "application/json",
        "accept": "application/json",
        "user-agent": _OPENAI_COMPAT_USER_AGENT,
        "authorization": f"Bearer {(api_key or '').strip()}",
    }

