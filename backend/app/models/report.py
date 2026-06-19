"""SQLAlchemy models for business analysis reports."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Report(Base):
    """Business analysis report configuration."""

    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    data_source_id = Column(Integer, ForeignKey("data_sources.id"), nullable=False)

    # Report layout and configuration stored as JSON
    layout_config = Column(JSON, nullable=True, default=dict)

    # Scheduled task configuration
    is_scheduled = Column(Boolean, default=False)
    cron_expression = Column(String(100), nullable=True)
    schedule_description = Column(String(255), nullable=True)

    # Output configuration
    output_formats = Column(JSON, nullable=True, default=lambda: ["excel", "html"])

    # Status
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    data_source = relationship("DataSource", backref="reports")
    items = relationship(
        "ReportItem",
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ReportItem.order_index",
    )

    def __repr__(self) -> str:
        return f"<Report(id={self.id}, name='{self.name}')>"


class ReportItem(Base):
    """Individual item within a report (chart, table, text block, etc.)."""

    __tablename__ = "report_items"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)

    # Item identification
    name = Column(String(255), nullable=False)
    item_type = Column(String(50), nullable=False)  # table, chart, text, metric

    # Display order
    order_index = Column(Integer, default=0)

    # Data query configuration
    table_name = Column(String(255), nullable=True)
    # Example: ["field1", "field2", "SUM(amount) as total"]
    fields = Column(JSON, nullable=True, default=list)
    # Example: [{"field": "status", "operator": "=", "value": "active"}]
    where_conditions = Column(JSON, nullable=True, default=list)
    # Example: ["category", "region"]
    group_by = Column(JSON, nullable=True, default=list)
    # Example: [{"field": "total", "direction": "DESC"}]
    order_by = Column(JSON, nullable=True, default=list)
    limit = Column(Integer, nullable=True, default=1000)

    # Visualization configuration
    display_config = Column(JSON, nullable=True, default=dict)
    # Example display_config: {
    #   "chart_type": "bar|line|pie|table",
    #   "title": "Sales by Region",
    #   "colors": ["#fff", "#000"],
    #   "columns": [{"field": "region", "header": "Region"}, {"field": "total", "header": "Total"}]
    # }

    # Custom SQL (alternative to auto-generated)
    custom_sql = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    report = relationship("Report", back_populates="items")

    def __repr__(self) -> str:
        return f"<ReportItem(id={self.id}, name='{self.name}', type='{self.item_type}')>"
