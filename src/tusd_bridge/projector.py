"""Projector: derive file_list_view from domain events."""

import json
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy.orm import Session

from tusd_bridge.models import DomainEvent, FileListView

STATUS_PRIORITY: dict[str, int] = {
    "creating": 0,
    "created": 1,
    "uploading": 2,
    "finishing": 3,
    "uploaded": 4,
    "terminating": 5,
    "terminated": 6,
}

EVENT_TYPE_TO_DISPLAY_STATUS: dict[str, str] = {
    "hook.pre-create": "creating",
    "hook.post-create": "created",
    #
    # hook.post-receiveを取り扱うには２つ問題があるため、一旦無視をする
    # 1. post-finishより後にpost-receiveが到着することがある
    # 2. post-receiveは大量に発生するため、DBが肥大化する
    # "hook.post-receive": "uploading",
    #
    "hook.pre-finish": "finishing",
    "hook.post-finish": "uploaded",
    "hook.pre-terminate": "terminating",
    "hook.post-terminate": "terminated",
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


def _should_update_status(current: str, new: str) -> bool:
    """Return True if the new status should replace the current one."""
    current_p = STATUS_PRIORITY.get(current, -1)
    new_p = STATUS_PRIORITY.get(new, -1)
    return new_p >= current_p


def project_event(session: Session, event: DomainEvent) -> FileListView | None:
    """Update the file_list_view projection from a single DomainEvent.

    Must be called within the same transaction as the event insert.
    Returns None if the event cannot be projected (e.g. empty stream_id).
    """
    if not event.stream_id:
        return None

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
            if _should_update_status(view.display_status, display_status):
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
        if _should_update_status(view.display_status, display_status):
            view.display_status = display_status
        view.last_event_id = event.event_id
        view.updated_at = now

    return view
