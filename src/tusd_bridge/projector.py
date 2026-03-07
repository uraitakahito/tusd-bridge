"""Projector: derive file_list_view from domain events."""

import json
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy.orm import Session

from tusd_bridge.models import DomainEvent, FileListView

EVENT_TYPE_TO_DISPLAY_STATUS: dict[str, str] = {
    "hook.pre-create": "creating",
    "hook.post-create": "created",
    "hook.post-receive": "uploading",
    "hook.pre-finish": "finishing",
    "hook.post-finish": "uploaded",
    "hook.pre-terminate": "terminating",
    "hook.post-terminate": "terminated",
    # Future conversion statuses:
    # "conversion.started": "converting",
    # "conversion.progress": "converting",
    # "conversion.completed": "ready",
    # "conversion.failed": "conv_failed",
}


def _get_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    val = data.get(key)
    if isinstance(val, dict):
        return cast(dict[str, Any], val)
    return {}


def _extract_upload_fields(
    payload_str: str,
) -> tuple[
    dict[str, Any],  # upload
    dict[str, Any],  # http_request
]:
    payload: dict[str, Any] = json.loads(payload_str)
    event_data = _get_dict(payload, "event")
    upload = _get_dict(event_data, "upload")
    http_request = _get_dict(event_data, "httpRequest")
    return upload, http_request


def _safe_int(val: Any) -> int | None:
    if isinstance(val, int):
        return val
    if isinstance(val, str) and val.isdigit():
        return int(val)
    return None


def project_event(session: Session, event: DomainEvent) -> FileListView:
    """Update the file_list_view projection from a single DomainEvent.

    Must be called within the same transaction as the event insert.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
    display_status = EVENT_TYPE_TO_DISPLAY_STATUS.get(
        event.event_type, event.event_type
    )

    view = session.get(FileListView, event.stream_id)

    if event.stream_type == "upload":
        upload, http_request = _extract_upload_fields(event.payload)
        meta_data = _get_dict(upload, "metaData")
        storage = _get_dict(upload, "storage")

        file_size = _safe_int(upload.get("size"))
        file_offset = _safe_int(upload.get("offset"))

        filename_val = meta_data.get("filename")
        filename = str(filename_val) if filename_val is not None else None
        filetype_val = meta_data.get("filetype")
        filetype = str(filetype_val) if filetype_val is not None else None
        remote_addr_val = http_request.get("remoteAddr")
        remote_addr = str(remote_addr_val) if remote_addr_val is not None else None

        metadata_json = json.dumps(meta_data, ensure_ascii=False) if meta_data else None
        storage_json = json.dumps(storage, ensure_ascii=False) if storage else None

        if view is None:
            view = FileListView(
                upload_id=event.stream_id,
                display_status=display_status,
                file_size=file_size,
                file_offset=file_offset,
                filename=filename,
                filetype=filetype,
                metadata_json=metadata_json,
                storage_json=storage_json,
                remote_addr=remote_addr,
                last_event_id=event.event_id,
                created_at=now,
                updated_at=now,
            )
            session.add(view)
        else:
            view.display_status = display_status
            view.file_size = file_size
            view.file_offset = file_offset
            view.filename = filename
            view.filetype = filetype
            view.metadata_json = metadata_json
            view.storage_json = storage_json
            view.remote_addr = remote_addr
            view.last_event_id = event.event_id
            view.updated_at = now
    elif view is not None:
        # Future: handle "conversion" stream_type
        view.display_status = display_status
        view.last_event_id = event.event_id
        view.updated_at = now

    assert view is not None
    return view
