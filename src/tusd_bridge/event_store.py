"""Unified event store for domain events."""

import json

from google.protobuf.json_format import MessageToDict
from hook_pb2 import HookRequest
from sqlalchemy.orm import Session

from tusd_bridge.models import DomainEvent, FileListView
from tusd_bridge.projector import project_event


def hook_request_to_json(request: HookRequest) -> str:
    """Serialize a HookRequest protobuf message to a JSON string."""
    d: dict[str, object] = MessageToDict(request, preserving_proto_field_name=True)
    return json.dumps(d, ensure_ascii=False)


def append_event(
    session: Session,
    stream_id: str,
    stream_type: str,
    event_type: str,
    payload: str,
) -> tuple[DomainEvent, FileListView]:
    """Append a single event to the domain event store and update projection."""
    event = DomainEvent(
        stream_id=stream_id,
        stream_type=stream_type,
        event_type=event_type,
        payload=payload,
    )
    session.add(event)
    session.flush()
    view = project_event(session, event)
    session.commit()
    return event, view


def append_hook_event(
    session: Session, request: HookRequest
) -> tuple[DomainEvent, FileListView]:
    """Convert a HookRequest to a DomainEvent and persist it."""
    return append_event(
        session,
        stream_id=request.event.upload.id,
        stream_type="upload",
        event_type=f"hook.{request.type}",
        payload=hook_request_to_json(request),
    )
