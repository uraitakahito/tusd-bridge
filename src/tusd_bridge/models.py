"""SQLAlchemy models for event sourcing."""

from sqlalchemy import ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UploadEvent(Base):
    __tablename__ = "upload_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[str] = mapped_column(Text, nullable=False)
    hook_type: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%f', 'now'))"),
    )
    payload: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_upload_events_upload_id", "upload_id"),
        Index("idx_upload_events_hook_type", "hook_type"),
        Index("idx_upload_events_occurred_at", "occurred_at"),
    )


class UploadSnapshot(Base):
    __tablename__ = "upload_snapshots"

    upload_id: Mapped[str] = mapped_column(Text, primary_key=True)
    current_status: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_is_deferred: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_partial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_final: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    filetype: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)
    storage_json: Mapped[str | None] = mapped_column("storage", Text, nullable=True)
    remote_addr: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_hook_type: Mapped[str] = mapped_column(Text, nullable=False)
    last_event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("upload_events.event_id"),
        nullable=False,
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%f', 'now'))"),
    )
