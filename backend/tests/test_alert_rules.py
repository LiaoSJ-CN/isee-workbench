"""Tests for deploy/prometheus/alerts/isee-workbench.yml.

Why test the YAML itself? ``deploy/prometheus/alerts/*.yml`` is shipped
as part of the product but never executed by the Python test suite
unless someone brings up the prometheus container. A typo (``status="5.x"``
vs ``status=~"5.."``) or a renamed metric (``report_generated_errors_total``
without the trailing 'd') silently disables an alert in production —
the rules file parses fine, but no alert ever fires.

These tests catch that class of bug at unit-test time:

* YAML parses, has the shape Prometheus expects (groups > rules).
* Every alert has a non-empty name, expr, severity label, and summary.
* ``alert`` names are unique within a group (Prometheus rejects dupes
  at load time, but the error is buried in container logs).
* ``expr`` only references metric series that we actually emit. The
  allow-list is hand-curated from ``backend/app/middleware/metrics.py``
  + the default ``prometheus-fastapi_instrumentator`` HTTP series. If
  you add a new metric, add it here too.
* If ``promtool`` is on PATH, run ``promtool check rules`` for the
  final syntactic check. Skipped (not failed) when the binary is
  missing — local dev sometimes runs without the prometheus stack.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

# Repo-relative path. Resolved at import time so the test fails loudly
# if someone moves the alerts file rather than silently testing nothing.
REPO_ROOT = Path(__file__).resolve().parents[2]
ALERTS_FILE = REPO_ROOT / "deploy" / "prometheus" / "alerts" / "isee-workbench.yml"

# Metric names actually emitted by the backend. Keep in sync with
# backend/app/middleware/metrics.py and the default HTTP histogram
# exposed by prometheus-fastapi_instrumentator (status, handler, method
# labels + duration_highr_seconds family).
#
# Match logic: an expr token is a metric if its base name (without the
# ``_total`` / ``_bucket`` / ``_count`` / ``_sum`` suffix) appears here,
# or if it is exactly ``up`` (Prometheus's built-in scrape health).
KNOWN_METRICS: frozenset[str] = frozenset(
    {
        "up",
        # prometheus-fastapi-instrumentator defaults
        "http_requests",
        "http_request_duration_highr_seconds",
        "http_request_duration_seconds",
        # custom — backend/app/middleware/metrics.py
        "report_generate_duration_seconds",
        "report_generate_errors",
        "webhook_delivery_attempts",
        "sql_validator_rejections",
    }
)

# PromQL keywords / aggregators / labels that look like metric tokens
# but aren't. Grep these out before validating.
PROMQL_RESERVED: frozenset[str] = frozenset(
    {
        # aggregators
        "sum",
        "min",
        "max",
        "avg",
        "count",
        "topk",
        "bottomk",
        "stddev",
        "stdvar",
        "group",
        "quantile",
        # range functions / vector selectors
        "rate",
        "irate",
        "increase",
        "delta",
        "idelta",
        "deriv",
        "predict_linear",
        "absent",
        "absent_over_time",
        "histogram_quantile",
        "clamp_min",
        "clamp_max",
        # binary operators
        "and",
        "or",
        "unless",
        "bool",
        "on",
        "ignoring",
        "group_left",
        "group_right",
        "without",
        "by",
        # scalar / vector helpers
        "le",
        # common label names (not metrics)
        "status",
        "method",
        "handler",
        "job",
        "service",
        "outcome",
        "reason",
        "rule",
        "instance",
        "format",
        "severity",
        # booleans / units
        "true",
        "false",
        "nan",
        "inf",
    }
)

# Metric suffixes Prometheus appends; strip before base-name lookup.
METRIC_SUFFIXES = ("_total", "_bucket", "_count", "_sum")


def _load_alerts() -> list[dict[str, Any]]:
    assert ALERTS_FILE.exists(), f"missing alerts file: {ALERTS_FILE}"
    raw = yaml.safe_load(ALERTS_FILE.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), "top-level must be a mapping"
    groups = raw.get("groups")
    assert isinstance(groups, list) and groups, "expected non-empty 'groups' list"
    return groups


def test_alerts_file_parses() -> None:
    """Smoke: YAML loads without error and has the shape Prometheus expects."""
    groups = _load_alerts()
    for group in groups:
        assert "name" in group and group["name"], "group missing 'name'"
        assert "rules" in group and isinstance(group["rules"], list) and group["rules"]
        for rule in group["rules"]:
            assert isinstance(rule, dict)
            assert rule.get("alert"), f"rule missing 'alert' name: {rule}"
            assert rule.get("expr"), f"alert '{rule.get('alert')}' missing 'expr'"


def test_alert_severity_is_known() -> None:
    """Severity labels must be from the documented ladder (critical/warning/info).

    Anything else gets dropped by alertmanager routes that match on
    ``severity="critical"``, so an unlabeled alert never pages.
    """
    allowed = {"critical", "warning", "info"}
    seen: list[tuple[str, str]] = []
    for group in _load_alerts():
        for rule in group["rules"]:
            labels = rule.get("labels") or {}
            sev = labels.get("severity")
            assert sev in allowed, f"alert '{rule['alert']}' severity={sev!r} not in {allowed}"
            seen.append((rule["alert"], sev))
    # Sanity: the file should contain at least one critical and one
    # warning so the ladder is exercised — flags accidental downgrades
    # if someone reverts everything to ``warning`` to silence pages.
    sevs = {sev for _, sev in seen}
    assert "critical" in sevs, "expected at least one 'critical' alert"
    assert "warning" in sevs, "expected at least one 'warning' alert"


def test_alert_summary_present() -> None:
    """Every alert needs a ``summary`` annotation; otherwise alertmanager
    templates render with an empty title and operators ignore it."""
    for group in _load_alerts():
        for rule in group["rules"]:
            annotations = rule.get("annotations") or {}
            summary = annotations.get("summary")
            assert summary and summary.strip(), (
                f"alert '{rule['alert']}' missing non-empty annotations.summary"
            )


def test_alert_names_unique_within_group() -> None:
    """Prometheus rejects duplicate alert names in the same group; the
    error is buried in container logs at startup, so guard it here."""
    for group in _load_alerts():
        names = [r["alert"] for r in group["rules"] if "alert" in r]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"duplicate alert names in group '{group['name']}': {dupes}"


_METRIC_REF_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\(|{)")


def test_alert_exprs_only_reference_known_metrics() -> None:
    """Each ``expr`` should only query metric series we actually emit.

    Catches renames / typos (``report_generated_errors_total``) that
    parse fine but never fire because no such series exists.
    """
    for group in _load_alerts():
        for rule in group["rules"]:
            expr = rule.get("expr") or ""
            for token in _METRIC_REF_RE.findall(expr):
                if token in PROMQL_RESERVED or token in {"on", "by", "le"}:
                    continue
                # Strip Prometheus metric suffix to find the base name.
                base = token
                for suf in METRIC_SUFFIXES:
                    if base.endswith(suf):
                        base = base[: -len(suf)]
                        break
                assert base in KNOWN_METRICS, (
                    f"alert '{rule['alert']}' expr references unknown metric "
                    f"{token!r} (base={base!r}). Add it to KNOWN_METRICS in "
                    f"backend/tests/test_alert_rules.py or fix the typo."
                )


def test_alerts_have_for_clause_to_avoid_flapping() -> None:
    """Every alert should set ``for: ...`` so single-sample spikes don't page.

    Without it, a one-second 5xx burst fires a critical alert and wakes
    someone up. The defaults here are 1–15 minutes depending on the
    metric's expected cadence.
    """
    for group in _load_alerts():
        for rule in group["rules"]:
            assert "for" in rule, (
                f"alert '{rule['alert']}' missing 'for' clause — single-sample "
                f"noise will page on every spike"
            )


@pytest.mark.skipif(
    shutil.which("promtool") is None,
    reason="promtool not on PATH; install prometheus or skip",
)
def test_promtool_check_rules() -> None:
    """Run promtool for the syntactic check we can't replicate in Python.

    promtool also catches label-name validity, template syntax, and a
    few expression errors we deliberately don't replicate.
    """
    result = subprocess.run(
        ["promtool", "check", "rules", str(ALERTS_FILE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"promtool rejected the rules file:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
