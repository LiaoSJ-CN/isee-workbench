"""ETag helpers for If-Match / If-None-Match (批 3 optimistic concurrency).

Weak ETags derived from the SQLAlchemy ``version_id_col`` integer
counter: ``W/"v<n>"``. Weak because the counter can flip from
side-effects unrelated to user-visible content (a cron tick, an
audit-row write that touches the row). For a single-table optimistic
lock that's fine — we want "I last read this version" semantics, not
bit-for-bit equality.

Why an integer counter and not ``updated_at``?

SQLite's ``CURRENT_TIMESTAMP`` (and ORM ``onupdate=func.now()``) collapses
to **second precision** — two writes inside the same second would
collide on the ETag and the lock would silently pass. An integer
counter sidesteps the precision issue, is cheaper to compare than a
stringified timestamp, and is the textbook SQLAlchemy
``version_id_col`` mechanism.

The RFC 7232 contract we implement:

* If-Match is OPTIONAL on PUT. Missing header → behave as if it weren't
  a concurrency check (backward compatible — pre-批 3 clients keep
  working without code changes).
* If-Match: * matches any existing resource (RFC 7232 §3.2).
* Multiple comma-separated tags are accepted; the first matchable one
  wins. RFC 7232 says we MAY accept any of them.
* Bare (unquoted) tags are accepted leniently because real-world clients
  (Postman, curl --etag-compare) sometimes strip the quotes.
"""

from __future__ import annotations

from typing import Final

_WEAK_PREFIX: Final[str] = "W/"


def compute_etag(version: int | None) -> str | None:
    """Return ``W/"v<n>"`` or ``None`` if ``version`` is unset.

    Callers should derive ``version`` from the ORM row's
    ``version_id_col`` (``Report.version`` for this batch). Newly
    inserted rows always carry ``version=1`` (column default), so this
    function only returns ``None`` if the caller hands us a NULL —
    treat that as "no ETag available" rather than emitting a
    malformed value.
    """
    if version is None:
        return None
    return f'{_WEAK_PREFIX}"v{version}"'


def parse_if_match(header_value: str | None) -> str | None:
    """Return the bare tag string from an ``If-Match`` header, or ``None``.

    Accepts the following shapes (returns the *bare* tag, no prefix, no
    quotes, so the caller compares against ``compute_etag``'s bare
    payload):

    * ``*`` → ``"*"``
    * ``W/"abc"`` / ``"abc"`` / bare ``abc`` → ``"abc"``
    * ``W/"abc", W/"def"`` → first matchable tag

    Returns ``None`` when the header is missing or no usable tag found.
    """
    if not header_value:
        return None
    for raw in header_value.split(","):
        tag = raw.strip()
        if not tag:
            continue
        if tag == "*":
            return "*"
        if tag.startswith(_WEAK_PREFIX):
            tag = tag[len(_WEAK_PREFIX) :]
        if len(tag) >= 2 and tag.startswith('"') and tag.endswith('"'):
            return tag[1:-1]
        # bare unquoted — accept leniently
        if " " not in tag and "," not in tag:
            return tag
    return None


def etag_matches(if_match: str | None, current_version: int | None) -> bool:
    """True iff ``if_match`` satisfies the precondition against ``current_version``.

    Semantics:

    * ``if_match == "*"`` matches any existing resource.
    * Exact bare-string equality on the ``v<n>`` payload.
    * ``None`` on either side → ``False`` (caller should treat as a
      conflict, not as "match").
    """
    if if_match is None or current_version is None:
        return False
    if if_match == "*":
        return True
    # ``current_version`` is an int; we expect the parsed bare tag to
    # be the ``v<n>`` shape.
    return if_match == f"v{current_version}"
