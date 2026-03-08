import asyncio
import logging

import typer
import uvicorn
from grpclib.server import Server, Stream
from hook_grpc import HookHandlerBase
from hook_pb2 import HookRequest, HookResponse, HTTPResponse
from sqlalchemy.orm import Session, sessionmaker

from tusd_bridge.airflow_client import AirflowClient, DagTriggerPayload
from tusd_bridge.database import get_engine
from tusd_bridge.event_store import append_hook_event, update_upload_progress
from tusd_bridge.http_app import create_http_app
from tusd_bridge.processing import trigger_processing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = typer.Typer()


class HookHandler(HookHandlerBase):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        tusd_download_base_url: str,
        airflow_client: AirflowClient,
    ) -> None:
        self._session_factory = session_factory
        self._tusd_download_base_url = tusd_download_base_url
        self._airflow_client = airflow_client

    async def InvokeHook(self, stream: Stream[HookRequest, HookResponse]) -> None:
        request = await stream.recv_message()
        if request is None:
            await stream.send_message(HookResponse())
            return

        upload = request.event.upload
        http_req = request.event.httpRequest

        # pre-create 時にメタデータの必須フィールドを検証
        if request.type == "pre-create":
            metadata = dict(upload.metaData)
            missing: list[str] = []
            if not metadata.get("filename"):
                missing.append("filename")
            if not metadata.get("filetype"):
                missing.append("filetype")
            if upload.size <= 0:
                missing.append("size (must be > 0)")
            if missing:
                logger.warning(
                    "Rejecting upload: missing required fields: %s",
                    ", ".join(missing),
                )
                await stream.send_message(
                    HookResponse(
                        rejectUpload=True,
                        httpResponse=HTTPResponse(
                            statusCode=400,
                            body=f"Missing required fields: {', '.join(missing)}",
                        ),
                    )
                )
                return

        # post-receiveは大量に届くため、処理を切り分ける
        if request.type == "post-receive":
            logger.debug(
                "Hook: %s | Upload: %s | Offset: %d",
                request.type,
                upload.id,
                upload.offset,
            )
            with self._session_factory() as session:
                update_upload_progress(session, upload.id, upload.offset)
        else:
            logger.info(
                "Hook: %s | Upload: %s | Size: %d | Offset: %d | Method: %s %s | Remote: %s | MetaData: %s | Storage: %s",
                request.type,
                upload.id,
                upload.size,
                upload.offset,
                http_req.method,
                http_req.uri,
                http_req.remoteAddr,
                dict(upload.metaData),
                dict(upload.storage),
            )
            with self._session_factory() as session:
                event, _view = append_hook_event(session, request)
                logger.info(
                    "Saved event_id=%d for stream_id=%s",
                    event.event_id,
                    event.stream_id,
                )
                if request.type == "post-finish":
                    download_url = f"{self._tusd_download_base_url}/{upload.id}"
                    metadata = dict(upload.metaData)
                    payload = DagTriggerPayload(
                        upload_id=upload.id,
                        download_url=download_url,
                        filename=metadata["filename"],
                        filetype=metadata["filetype"],
                    )
                    trigger_processing(session, payload, self._airflow_client)

        await stream.send_message(HookResponse())


async def _serve(
    host: str,
    grpc_port: int,
    http_port: int,
    tusd_download_base_url: str,
    airflow_client: AirflowClient,
) -> None:
    engine = get_engine()
    session_factory = sessionmaker(bind=engine)

    download_base_url = tusd_download_base_url.rstrip("/")
    grpc_server = Server(
        [HookHandler(session_factory, download_base_url, airflow_client)]
    )
    await grpc_server.start(host, grpc_port)
    logger.info("gRPC Hook server listening on %s:%d", host, grpc_port)

    http_app = create_http_app(session_factory, tusd_download_base_url, airflow_client)
    config = uvicorn.Config(http_app, host=host, port=http_port, log_level="info")
    http_server = uvicorn.Server(config)
    logger.info("HTTP server listening on %s:%d", host, http_port)

    await asyncio.gather(
        grpc_server.wait_closed(),
        http_server.serve(),
    )


@app.command()
def main(
    host: str = typer.Option(
        "0.0.0.0", envvar="TUSD_BRIDGE_HOST", help="Bind address."
    ),
    grpc_port: int = typer.Option(
        8000, envvar="TUSD_BRIDGE_GRPC_PORT", help="gRPC listen port."
    ),
    http_port: int = typer.Option(
        8001, envvar="TUSD_BRIDGE_HTTP_PORT", help="HTTP listen port."
    ),
    tusd_download_base_url: str = typer.Option(
        ...,
        envvar="TUSD_DOWNLOAD_BASE_URL",
        help="エンドユーザーがブラウザからアクセスする tusd の公開ベース URL (例: http://localhost:8080/files/)",
    ),
    airflow_base_url: str = typer.Option(
        ...,
        envvar="AIRFLOW_BASE_URL",
        help="Airflow REST API のベース URL (例: http://host.docker.internal:8082)",
    ),
    airflow_auth_token: str = typer.Option(
        ...,
        envvar="AIRFLOW_AUTH_TOKEN",
        help="Airflow API の認証トークン (Basic Auth, base64 エンコード済み)",
    ),
    airflow_dag_id: str = typer.Option(
        "file_post_processing",
        envvar="AIRFLOW_DAG_ID",
        help="トリガーする DAG の ID",
    ),
) -> None:
    airflow_client = AirflowClient(airflow_base_url, airflow_auth_token, airflow_dag_id)
    asyncio.run(
        _serve(host, grpc_port, http_port, tusd_download_base_url, airflow_client)
    )


if __name__ == "__main__":
    app()
