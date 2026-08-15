"""SQLAlchemy model for typed runtime parameters on a report."""

from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    from app.models.report import Report


class ReportParameter(Base):
    """A typed runtime parameter declaration for a report.

    ``name`` is the substitution key used in ``{name}`` placeholders inside
    ``ReportItem.custom_sql``. ``type`` drives both the form widget rendered
    on the frontend and the runtime validation of values submitted to
    ``POST /reports/generate``.
    """

    __tablename__ = "report_parameters"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(
        Integer,
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = Column(String(64), nullable=False)
    label = Column(String(255), nullable=False)
    # "string" | "number" | "date" | "enum" | "bool"
    type = Column(String(16), nullable=False)
    required = Column(Boolean, nullable=False, default=True)
    # Typed JSON: str | int | float | bool | None. Mirrors the variant's declared type.
    default = Column(JSON, nullable=True)
    # list[str] for enum type; None otherwise.
    options = Column(JSON, nullable=True)

    order_index = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    report: Mapped["Report"] = relationship("Report", back_populates="parameters")

    __table_args__ = (
        UniqueConstraint(
            "report_id", "name", name="uq_report_parameters_report_id_name"
        ),
        Index("ix_report_parameters_report_id_order", "report_id", "order_index"),
    )

    def __repr__(self) -> str:
        return (
            f"<ReportParameter(id={self.id}, report_id={self.report_id}, "
            f"name='{self.name}', type='{self.type}')>"
        )
