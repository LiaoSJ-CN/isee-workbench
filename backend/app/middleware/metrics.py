"""Prometheus metrics — `/metrics` endpoint + 4 custom counters/histograms.

批 6b.1 plan surface:

* ``report_generate_duration_seconds{format=...}`` — Histogram around
  every ``generate_report`` call (sync HTTP handler + async worker
  thread both).
* ``report_generate_errors_total{reason=...}`` — Counter for
  generation failures, partitioned by reason
  (``ReportGeneratorError``, ``DataSourceNotFound``,
  ``ExecutorCrash``).
* ``webhook_delivery_attempts_total{outcome=...}`` — Counter
  incremented by the SSRF-guarded webhook path, partitioned by
  outcome (``success``, ``ssrf_blocked``, ``https_required``,
  ``http_error``).
* ``sql_validator_rejections_total{rule=...}`` — Counter incremented
  by :mod:`app.services.sql_validator` whenever an
  :class:`UnsafeSQLError` is raised, partitioned by the rejection rule
  (parse, not_select, multi_stmt, empty, unsafe_*).

Default HTTP latency / status-code metrics come for free from
``prometheus-fastapi-instrumentator``; we leave its defaults on so
operators get request-rate + p50/p95/p99 latency for every route
without any extra code.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)


# ---- Custom metric definitions ---------------------------------------------

report_generate_duration_seconds: Histogram = Histogram(
    "report_generate_duration_seconds",
    "Time spent generating a report (sync HTTP handler + async worker), in seconds.",
    labelnames=("format",),
    # Buckets tuned for both Excel (multi-second) and HTML (sub-second) renders.
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)

report_generate_errors_total: Counter = Counter(
    "report_generate_errors_total",
    "Report-generation failures, partitioned by reason.",
    labelnames=("reason",),
)

webhook_delivery_attempts_total: Counter = Counter(
    "webhook_delivery_attempts_total",
    "Webhook delivery attempts, partitioned by outcome.",
    labelnames=("outcome",),
)

sql_validator_rejections_total: Counter = Counter(
    "sql_validator_rejections_total",
    "SQL validator rejections, partitioned by rejection rule.",
    labelnames=("rule",),
)


# ---- Setup ------------------------------------------------------------------


def setup_metrics(app: FastAPI) -> None:
    """Attach Prometheus instrumentation and expose ``/metrics``.

    Idempotent: safe to call from ``main.lifespan`` even if tests
    import this module more than once — Instrumentator mutates
    ``app.user_middleware`` / route table, but does so via a single
    reference per app instance.
    """
    Instrumentator(
        # Keep status-code labels expanded (2xx/3xx/4xx/5xx) so dashboards
        # can split by class without losing per-code fidelity.
        should_group_status_codes=False,
        # Skip routes not decorated with ``@instrument`` — we only want
        # the default HTTP histogram, not per-route boilerplate.
        should_ignore_untemplated=True,
    ).instrument(app).expose(
        app,
        endpoint="/metrics",
        # Keep OpenAPI surface clean — operators find /metrics via docs
        # link, not the schema.
        include_in_schema=False,
    )
    logger.info("Prometheus /metrics endpoint enabled")


__all__ = [
    "setup_metrics",
    "report_generate_duration_seconds",
    "report_generate_errors_total",
    "webhook_delivery_attempts_total",
    "sql_validator_rejections_total",
]
