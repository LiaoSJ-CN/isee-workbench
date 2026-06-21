"""Unit tests for backend.app.services.ssrf_guard.

The validator runs before any outbound HTTP, so the rejection contract
matters more than the resolution behavior on any one host. We mock
socket.getaddrinfo where DNS would otherwise depend on the runner.
"""

from unittest.mock import patch

import pytest

from app.services.ssrf_guard import SSRFBlocked, validate_webhook_url

# ---------- scheme allow-list ----------

@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "gopher://example.com/_test",
        "data:text/plain,hi",
        "ldap://internal/x",
    ],
)
def test_rejects_non_http_scheme(url: str) -> None:
    with pytest.raises(SSRFBlocked, match="scheme"):
        validate_webhook_url(url)


def test_accepts_http_and_https_schemes() -> None:
    # IP literal so we don't touch DNS — both schemes must pass the scheme gate.
    validate_webhook_url("http://8.8.8.8/x")
    validate_webhook_url("https://1.1.1.1/x")


# ---------- IPv4 literal denial list ----------

@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",       # loopback
        "127.255.255.254", # loopback range upper edge
        "0.0.0.0",         # unspecified
        "10.0.0.1",        # private (RFC1918)
        "192.168.1.1",     # private (RFC1918)
        "172.16.0.1",      # private (RFC1918)
        "169.254.169.254", # link-local (cloud metadata!)
        "224.0.0.1",       # multicast
        "240.0.0.1",       # reserved
    ],
)
def test_rejects_blocked_ipv4_literal(host: str) -> None:
    with pytest.raises(SSRFBlocked):
        validate_webhook_url(f"http://{host}/x")


# ---------- IPv6 literal denial list ----------

@pytest.mark.parametrize(
    "host",
    [
        "::1",        # loopback
        "fc00::1",    # ULA (private)
        "fd00::1",    # ULA (private)
        "fe80::1",    # link-local
        "ff02::1",    # multicast
        "::",         # unspecified
    ],
)
def test_rejects_blocked_ipv6_literal(host: str) -> None:
    with pytest.raises(SSRFBlocked):
        validate_webhook_url(f"http://[{host}]/x")


# ---------- hostname DNS resolution ----------

def test_rejects_localhost_via_dns() -> None:
    # Most CI runners resolve localhost to 127.0.0.1; mock to lock behavior
    # independent of /etc/hosts.
    with patch(
        "app.services.ssrf_guard.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("127.0.0.1", 0))],
    ):
        with pytest.raises(SSRFBlocked, match="resolves to blocked address"):
            validate_webhook_url("http://localhost/x")


def test_rejects_hostname_with_any_blocked_address() -> None:
    # Even one private address in a multi-IP response is enough — attacker
    # could otherwise bind one public + one private and hit the private one.
    with patch(
        "app.services.ssrf_guard.socket.getaddrinfo",
        return_value=[
            (2, 1, 6, "", ("8.8.8.8", 0)),
            (2, 1, 6, "", ("10.0.0.5", 0)),
        ],
    ):
        with pytest.raises(SSRFBlocked, match="10.0.0.5"):
            validate_webhook_url("https://example.com/x")


def test_rejects_when_dns_resolution_fails() -> None:
    import socket

    with patch(
        "app.services.ssrf_guard.socket.getaddrinfo",
        side_effect=socket.gaierror("Name or service not known"),
    ):
        with pytest.raises(SSRFBlocked, match="DNS resolution failed"):
            validate_webhook_url("https://no-such-host.invalid/x")


# ---------- hostname happy path ----------

def test_accepts_hostname_resolving_to_public_ip() -> None:
    with patch(
        "app.services.ssrf_guard.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("8.8.8.8", 0))],
    ):
        validate_webhook_url("https://example.com/x")


def test_accepts_public_ipv4_literal() -> None:
    validate_webhook_url("http://8.8.8.8/x")


def test_accepts_public_ipv6_literal() -> None:
    # 2606:4700:4700::1111 is Cloudflare DNS (public, routable).
    validate_webhook_url("https://[2606:4700:4700::1111]/x")


# ---------- malformed URLs ----------

def test_rejects_missing_hostname() -> None:
    with pytest.raises(SSRFBlocked, match="hostname"):
        validate_webhook_url("http:///path")


def test_rejects_empty_url() -> None:
    with pytest.raises(SSRFBlocked):
        validate_webhook_url("")
