"""SQLAlchemy models for event sourcing."""

from sqlalchemy import ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DomainEvent(Base):
    __tablename__ = "domain_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stream_id: Mapped[str] = mapped_column(Text, nullable=False)
    stream_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%f', 'now'))"),
    )
    payload: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_domain_events_stream_id", "stream_id"),
        Index("idx_domain_events_stream_type", "stream_type"),
        Index("idx_domain_events_event_type", "event_type"),
        Index("idx_domain_events_occurred_at", "occurred_at"),
    )


class FileListView(Base):
    __tablename__ = "file_list_view"

    upload_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_status: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    filetype: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)
    storage_json: Mapped[str | None] = mapped_column("storage", Text, nullable=True)
    remote_addr: Mapped[str | None] = mapped_column(Text, nullable=True)
    outputs_json: Mapped[str | None] = mapped_column("outputs", Text, nullable=True)
    last_event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("domain_events.event_id"),
        nullable=False,
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%f', 'now'))"),
    )
