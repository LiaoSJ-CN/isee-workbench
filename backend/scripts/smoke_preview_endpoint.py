"""Regression: hit /reports/{id}/preview through the FastAPI router, not
the service directly. Catches the failure mode where render_html is
invoked without a base_url (e.g. router not plumbing request.base_url
through, or someone refactors preview_report and forgets the call).

Confirms:
  - auth still gates the endpoint
  - response is HTML
  - <base href=...> is present (required so /static/chart.umd.min.js
    resolves to the backend origin inside a blob: URL iframe)
  - ?token= query-param fallback is gone (rejected with 401)

Run: cd backend && source .venv/bin/activate && python scripts/smoke_preview_endpoint.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.report import Report
from app.services.jwt_auth import create_access_token


def main() -> int:
    db = SessionLocal()
    try:
        report = db.query(Report).filter(Report.is_active.is_(True)).first()
        if not report:
            print("FAIL — no active reports found in app.db; run seed_reports.py first")
            return 1

        token = create_access_token("admin")
        client = TestClient(app)
        failures: list[str] = []

        # 1. Unauthenticated request → 401
        r = client.get(f"/reports/{report.id}/preview", params={"format": "html"})
        if r.status_code != 401:
            failures.append(f"unauth GET expected 401, got {r.status_code}")
        else:
            print("  unauth GET → 401 ✓")

        # 2. ?token= fallback should be REJECTED (no longer supported)
        r = client.get(
            f"/reports/{report.id}/preview",
            params={"format": "html", "token": token},
        )
        if r.status_code != 401:
            failures.append(
                f"?token= fallback still works (got {r.status_code}); "
                "should be removed but is still accepted"
            )
        else:
            print("  ?token= fallback rejected with 401 ✓")

        # 3. Authenticated request via Authorization header → 200 + HTML
        r = client.get(
            f"/reports/{report.id}/preview",
            params={"format": "html"},
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code != 200:
            failures.append(f"auth GET expected 200, got {r.status_code}: {r.text[:200]}")
            print("\nFAIL")
            for f in failures:
                print("  -", f)
            return 1

        ctype = r.headers.get("content-type", "")
        if "text/html" not in ctype:
            failures.append(f"expected text/html, got {ctype!r}")

        html = r.text
        if "<base href=" not in html:
            failures.append("response HTML missing <base href=...>")
            failures.append("  → blob-URL iframe will not load Chart.js")
        else:
            print("  response contains <base href=...> ✓")

        if "/static/chart.umd.min.js" not in html:
            failures.append("response HTML missing Chart.js script src")
        if "<title>" not in html:
            failures.append("response HTML missing <title>")

        print(f"  response: HTTP {r.status_code}, {len(html)} bytes, ct={ctype}")

        if failures:
            print("\nFAIL")
            for f in failures:
                print("  -", f)
            return 1

        print("\nPASS — /reports/{id}/preview serves HTML with base href via Authorization header")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
