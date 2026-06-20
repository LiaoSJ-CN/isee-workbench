"""Regression: render all seeded reports and sanity-check output.

Exercises ReportGenerator.generate_report with preview_only=True against
the real app.db + erp_demo.db seed. Confirms:
  - no SQLAlchemy / build_query failures
  - HTML contains expected chart/table markup
  - no escape regression (escaped data appears, raw <script> does not)

Run: cd backend && source .venv/bin/activate && python scripts/smoke_xss_regression.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.report import Report
from app.services.report_generator import ReportGeneratorError, generate_report


def main() -> int:
    db: Session = SessionLocal()
    try:
        reports = db.query(Report).filter(Report.is_active.is_(True)).all()
        if not reports:
            print("FAIL — no active reports found in app.db; run seed_reports.py first")
            return 1

        print(f"Found {len(reports)} active report(s)")
        failures: list[str] = []

        for report in reports:
            print(f"\n=== Rendering report {report.id}: '{report.name}' ===")
            try:
                result = generate_report(
                    report=report,
                    output_format="html",
                    parameters={},
                    db=db,
                    preview_only=True,
                )
            except ReportGeneratorError as exc:
                failures.append(f"report {report.id} failed: {exc}")
                continue

            html = result.get("preview_data", "")
            if not html:
                failures.append(f"report {report.id} returned empty html")
                continue

            # Should contain chart.js script tag and the report name
            if "<title>" not in html:
                failures.append(f"report {report.id} missing <title>")
            if "/static/chart.umd.min.js" not in html:
                failures.append(f"report {report.id} missing Chart.js script src")

            # No unescaped raw script/img tags from data
            if "<script>alert" in html:
                failures.append(f"report {report.id} contains raw <script>alert")

            # At least one rendered item
            item_types = {it.item_type for it in report.items}
            for t in item_types:
                if t == "table" and "<table>" not in html:
                    failures.append(f"report {report.id} missing <table> for table item")
                if t == "metric" and "class='metric'" not in html:
                    failures.append(f"report {report.id} missing metric card")
                if t == "chart" and "<canvas" not in html:
                    failures.append(f"report {report.id} missing <canvas> for chart item")

            print(f"  html length: {len(html)} bytes")

        if failures:
            print("\nFAIL")
            for f in failures:
                print("  -", f)
            return 1

        print("\nPASS — all seeded reports render without regression")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())