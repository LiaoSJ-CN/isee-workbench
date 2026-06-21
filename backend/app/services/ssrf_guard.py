"""Outbound URL validator for webhook delivery.

The scheduler posts generated report notifications to user-supplied URLs.
Without a check, an attacker can point the URL at internal services
(127.0.0.1, 10.x, 169.254.169.254 cloud metadata, etc.) — classic SSRF.

This module gives a single chokepoint call sites use before any outbound
HTTP. It rejects:

* non-http(s) schemes (file:, ftp:, javascript:, …)
* IP literals in loopback / private / link-local / reserved / multicast
  / unspecified ranges, both IPv4 and IPv6
* hostnames whose DNS resolution returns any address in the above ranges
* hostnames that fail to resolve

Residual risk (not mitigated here): DNS rebinding between validation and
the actual httpx connection. Mitigating that needs a custom httpx
transport that pins the resolved IP and re-validates it on connect; out
of scope for the initial fix.
"""

import ipaddress
import socket
from urllib.parse import urlparse

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
        addr = info[4][0]
        if _ip_is_blocked(addr):
            raise SSRFBlocked(
                f"hostname {hostname!r} resolves to blocked address {addr}"
            )
