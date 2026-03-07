"""Event sourcing persistence for tusd hook events."""

import json
from datetime import datetime, timezone

from google.protobuf.json_format import MessageToDict
from hook_pb2 import HookRequest  # pyright: ignore[reportMissingModuleSource]
from sqlalchemy.orm import Session

from tusd_bridge.models import UploadEvent, UploadSnapshot

HOOK_TYPE_TO_STATUS: dict[str, str] = {
    "pre-create": "creating",
    "post-create": "created",
    "post-receive": "uploading",
    "pre-finish": "finishing",
    "post-finish": "finished",
    "pre-terminate": "terminating",
    "post-terminate": "terminated",
}


def hook_request_to_json(request: HookRequest) -> str:  # pyright: ignore[reportMissingTypeStubs]
    """Serialize a HookRequest protobuf message to a JSON string."""
    d: dict[str, object] = MessageToDict(request, preserving_proto_field_name=True)  # pyright: ignore[reportUnknownMemberType]
    return json.dumps(d, ensure_ascii=False)


def save_hook_event(session: Session, request: HookRequest) -> UploadEvent:  # pyright: ignore[reportMissingTypeStubs]
    """Persist a hook event and update the upload snapshot in one transaction."""
    upload = request.event.upload
    hook_type: str = request.type

    # 1. Insert event
    event = UploadEvent(
        upload_id=upload.id,
        hook_type=hook_type,
        payload=hook_request_to_json(request),
    )
    session.add(event)
    session.flush()

    # 2. Determine status
    current_status = HOOK_TYPE_TO_STATUS.get(hook_type, hook_type)

    # 3. Upsert snapshot
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
    meta_data = dict(upload.metaData)
    snapshot = session.get(UploadSnapshot, upload.id)
    if snapshot is None:
        snapshot = UploadSnapshot(
            upload_id=upload.id,
            current_status=current_status,
            file_size=upload.size,
            file_offset=upload.offset,
            size_is_deferred=int(upload.sizeIsDeferred),
            is_partial=int(upload.isPartial),
            is_final=int(upload.isFinal),
            filename=meta_data.get("filename"),
            filetype=meta_data.get("filetype"),
            metadata_json=json.dumps(meta_data, ensure_ascii=False)
            if meta_data
            else None,
            storage_json=json.dumps(dict(upload.storage), ensure_ascii=False) or None,
            remote_addr=request.event.httpRequest.remoteAddr,
            last_hook_type=hook_type,
            last_event_id=event.event_id,
            created_at=now,
            updated_at=now,
        )
        session.add(snapshot)
    else:
        snapshot.current_status = current_status
        snapshot.file_size = upload.size
        snapshot.file_offset = upload.offset
        snapshot.size_is_deferred = int(upload.sizeIsDeferred)
        snapshot.is_partial = int(upload.isPartial)
        snapshot.is_final = int(upload.isFinal)
        snapshot.filename = meta_data.get("filename")
        snapshot.filetype = meta_data.get("filetype")
        snapshot.metadata_json = (
            json.dumps(meta_data, ensure_ascii=False) if meta_data else None
        )
        storage = dict(upload.storage)
        snapshot.storage_json = (
            json.dumps(storage, ensure_ascii=False) if storage else None
        )
        snapshot.remote_addr = request.event.httpRequest.remoteAddr
        snapshot.last_hook_type = hook_type
        snapshot.last_event_id = event.event_id
        snapshot.updated_at = now

    session.commit()
    return event
