"""Tests for the SSRF guard (harvester/url_guard.py)."""
from __future__ import annotations

import pytest

from harvester.url_guard import SafeUrlError, assert_safe_public_url, is_safe_public_url


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/abs/2401.00001",
        "http://export.arxiv.org/pdf/2401.00001",
        "https://api.semanticscholar.org/graph/v1/paper/123",
    ],
)
def test_public_urls_allowed(url: str) -> None:
    assert is_safe_public_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/papers",          # loopback
        "http://localhost/admin",                # resolves to loopback
        "http://169.254.169.254/latest/meta",    # cloud metadata (link-local)
        "http://10.0.0.5/internal",              # private RFC1918
        "http://192.168.1.1/router",             # private RFC1918
        "http://172.16.0.1/",                    # private RFC1918
        "http://[::1]/",                         # IPv6 loopback
        "http://0.0.0.0/",                       # unspecified
        "ftp://arxiv.org/file",                  # disallowed scheme
        "file:///etc/passwd",                    # disallowed scheme
        "gopher://example.com/",                 # disallowed scheme
        "not-a-url",
        "",
        None,
    ],
)
def test_unsafe_urls_rejected(url: str | None) -> None:
    assert is_safe_public_url(url) is False


def test_assert_raises_for_private() -> None:
    with pytest.raises(SafeUrlError):
        assert_safe_public_url("http://127.0.0.1/secret")


def test_assert_returns_url_for_public() -> None:
    url = "https://arxiv.org/abs/2401.00001"
    assert assert_safe_public_url(url) == url
