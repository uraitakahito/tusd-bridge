import asyncio
import logging

import typer
import uvicorn
from grpclib.server import Server, Stream
from hook_grpc import HookHandlerBase
from hook_pb2 import HookRequest, HookResponse
from sqlalchemy.orm import Session, sessionmaker

from tusd_bridge.database import get_engine
from tusd_bridge.event_bus import EventBus, SSEEvent
from tusd_bridge.event_store import append_hook_event
from tusd_bridge.http_app import create_http_app, view_to_dict

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
        event_bus: EventBus,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus

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

        with self._session_factory() as session:
            event, view = append_hook_event(session, request)
            sse_event = SSEEvent(event_id=event.event_id, data=view_to_dict(view))
            logger.info(
                "Saved event_id=%d for stream_id=%s", event.event_id, event.stream_id
            )

        await self._event_bus.publish(sse_event)

        await stream.send_message(HookResponse())


async def _serve(host: str, grpc_port: int, http_port: int) -> None:
    engine = get_engine()
    session_factory = sessionmaker(bind=engine)
    event_bus = EventBus()

    grpc_server = Server([HookHandler(session_factory, event_bus)])
    await grpc_server.start(host, grpc_port)
    logger.info("gRPC Hook server listening on %s:%d", host, grpc_port)

    http_app = create_http_app(session_factory, event_bus)
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
