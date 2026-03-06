import asyncio
import logging

import typer
from grpclib.server import Server, Stream
from hook_grpc import HookHandlerBase
from hook_pb2 import HookRequest, HookResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = typer.Typer()


class HookHandler(HookHandlerBase):
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

        await stream.send_message(HookResponse())


async def _serve(host: str, port: int) -> None:
    server = Server([HookHandler()])
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
