"""XSS regression: render every seeded report and confirm the output
contains expected markup and never leaks raw ``<script>alert`` payloads.

Skipped automatically if the metadata DB has no active reports — run
``python scripts/seed_reports.py`` to populate.
"""

import pytest
from sqlalchemy.orm import Session, selectinload

from app.database import SessionLocal
from app.models.report import Report
from app.services.report_generator import ReportGeneratorError, generate_report


@pytest.fixture(scope="module")
def seeded_reports() -> list[Report]:
    db: Session = SessionLocal()
    try:
        # Eager-load items so the test can iterate them after the session
        # is closed without hitting DetachedInstanceError on lazy load.
        reports = (
            db.query(Report)
            .options(selectinload(Report.items))
            .filter(Report.is_active.is_(True))
            .all()
        )
        if not reports:
            pytest.skip("no active reports; run seed_reports.py to populate app.db")
        return reports
    finally:
        db.close()


def test_all_seeded_reports_render_successfully(seeded_reports) -> None:
    db: Session = SessionLocal()
    try:
        for report in seeded_reports:
            try:
                result = generate_report(
                    report=report,
                    output_format="html",
                    parameters={},
                    db=db,
                    preview_only=True,
                )
            except ReportGeneratorError as exc:
                pytest.fail(f"report {report.id} ('{report.name}') failed: {exc}")

            html = result.get("preview_data", "")
            assert html, f"report {report.id} returned empty html"
            assert "<title>" in html, f"report {report.id} missing <title>"
            assert "/static/chart.umd.min.js" in html, (
                f"report {report.id} missing Chart.js script src"
            )
            assert "<script>alert" not in html, f"report {report.id} contains raw <script>alert"

            # Per-item-type assertions.
            item_types = {it.item_type for it in report.items}
            if "table" in item_types:
                assert "<table>" in html
            if "metric" in item_types:
                assert "class='metric'" in html
            if "chart" in item_types:
                assert "<canvas" in html
    finally:
        db.close()
