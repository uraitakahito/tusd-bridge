import asyncio
import logging

import typer
from grpclib.server import Server, Stream
from hook_grpc import HookHandlerBase
from hook_pb2 import HookRequest, HookResponse
from sqlalchemy.orm import Session, sessionmaker

from tusd_bridge.database import get_engine
from tusd_bridge.event_store import save_hook_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = typer.Typer()


class HookHandler(HookHandlerBase):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
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

        with self._session_factory() as session:
            event = save_hook_event(session, request)
            logger.info(
                "Saved event_id=%d for upload_id=%s", event.event_id, event.upload_id
            )

        await stream.send_message(HookResponse())


async def _serve(host: str, port: int) -> None:
    engine = get_engine()
    session_factory = sessionmaker(bind=engine)
    server = Server([HookHandler(session_factory)])
    await server.start(host, port)
    logger.info("gRPC Hook server listening on %s:%d", host, port)
    await server.wait_closed()


@app.command()
def main(
    host: str = typer.Option("0.0.0.0", help="Bind address."),
    port: int = typer.Option(8000, help="Listen port."),
) -> None:
    asyncio.run(_serve(host, port))


if __name__ == "__main__":
    app()
