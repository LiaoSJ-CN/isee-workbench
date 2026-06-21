"""Outbound URL validator for webhook delivery.

The scheduler posts generated report notifications to user-supplied URLs.
Without a check, an attacker can point the URL at internal services
(127.0.0.1, 10.x, 169.254.169.254 cloud metadata, etc.) — classic SSRF.

This module gives a single chokepoint call sites use before any outbound
HTTP.  It rejects:

* non-http(s) schemes (file:, ftp:, javascript:, …)
* IP literals in loopback / private / link-local / reserved / multicast
  / unspecified ranges, both IPv4 and IPv6
* hostnames whose DNS resolution returns any address in the above ranges
* hostnames that fail to resolve

P4 (PY-4) addition: ``create_webhook_client`` returns an ``httpx.Client``
whose transport pins the connection to the IP resolved during validation,
eliminating the DNS rebinding TOCTOU between the check and the HTTP call.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from httpcore import ConnectionPool, SyncBackend

ALLOWED_SCHEMES = frozenset({"http", "https"})

ALLOWED_SCHEMES = frozenset({"http", "https"})


class SSRFBlocked(ValueError):
    """Raised when an outbound URL targets a private/internal address."""


def _ip_is_blocked(addr: str) -> bool:
    """True if `addr` (IPv4 or IPv6 literal) is non-public.

    Centralizes the deny-list so the IP-literal path and the DNS path
    share one source of truth.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False  # not a literal; hostname path will handle it
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_webhook_url(url: str) -> None:
    """Raise SSRFBlocked if `url` is unsafe for outbound HTTP.

    Raises:
        SSRFBlocked: any rule above is violated.
    """
    if not url:
        raise SSRFBlocked("empty URL")

    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFBlocked(f"scheme {parsed.scheme!r} is not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlocked("URL is missing a hostname")

    # IP literal — validate directly, no DNS lookup.
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass  # not an IP literal; fall through to DNS path
    else:
        if _ip_is_blocked(hostname):
            raise SSRFBlocked(f"blocked IP literal: {hostname}")
        return

    # Hostname — resolve and check every returned address.
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFBlocked(
            f"DNS resolution failed for {hostname!r}: {exc}"
        ) from exc

    for info in infos:
        addr = str(info[4][0])
        if _ip_is_blocked(addr):
            raise SSRFBlocked(
                f"hostname {hostname!r} resolves to blocked address {addr}"
            )


# ---------------------------------------------------------------------------
# P4 (PY-4): IP-pinned httpx transport — closes the DNS rebinding TOCTOU
# between SSRF validation and the actual HTTP connection.
# ---------------------------------------------------------------------------


def _resolve_and_validate(hostname: str, port: int) -> str:
    """Resolve *hostname* and return the first non-blocked IP address.

    Raises SSRFBlocked if resolution fails or every address is blocked.
    """
    try:
        infos = socket.getaddrinfo(hostname, port)
    except socket.gaierror as exc:
        raise SSRFBlocked(f"DNS resolution failed for {hostname!r}: {exc}") from exc

    for info in infos:
        addr = str(info[4][0])
        if not _ip_is_blocked(addr):
            return addr

    raise SSRFBlocked(f"Every resolved address for {hostname!r} is blocked")


class _PinnedBackend(SyncBackend):
    """httpcore network backend that connects to a pre-validated IP.

    ``SyncBackend.connect_tcp(host, ...)`` normally resolves *host* via
    DNS and connects to the result.  This subclass ignores the *host*
    parameter and always connects to the IP that was validated at
    construction time — so a rebinding attacker cannot redirect the
    connection to a private address between the SSRF check and the
    actual HTTP call.

    TLS SNI still uses the original hostname because ``start_tls`` is
    called separately with the URL host, not the connect target.
    """

    def __init__(self, pinned_ip: str, pinned_port: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pinned_ip = pinned_ip
        self._pinned_port = pinned_port

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        return super().connect_tcp(
            self._pinned_ip,
            self._pinned_port,
            timeout,
            local_address,
            socket_options,
        )


def create_webhook_client(webhook_url: str, *, timeout: float = 30) -> httpx.Client:
    """Validate *webhook_url* and return an ``httpx.Client`` with IP-pinned transport.

    The transport is bound to the exact IP resolved during validation,
    closing the DNS rebinding gap (PY-4).

    If the URL is HTTPS, the TLS handshake uses the original hostname
    for SNI and certificate verification — only the TCP connection
    target is pinned.
    """
    # Reuse the fast-path checks from validate_webhook_url for consistency.
    validate_webhook_url(webhook_url)

    parsed = urlparse(webhook_url)
    hostname = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    pinned_ip = _resolve_and_validate(hostname, port)
    backend = _PinnedBackend(pinned_ip, port)
    pool = ConnectionPool(network_backend=backend)

    return httpx.Client(transport=pool, timeout=timeout, follow_redirects=False)  # type: ignore[arg-type]
