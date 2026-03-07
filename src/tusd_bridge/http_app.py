"""Starlette HTTP application for file listing REST API and SSE."""

import json
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from tusd_bridge.event_bus import EventBus
from tusd_bridge.models import DomainEvent, FileListView


def view_to_dict(view: FileListView) -> dict[str, Any]:
    return {
        "upload_id": view.upload_id,
        "display_status": view.display_status,
        "file_size": view.file_size,
        "file_offset": view.file_offset,
        "filename": view.filename,
        "filetype": view.filetype,
        "conversion_summary": (
            json.loads(view.conversion_summary) if view.conversion_summary else None
        ),
        "updated_at": view.updated_at,
    }


def _format_sse(event_id: int, data: dict[str, Any]) -> str:
    return (
        f"event: file_status_changed\n"
        f"id: {event_id}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


def _create_list_files_endpoint(
    session_factory: sessionmaker[Session],
) -> Any:
    async def list_files(request: Request) -> JSONResponse:
        status_param = request.query_params.get("status")
        limit = min(int(request.query_params.get("limit", "50")), 200)
        cursor = request.query_params.get("cursor")

        stmt = select(FileListView)

        if status_param:
            statuses = [s.strip() for s in status_param.split(",")]
            stmt = stmt.where(FileListView.display_status.in_(statuses))

        if cursor:
            cursor_event_id = int(cursor)
            stmt = stmt.where(FileListView.last_event_id < cursor_event_id)

        stmt = stmt.order_by(FileListView.last_event_id.desc()).limit(limit)

        with session_factory() as session:
            rows = session.execute(stmt).scalars().all()

        files = [view_to_dict(row) for row in rows]
        last_event_id = max((row.last_event_id for row in rows), default=0)
        next_cursor = str(rows[-1].last_event_id) if len(rows) == limit else None

        return JSONResponse(
            {
                "files": files,
                "last_event_id": last_event_id,
                "next_cursor": next_cursor,
            }
        )

    return list_files


def _create_sse_endpoint(
    session_factory: sessionmaker[Session],
    event_bus: EventBus,
) -> Any:
    async def sse_endpoint(request: Request) -> StreamingResponse:
        last_event_id_str = request.headers.get("Last-Event-ID", "0")
        last_event_id = int(last_event_id_str) if last_event_id_str.isdigit() else 0

        async def event_stream() -> AsyncIterator[str]:
            # Replay missed events from the database
            if last_event_id > 0:
                stmt = (
                    select(DomainEvent)
                    .where(DomainEvent.event_id > last_event_id)
                    .order_by(DomainEvent.event_id.asc())
                )
                with session_factory() as session:
                    missed_events = session.execute(stmt).scalars().all()
                    for evt in missed_events:
                        view = session.get(FileListView, evt.stream_id)
                        if view is not None:
                            yield _format_sse(evt.event_id, view_to_dict(view))

            # Stream real-time events
            async for sse_event in event_bus.subscribe():
                if await request.is_disconnected():
                    break
                yield _format_sse(sse_event.event_id, sse_event.data)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return sse_endpoint


def create_http_app(
    session_factory: sessionmaker[Session],
    event_bus: EventBus,
) -> Starlette:
    """Create the Starlette application with file listing routes and SSE."""
    routes = [
        Route("/files", _create_list_files_endpoint(session_factory), methods=["GET"]),
        Route(
            "/files/events",
            _create_sse_endpoint(session_factory, event_bus),
            methods=["GET"],
        ),
    ]
    return Starlette(routes=routes)
