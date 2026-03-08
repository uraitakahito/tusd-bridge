"""Post-upload processing trigger."""

import json
import logging

from sqlalchemy.orm import Session

from tusd_bridge.event_store import append_event

logger = logging.getLogger(__name__)


def trigger_processing(
    session: Session,
    upload_id: str,
    download_url: str,
) -> None:
    """Trigger post-upload processing for the given upload.

    Currently stubbed: records triggered + completed events immediately.
    When Airflow integration is enabled, this will call the Airflow REST API
    and only record the triggered event. The completed event will be recorded
    by the webhook callback endpoint.
    """
    # TODO: Airflow REST API を呼び出して DAG Run を作成する
    # dag_run = await airflow_client.trigger_dag(
    #     dag_id=settings.dag_id,
    #     conf={
    #         "upload_id": upload_id,
    #         "download_url": download_url,
    #     },
    # )
    # dag_run_id = dag_run["dag_run_id"]
    dag_run_id = None

    triggered_payload = json.dumps(
        {
            "upload_id": upload_id,
            "download_url": download_url,
            "dag_run_id": dag_run_id,
        }
    )
    triggered_event, _ = append_event(
        session,
        stream_id=upload_id,
        stream_type="processing",
        event_type="processing.triggered",
        payload=triggered_payload,
    )
    logger.info(
        "Processing triggered: event_id=%d, upload_id=%s",
        triggered_event.event_id,
        upload_id,
    )

    # TODO: Airflow 連携時は以下を削除し、Webhook コールバックで completed を記録する
    completed_payload = json.dumps(
        {
            "upload_id": upload_id,
            "dag_run_id": dag_run_id,
            "result": "stub_success",
        }
    )
    completed_event, _ = append_event(
        session,
        stream_id=upload_id,
        stream_type="processing",
        event_type="processing.completed",
        payload=completed_payload,
    )
    logger.info(
        "Processing completed (stub): event_id=%d, upload_id=%s",
        completed_event.event_id,
        upload_id,
    )
