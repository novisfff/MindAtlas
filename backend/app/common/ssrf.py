"""SSRF protection utilities."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


_DNS_FAKE_IP_NETWORKS = [
    # Some proxy/DNS stacks (for example Clash fake-ip mode) synthesize
    # benchmark-range answers for public hostnames. We only tolerate this
    # during hostname resolution, never for direct IP literals.
    ipaddress.ip_network("198.18.0.0/15"),
]


BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("169.254.0.0/16"),
    *_DNS_FAKE_IP_NETWORKS,
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


class SSRFError(ValueError):
    """SSRF security error."""
    pass


def _normalize_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    # Normalize IPv6-mapped IPv4 (e.g. ::ffff:127.0.0.1) to its IPv4 equivalent
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _is_dns_fake_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    normalized = _normalize_ip(ip)
    return any(normalized in net for net in _DNS_FAKE_IP_NETWORKS)


def _is_ip_blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_dns_fake_ip: bool = False,
) -> bool:
    normalized = _normalize_ip(ip)

    if allow_dns_fake_ip and _is_dns_fake_ip(normalized):
        return False

    if normalized.is_loopback or normalized.is_link_local or normalized.is_reserved or normalized.is_unspecified:
        return True

    return any(normalized in net for net in BLOCKED_NETWORKS)


def validate_url_ssrf(url: str, *, raise_api_exception: bool = False) -> None:
    """Validate URL to prevent SSRF attacks.

    Args:
        url: The URL to validate
        raise_api_exception: If True, raise ApiException instead of SSRFError
    """
    if not url:
        msg = "URL is required"
        if raise_api_exception:
            from app.common.exceptions import ApiException
            raise ApiException(status_code=400, code=40020, message=msg)
        raise SSRFError(msg)

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        msg = "URL must use http or https scheme"
        if raise_api_exception:
            from app.common.exceptions import ApiException
            raise ApiException(status_code=400, code=40021, message=msg)
        raise SSRFError(msg)

    hostname = parsed.hostname
    if not hostname:
        msg = "Invalid URL: no hostname"
        if raise_api_exception:
            from app.common.exceptions import ApiException
            raise ApiException(status_code=400, code=40022, message=msg)
        raise SSRFError(msg)

    if hostname.lower() in ("localhost", "localhost.localdomain"):
        msg = "URL cannot point to localhost"
        if raise_api_exception:
            from app.common.exceptions import ApiException
            raise ApiException(status_code=400, code=40023, message=msg)
        raise SSRFError(msg)

    # Check if hostname is a direct IP literal
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    else:
        if _is_ip_blocked(ip):
            msg = "URL resolves to blocked IP range"
            if raise_api_exception:
                from app.common.exceptions import ApiException
                raise ApiException(status_code=400, code=40024, message=msg)
            raise SSRFError(msg)
        return

    # Resolve hostname and check all IPs
    try:
        addrs = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    except socket.gaierror:
        # DNS resolution failed - allow it (might be valid external host)
        return
    except Exception as exc:
        msg = f"DNS resolution failed unexpectedly: {exc}"
        if raise_api_exception:
            from app.common.exceptions import ApiException
            raise ApiException(status_code=400, code=40025, message=msg)
        raise SSRFError(msg) from exc

    for _family, _, _, _, sockaddr in addrs:
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_ip_blocked(ip, allow_dns_fake_ip=True):
            msg = "URL resolves to blocked IP range"
            if raise_api_exception:
                from app.common.exceptions import ApiException
                raise ApiException(status_code=400, code=40024, message=msg)
            raise SSRFError(msg)


def normalize_openai_base_url(base_url: str) -> str:
    """Normalize base_url to end with /v1."""
    value = (base_url or "").strip().rstrip("/")
    if not value:
        return ""
    if not value.endswith("/v1"):
        value += "/v1"
    return value
