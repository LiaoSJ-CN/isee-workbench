"""Runtime validation of report parameter values against a parameter spec.

Used by ``POST /reports/generate`` (and any future entry point that needs
typed runtime validation, e.g. a CLI seeder or a scheduler that supplies
parameters from the job row). Kept orthogonal to the SQL safety net in
``sql_validator.substitute_parameters`` — that helper protects against
unsafe SQL, this one protects against values that don't match the report's
declared contract.
"""

from datetime import datetime
from typing import Any

from app.models.report_parameter import ReportParameter


class ParameterValidationError(Exception):
    """Raised when request parameters don't match the report's parameter spec.

    The ``detail`` is a human-readable string suitable for surfacing in
    an HTTP 400 response body.
    """


def validate_parameters(
    spec: list[ReportParameter], values: dict[str, Any]
) -> dict[str, Any]:
    """Return ``values`` with defaults filled in for missing optional params.

    Raises ``ParameterValidationError`` on:
      - unknown key (typo in ``{name}`` substitution)
      - missing required value with no default
      - value that can't be coerced to the declared type
      - enum value not in the declared options list

    The returned dict is a fresh copy; the input ``values`` is not mutated.
    """
    # SQLAlchemy Mapped[str] is typed as `str | None` even though the
    # column is NOT NULL — narrow by filtering, so we can use these as
    # dict keys below without `str | None` indexing.
    by_name: dict[str, ReportParameter] = {
        p.name: p for p in spec if p.name is not None
    }

    # Unknown keys — fail fast on typos before doing per-value coercion.
    unknown = set(values) - set(by_name)
    if unknown:
        raise ParameterValidationError(
            f"unknown parameter(s): {sorted(unknown)!r} "
            f"(declared: {sorted(by_name)!r})"
        )

    result: dict[str, Any] = {}
    for name, param in by_name.items():
        if name in values:
            result[name] = _coerce(param, values[name])
            continue

        # Not supplied by caller — try default, then check required.
        if param.default is not None:
            # The default was stored typed JSON; coerce to be safe (e.g. a
            # numeric string default that bypassed the schema layer).
            result[name] = _coerce(param, param.default)
        elif param.required:
            raise ParameterValidationError(f"missing required parameter: {name!r}")
        # else: optional + no default → omit

    return result


def _coerce(param: ReportParameter, value: Any) -> Any:
    """Coerce ``value`` to ``param``'s declared type. Raise on mismatch."""
    kind = param.type

    if kind == "string":
        if not isinstance(value, str):
            raise ParameterValidationError(
                f"parameter {param.name!r}: expected string, got {type(value).__name__}"
            )
        return value.strip()

    if kind == "number":
        # bool is a subclass of int in Python — reject explicitly to avoid
        # ``True`` silently satisfying a number parameter.
        if isinstance(value, bool) or type(value) is bool:
            raise ParameterValidationError(
                f"parameter {param.name!r}: expected number, got bool"
            )
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                # ``float`` accepts ints and floats; int values round-trip
                # cleanly through ``float`` for the binding layer.
                return float(value)
            except ValueError as exc:
                raise ParameterValidationError(
                    f"parameter {param.name!r}: expected number, got {value!r}"
                ) from exc
        raise ParameterValidationError(
            f"parameter {param.name!r}: expected number, got {type(value).__name__}"
        )

    if kind == "date":
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value)
            except ValueError as exc:
                raise ParameterValidationError(
                    f"parameter {param.name!r}: expected ISO-8601 date, got {value!r}"
                ) from exc
            return value
        # ``datetime.date`` passes through; ``datetime.datetime`` is also
        # acceptable — the substitute layer just renders it via SQLAlchemy.
        if hasattr(value, "isoformat"):
            return value.isoformat()
        raise ParameterValidationError(
            f"parameter {param.name!r}: expected ISO-8601 date string, "
            f"got {type(value).__name__}"
        )

    if kind == "enum":
        options: list[str] | None = param.options
        if not options:
            # Should be caught at the schema layer (EnumParam requires
            # non-empty options), but defend in depth.
            raise ParameterValidationError(
                f"parameter {param.name!r}: enum has no options"
            )
        if value not in options:
            raise ParameterValidationError(
                f"parameter {param.name!r}: {value!r} not in options {options!r}"
            )
        return value

    if kind == "bool":
        # Strict: don't accept "true"/"false" strings; the frontend Checkbox
        # already emits a real bool. Avoids accidental truthy strings.
        if isinstance(value, bool):
            return value
        raise ParameterValidationError(
            f"parameter {param.name!r}: expected bool, got {type(value).__name__}"
        )

    raise ParameterValidationError(
        f"parameter {param.name!r}: unknown type {kind!r} on server-side spec"
    )
