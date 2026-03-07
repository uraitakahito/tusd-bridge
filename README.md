# tusd-bridge

[tusd](https://github.com/tus/tusd)の[gRPC Hooks](https://tus.github.io/tusd/advanced-topics/hooks/#grpc-hooks)から呼び出され、アップロードイベントやファイルのメタ情報をDBに保存します。
ファイル一覧のREST APIとSSE (Server-Sent Events) によるリアルタイム通知を提供します。
ファイルをアップロードするクライアントのサンプルは[hello-tus-js-client](https://github.com/uraitakahito/hello-tus-js-client)を想定しています。

## セットアップ

```bash
uv sync
uv run inv generate
uv run inv db-upgrade
```

### ホストOSでのtusd Dockerコンテナの起動

```bash
docker run -d --init --rm -p 8080:8080 --name tusd-container docker.io/tusproject/tusd:latest -host=0.0.0.0 -port=8080 -hooks-grpc=host.docker.internal:8000
```

## サーバーの起動

次のコマンドにより、gRPCサーバーとHTTP APIサーバーが同時に起動します。

```bash
TUSD_DOWNLOAD_BASE_URL=http://localhost:8080/files/ uv run inv run
```

or

```bash
TUSD_DOWNLOAD_BASE_URL=http://localhost:8080/files/ uv run tusd-bridge
```

### 環境変数

| 環境変数 | 説明 | デフォルト値 |
|---|---|---|
| `TUSD_DOWNLOAD_BASE_URL` | エンドユーザーがブラウザからアクセスする tusd の公開ベース URL (必須) | なし |
| `TUSD_BRIDGE_HOST` | バインドアドレス | `0.0.0.0` |
| `TUSD_BRIDGE_GRPC_PORT` | gRPC リッスンポート | `8000` |
| `TUSD_BRIDGE_HTTP_PORT` | HTTP リッスンポート | `8001` |

## HTTP APIによるデバッグ

### ファイル一覧の取得

```bash
curl http://localhost:8001/files
curl http://localhost:8001/files?status=uploaded,converting&limit=10
```

### SSE (Server-Sent Events) によるリアルタイム通知

```bash
curl -N http://localhost:8001/files/events
```

REST APIで取得した `last_event_id` を `cursor` クエリパラメータに指定すると、それ以降のイベントのみを受信できます。ページ読み込み時にREST APIでファイル一覧を取得した後、SSEでリアルタイム更新を受け取る使い方を想定しています。

```bash
# 1. REST APIでファイル一覧と last_event_id を取得
curl http://localhost:8001/files
# → { "files": [...], "last_event_id": 42, ... }

# 2. SSEで last_event_id 以降のイベントのみ受信
curl -N http://localhost:8001/files/events?cursor=42
```

再接続時はブラウザの `EventSource` APIが `Last-Event-ID` ヘッダを自動送信するため、イベントの欠落なく再開されます。`Last-Event-ID` ヘッダは `cursor` クエリパラメータより優先されます。

```bash
curl -N -H "Last-Event-ID: 42" http://localhost:8001/files/events
```

## Starting the development server with Docker

If you develop inside a Docker container, run the following commands and read the documentation at the top of the Dockerfile to set up your development environment.

```console
% curl -L -O https://raw.githubusercontent.com/uraitakahito/hello_python_uv/refs/tags/1.1.0/Dockerfile
% curl -L -O https://raw.githubusercontent.com/uraitakahito/hello_python_uv/refs/tags/1.1.0/docker-entrypoint.sh
% chmod 755 docker-entrypoint.sh
```

ただし、gRPCサーバーとHTTPサーバーのポートを公開する必要があるので、コンテナの起動コマンドは次のようになります。

```console
% PROJECT=$(basename `pwd`) && docker image build -t $PROJECT-image . --build-arg user_id=`id -u` --build-arg group_id=`id -g`
% docker container run -d --rm --init -p 8000:8000 -p 8001:8001 -v /run/host-services/ssh-auth.sock:/run/host-services/ssh-auth.sock -e SSH_AUTH_SOCK=/run/host-services/ssh-auth.sock -e GH_TOKEN=$(gh auth token) --mount type=bind,src=`pwd`,dst=/app --mount type=volume,source=$PROJECT-zsh-history,target=/zsh-volume --name $PROJECT-container $PROJECT-image
```
