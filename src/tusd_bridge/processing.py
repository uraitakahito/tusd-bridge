"""Post-upload processing trigger."""

import json
import logging

from sqlalchemy.orm import Session

from tusd_bridge.airflow_client import AirflowClient
from tusd_bridge.event_store import append_event

logger = logging.getLogger(__name__)


def trigger_processing(
    session: Session,
    upload_id: str,
    download_url: str,
    airflow_client: AirflowClient,
) -> None:
    """Trigger post-upload processing by calling the Airflow REST API.

    Records a processing.triggered event on success, or a processing.failed
    event if the Airflow API call fails.
    """
    try:
        dag_run_id = airflow_client.trigger_dag(upload_id, download_url)
    except Exception:
        logger.exception("Failed to trigger Airflow DAG for upload_id=%s", upload_id)
        failed_payload = json.dumps(
            {
                "upload_id": upload_id,
                "download_url": download_url,
                "error": "Failed to trigger Airflow DAG",
            }
        )
        failed_event, _ = append_event(
            session,
            stream_id=upload_id,
            stream_type="processing",
            event_type="processing.failed",
            payload=failed_payload,
        )
        logger.info(
            "Processing failed: event_id=%d, upload_id=%s",
            failed_event.event_id,
            upload_id,
        )
        return

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
        "Processing triggered: event_id=%d, upload_id=%s, dag_run_id=%s",
        triggered_event.event_id,
        upload_id,
        dag_run_id,
    )
