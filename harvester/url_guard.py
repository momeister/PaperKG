"""SSRF guard for server-side URL fetches.

PaperKG fetches PDFs and web pages whose URLs come from external APIs, search
providers and stored records — i.e. data the operator does not fully control.
Without a guard, a crafted URL could make the backend request internal services
(``http://127.0.0.1:...``), cloud metadata endpoints (``169.254.169.254``) or
other private-network hosts. This module rejects such URLs before any request is
made.

Use :func:`is_safe_public_url` (synchronous, does a DNS lookup) right before any
``httpx``/``requests`` call on an externally-influenced URL. In async code call it
via ``await asyncio.to_thread(is_safe_public_url, url)`` so the DNS lookup does not
block the event loop.

Only ``http`` and ``https`` are allowed. Every resolved IP for the host must be a
global (public) address; if *any* resolved address is private/loopback/link-local/
reserved the URL is rejected (defends against DNS rebinding to a private answer).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

__all__ = ["is_safe_public_url", "SafeUrlError", "assert_safe_public_url"]

_ALLOWED_SCHEMES = {"http", "https"}


class SafeUrlError(ValueError):
    """Raised by :func:`assert_safe_public_url` for a disallowed URL."""


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Map IPv4-mapped IPv6 (::ffff:127.0.0.1) back to IPv4 before classifying.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_safe_public_url(url: str | None) -> bool:
    """Return True only if *url* is an http(s) URL whose host resolves to public IPs.

    Never raises. A URL that cannot be parsed or whose host cannot be resolved is
    treated as unsafe (returns False), so callers fail closed.
    """
    if not url:
        return False
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return False
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    host = parts.hostname
    if not host:
        return False

    # A literal IP host: classify directly (no DNS).
    try:
        return _ip_is_public(ipaddress.ip_address(host))
    except ValueError:
        pass

    # A hostname: resolve every A/AAAA record and require all to be public.
    try:
        infos = socket.getaddrinfo(host, parts.port or None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    addresses = {str(info[4][0]) for info in infos}
    if not addresses:
        return False
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])  # strip IPv6 zone id
        except ValueError:
            return False
        if not _ip_is_public(ip):
            return False
    return True


def assert_safe_public_url(url: str | None) -> str:
    """Return *url* unchanged if safe, else raise :class:`SafeUrlError`."""
    if not is_safe_public_url(url):
        raise SafeUrlError(f"Refusing to fetch non-public or malformed URL: {url!r}")
    return str(url)
