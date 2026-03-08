"""Airflow REST API client for triggering DAG runs."""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class DagTriggerPayload:
    upload_id: str
    download_url: str
    filename: str
    filetype: str


class AirflowClient:
    """Client for the Airflow REST API.

    Uses Basic Auth with a base64-encoded token.
    """

    def __init__(self, base_url: str, auth_token: str, dag_id: str) -> None:
        self._dag_id = dag_id
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Basic {auth_token}",
                "Content-Type": "application/json",
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )

    def trigger_dag(self, payload: DagTriggerPayload) -> str:
        """Trigger a DAG run and return the dag_run_id.

        Raises httpx.HTTPStatusError on API errors.
        """
        url = f"/api/v1/dags/{self._dag_id}/dagRuns"
        body: dict[str, Any] = {
            "conf": {
                "upload_id": payload.upload_id,
                "download_url": payload.download_url,
                "filename": payload.filename,
                "filetype": payload.filetype,
            },
        }
        response = self._client.post(url, json=body)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        dag_run_id: str = data["dag_run_id"]
        logger.info(
            "Airflow DAG triggered: dag_id=%s, dag_run_id=%s, upload_id=%s",
            self._dag_id,
            dag_run_id,
            payload.upload_id,
        )
        return dag_run_id
