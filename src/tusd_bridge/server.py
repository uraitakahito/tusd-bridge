import asyncio
import logging

import typer
import uvicorn
from grpclib.server import Server, Stream
from hook_grpc import HookHandlerBase
from hook_pb2 import HookRequest, HookResponse
from sqlalchemy.orm import Session, sessionmaker

from tusd_bridge.database import get_engine
from tusd_bridge.event_store import append_hook_event
from tusd_bridge.http_app import create_http_app

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
    ) -> None:
        self._session_factory = session_factory

    async def InvokeHook(self, stream: Stream[HookRequest, HookResponse]) -> None:
        request = await stream.recv_message()
        if request is None:
            await stream.send_message(HookResponse())
            return

        upload = request.event.upload
        http_req = request.event.httpRequest

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

        # DB への書き込みだけを行う。SSE クライアントへの配信は
        # SSE エンドポイントが domain_events テーブルをポーリングして検出するため、
        # ここでの明示的な通知 (EventBus publish) は不要。
        with self._session_factory() as session:
            event, _view = append_hook_event(session, request)
            logger.info(
                "Saved event_id=%d for stream_id=%s", event.event_id, event.stream_id
            )

        await stream.send_message(HookResponse())


async def _serve(host: str, grpc_port: int, http_port: int) -> None:
    engine = get_engine()
    session_factory = sessionmaker(bind=engine)

    grpc_server = Server([HookHandler(session_factory)])
    await grpc_server.start(host, grpc_port)
    logger.info("gRPC Hook server listening on %s:%d", host, grpc_port)

    http_app = create_http_app(session_factory)
    config = uvicorn.Config(http_app, host=host, port=http_port, log_level="info")
    http_server = uvicorn.Server(config)
    logger.info("HTTP server listening on %s:%d", host, http_port)

    await asyncio.gather(
        grpc_server.wait_closed(),
        http_server.serve(),
    )


@app.command()
def main(
    host: str = typer.Option("0.0.0.0", help="Bind address."),
    grpc_port: int = typer.Option(8000, help="gRPC listen port."),
    http_port: int = typer.Option(8001, help="HTTP listen port."),
) -> None:
    asyncio.run(_serve(host, grpc_port, http_port))


if __name__ == "__main__":
    app()
