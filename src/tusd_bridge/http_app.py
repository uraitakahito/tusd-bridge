"""Starlette HTTP application for file listing REST API and SSE."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from tusd_bridge.airflow_client import AirflowClient, DagTriggerPayload
from tusd_bridge.event_store import append_event
from tusd_bridge.models import DomainEvent, FileListView
from tusd_bridge.processing import trigger_processing

POLLING_INTERVAL_SECONDS = 0.5

VALID_PROCESSING_STATUSES = {"completed", "failed"}


def view_to_dict(view: FileListView, tusd_download_base_url: str) -> dict[str, Any]:
    original: dict[str, Any] = {
        "filename": view.filename,
        "filetype": view.filetype,
        "url": f"{tusd_download_base_url}/{view.upload_id}",
        "size": view.file_size,
    }
    converted: list[dict[str, Any]] | None = None
    if view.outputs_json:
        converted = json.loads(view.outputs_json)

    return {
        "upload_id": view.upload_id,
        "display_status": view.display_status,
        "original": original,
        "converted": converted,
        "file_offset": view.file_offset,
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
    tusd_download_base_url: str,
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

        files = [view_to_dict(row, tusd_download_base_url) for row in rows]
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


def _resolve_cursor(request: Request) -> int:
    """Resolve the SSE cursor from Last-Event-ID header or cursor query param."""
    header = request.headers.get("Last-Event-ID", "")
    if header.isdigit() and int(header) > 0:
        return int(header)
    param = request.query_params.get("cursor", "")
    if param.isdigit():
        return int(param)
    return 0


def _create_sse_endpoint(
    session_factory: sessionmaker[Session],
    tusd_download_base_url: str,
) -> Any:
    async def sse_endpoint(request: Request) -> StreamingResponse:
        last_event_id = _resolve_cursor(request)

        async def event_stream() -> AsyncIterator[str]:
            # DB をイベントの Single Source of Truth として使用する。
            # domain_events テーブルを定期的にポーリングすることで、
            # インメモリ EventBus の subscribe タイミングに依存していた
            # レースコンディション (DB クエリ完了〜subscribe 開始の間に
            # publish されたイベントが欠落する問題) を構造的に排除する。
            cursor = last_event_id
            while True:
                with session_factory() as session:
                    stmt = (
                        select(DomainEvent)
                        .where(DomainEvent.event_id > cursor)
                        .order_by(DomainEvent.event_id.asc())
                    )
                    new_events = session.execute(stmt).scalars().all()
                    for evt in new_events:
                        view = session.get(FileListView, evt.stream_id)
                        if view is not None:
                            yield _format_sse(
                                evt.event_id, view_to_dict(view, tusd_download_base_url)
                            )
                        cursor = evt.event_id

                if await request.is_disconnected():
                    break
                await asyncio.sleep(POLLING_INTERVAL_SECONDS)

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


def _create_rerun_endpoint(
    session_factory: sessionmaker[Session],
    tusd_download_base_url: str,
    airflow_client: AirflowClient,
) -> Any:
    async def rerun(request: Request) -> JSONResponse:
        upload_id = request.path_params["upload_id"]

        with session_factory() as session:
            view = session.get(FileListView, upload_id)
            if view is None:
                return JSONResponse({"error": "upload not found"}, status_code=404)
            if view.filename is None:
                return JSONResponse(
                    {"error": "filename is missing, cannot rerun processing"},
                    status_code=400,
                )
            download_url = f"{tusd_download_base_url}/{upload_id}"
            payload = DagTriggerPayload(
                upload_id=upload_id,
                download_url=download_url,
                filename=view.filename,
                filetype=view.filetype,
            )
            trigger_processing(session, payload, airflow_client)

        return JSONResponse({"upload_id": upload_id, "status": "triggered"})

    return rerun


def _create_webhook_endpoint(
    session_factory: sessionmaker[Session],
) -> Any:
    async def webhook_processing_result(request: Request) -> JSONResponse:
        body: dict[str, Any] = await request.json()

        upload_id = body.get("upload_id")
        if not isinstance(upload_id, str) or not upload_id:
            return JSONResponse({"error": "upload_id is required"}, status_code=400)

        status = body.get("status")
        if status not in VALID_PROCESSING_STATUSES:
            return JSONResponse(
                {
                    "error": f"status must be one of: {', '.join(sorted(VALID_PROCESSING_STATUSES))}"
                },
                status_code=400,
            )

        if status == "completed":
            outputs_val: object = body.get("outputs")
            if (
                not isinstance(outputs_val, list)
                or len(cast(list[Any], outputs_val)) == 0
            ):
                return JSONResponse(
                    {"error": "outputs array is required when status is completed"},
                    status_code=400,
                )

        with session_factory() as session:
            view = session.get(FileListView, upload_id)
            if view is None:
                return JSONResponse({"error": "upload not found"}, status_code=404)

            event_type = f"processing.{status}"
            payload = json.dumps(body, ensure_ascii=False)
            append_event(
                session,
                stream_id=upload_id,
                stream_type="processing",
                event_type=event_type,
                payload=payload,
            )

        return JSONResponse({"upload_id": upload_id, "status": status})

    return webhook_processing_result


def create_http_app(
    session_factory: sessionmaker[Session],
    tusd_download_base_url: str,
    airflow_client: AirflowClient,
) -> Starlette:
    """Create the Starlette application with file listing routes and SSE."""
    download_base_url = tusd_download_base_url.rstrip("/")
    routes = [
        Route(
            "/files",
            _create_list_files_endpoint(session_factory, download_base_url),
            methods=["GET"],
        ),
        Route(
            "/files/events",
            _create_sse_endpoint(session_factory, download_base_url),
            methods=["GET"],
        ),
        Route(
            "/files/{upload_id}/rerun",
            _create_rerun_endpoint(session_factory, download_base_url, airflow_client),
            methods=["POST"],
        ),
        Route(
            "/webhooks/processing-result",
            _create_webhook_endpoint(session_factory),
            methods=["POST"],
        ),
    ]
    return Starlette(routes=routes)
