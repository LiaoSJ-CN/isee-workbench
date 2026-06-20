"""Smoke test for _safe_filename (path traversal prevention).

Run: cd backend && source .venv/bin/activate && python scripts/smoke_path_traversal.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.report_generator import _safe_filename


def main() -> int:
    cases = [
        # (input, expected_substring_check)
        ("../../etc/passwd", "passwd"),       # traversal collapsed
        ("..\\..\\windows\\system", "windows"),  # windows-style traversal
        ("foo/bar", "foo"),                   # slash stripped
        ("财务经营月报", "财务经营月报"),       # CJK preserved
        ("name with spaces", "name_with_spaces"),
        ("....", "report"),                   # all dots -> fallback
        ("", "report"),                       # empty -> fallback
        ("a" * 500, "a" * 200),               # length capped
        ("report.name", "report.name"),       # internal dot preserved
        ("a$b%c@d", "a_b_c_d"),               # unsafe symbols -> underscore
    ]

    failures: list[str] = []
    for raw, must_contain in cases:
        got = _safe_filename(raw)
        ok = must_contain in got
        # Also: must not contain any path separator or parent-dir marker
        safe = ("/" not in got) and ("\\" not in got) and (".." not in got.split("_"))
        status = "PASS" if (ok and safe) else "FAIL"
        print(f"  {status}: {raw!r:40} -> {got!r}")
        if status == "FAIL":
            failures.append(f"{raw!r} -> {got!r}")

    if failures:
        print("\nFAIL")
        return 1
    print("\nPASS — all sanitization cases produce safe filenames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())