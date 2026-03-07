"""invoke タスク定義"""

from invoke.context import Context
from invoke.tasks import task  # pyright: ignore[reportUnknownVariableType]

# grpclib の protoc プラグインは絶対インポート (import hook_pb2) を生成し、
# この挙動を設定で変更する手段はない。
# サブパッケージ内に生成すると絶対インポートが解決できないため、
# src/ 直下にトップレベルモジュールとして生成することで、
# sed による後処理なしにインポートを正しく解決させる。
GENERATED_FILES = [
    "src/hook_pb2.py",
    "src/hook_pb2.pyi",
    "src/hook_grpc.py",
]


@task
def generate(c: Context) -> None:
    """protoc でコード生成."""
    c.run(
        "uv run python -m grpc_tools.protoc"
        " --proto_path=proto/"
        " --python_out=src/"
        " --mypy_out=src/"
        " --grpclib_python_out=src/"
        " hook.proto"
    )


@task(pre=[generate])  # pyright: ignore[reportUntypedFunctionDecorator]
def run(c: Context) -> None:
    """generate 後にサーバー起動."""
    c.run(
        "uv run tusd-bridge --tusd-base-url http://localhost:8080/files/",
        pty=True,
    )


@task
def clean(c: Context) -> None:
    """生成コードを削除."""
    for f in GENERATED_FILES:
        c.run(f"rm -f {f}")


@task
def lint(c: Context) -> None:
    """ruff check + ruff format --check + pyright."""
    c.run("uv run ruff check")
    c.run("uv run ruff format --check --diff")
    c.run("uv run pyright")


@task
def format(c: Context) -> None:  # noqa: A001
    """ruff check --fix + ruff format."""
    c.run("uv run ruff check --fix")
    c.run("uv run ruff format")


@task
def db_upgrade(c: Context) -> None:
    """alembic upgrade head."""
    c.run("uv run alembic upgrade head")


@task
def db_downgrade(c: Context) -> None:
    """alembic downgrade -1."""
    c.run("uv run alembic downgrade -1")


@task
def db_reset(c: Context) -> None:
    """DB を削除して再作成."""
    c.run("rm -f data/tusd_bridge.db")
    c.run("uv run alembic upgrade head")


@task
def db_revision(c: Context, message: str = "") -> None:
    """alembic revision --autogenerate."""
    if not message:
        raise ValueError("message is required: inv db-revision --message '...'")
    c.run(f"uv run alembic revision --autogenerate -m '{message}'")
