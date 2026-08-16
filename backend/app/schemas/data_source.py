"""Pydantic schemas for data sources."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DataSourceBase(BaseModel):
    """Base fields for a data source."""

    name: str = Field(..., min_length=1, max_length=255)
    db_type: str = Field(..., pattern="^(opengauss|dws|postgresql|sqlite)$")
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str = Field(..., min_length=1, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    schema_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_required_fields(self) -> "DataSourceBase":
        """Validate required fields based on db_type."""
        if self.db_type != "sqlite":
            if not self.host:
                raise ValueError("host is required for non-SQLite databases")
            if not self.port:
                raise ValueError("port is required for non-SQLite databases")
            if not self.username:
                raise ValueError("username is required for non-SQLite databases")
        return self


class DataSourceCreate(DataSourceBase):
    """Schema for creating a data source."""

    password: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_password(self) -> "DataSourceCreate":
        """Password required for non-SQLite databases."""
        if self.db_type != "sqlite" and not self.password:
            raise ValueError("password is required for non-SQLite databases")
        return self


class DataSourceUpdate(BaseModel):
    """Schema for updating a data source."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    db_type: str | None = Field(default=None, pattern="^(opengauss|dws|postgresql|sqlite)$")
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, min_length=1, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    schema_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    password: str | None = Field(default=None, max_length=255)


class DataSourceResponse(DataSourceBase):
    """Schema returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int | None = None
    org_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Grants (批 9.3)
# ---------------------------------------------------------------------------


class GrantCreate(BaseModel):
    """Payload for ``POST /data-sources/{id}/grants``.

    Upserts on ``(data_source_id, user_id)`` — sending the same
    ``user_id`` twice updates the permission level instead of failing
    the unique constraint.
    """

    user_id: int
    permission: str = Field(..., pattern="^(read|write)$")


class GrantResponse(BaseModel):
    """One :class:`app.models.data_source_access.DataSourceAccess` row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    data_source_id: int
    user_id: int
    permission: str
    granted_by: int | None = None
    created_at: datetime | None = None


class ColumnInfo(BaseModel):
    """One column of a database table — returned by the schema browser endpoint."""

    name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(..., min_length=1, max_length=255)
    nullable: bool
    description: str | None = None


class TableInfo(BaseModel):
    """One table (or view) — groups its columns for the schema browser."""

    model_config = ConfigDict(protected_namespaces=())

    name: str = Field(..., min_length=1, max_length=255)
    schema_name: str | None = None
    columns: list[ColumnInfo] = Field(default_factory=list)


class DataSourceSchemaResponse(BaseModel):
    """Top-level schema response — list of tables in the data source."""

    tables: list[TableInfo]
