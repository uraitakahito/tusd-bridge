# tusd-bridge

- [tusd](https://github.com/tus/tusd)の[gRPC Hooks](https://tus.github.io/tusd/advanced-topics/hooks/#grpc-hooks)から呼び出され、アップロードイベントやファイルのメタ情報をDBに保存します。
- ファイル一覧のREST APIとSSE (Server-Sent Events) によるリアルタイム通知を提供します。
- ファイルをアップロードするクライアントのサンプルは[tusd-bridge-client](https://github.com/uraitakahito/tusd-bridge-client)を想定しています。
- アップロード完了時に Airflow DAGを起動してファイル変換などの後処理パイプラインを実行します。詳細は [AIRFLOW.md](AIRFLOW.md) を参照してください。

## Docker Compose によるバックエンド一括起動

`docker-compose.yml` により、以下のサービスが一括で起動します:

| サービス | 説明 | ホストポート |
|---|---|---|
| `tusd-bridge` | 開発用コンテナ (gRPC + HTTP サーバー) | 8000, 8001 |
| `tusd` | TUS プロトコル対応ファイルアップロードサーバー | 8080 |
| `airflow-webserver` | Airflow Web UI | 8082 |
| `airflow-scheduler` | Airflow スケジューラー | - |
| `postgres` | Airflow 用 PostgreSQL | - |
| `minio` | S3 互換オブジェクトストレージ | 9000, 9001 |

### 開発用 Dockerfile の取得

```console
% curl -L -O https://raw.githubusercontent.com/uraitakahito/hello_python_uv/refs/tags/1.1.0/Dockerfile
% curl -L -O https://raw.githubusercontent.com/uraitakahito/hello_python_uv/refs/tags/1.1.0/docker-entrypoint.sh
% chmod 755 docker-entrypoint.sh
```

### 起動

コンテナ内で `gh` コマンドを使うために `GH_TOKEN` を渡します:

```bash
GH_TOKEN=$(gh auth token) docker compose up -d
```

### tusd-bridge コンテナでの作業

コンテナにアタッチするか、VS Code の「Dev Containers: Attach to Running Container」でコンテナに接続して `/app` ディレクトリを開きます。

コンテナ内で初回セットアップ:

```bash
uv sync
uv run inv generate
uv run inv db-upgrade
```

サーバーの起動:

```bash
TUSD_DOWNLOAD_BASE_URL=http://localhost:8080/files/ AIRFLOW_BASE_URL=http://airflow-webserver:8080 AIRFLOW_AUTH_TOKEN=$(echo -n "admin:admin" | base64) AIRFLOW_DAG_ID=file_post_processing uv run inv run
```

### フロントエンド (tusd-bridge-client)

フロントエンドの Nginx は別途 [tusd-bridge-client](https://github.com/uraitakahito/tusd-bridge-client) リポジトリで起動します。

### 環境変数

| 環境変数 | 説明 | デフォルト値 |
|---|---|---|
| `TUSD_DOWNLOAD_BASE_URL` | エンドユーザーがブラウザからアクセスする tusd の公開ベース URL (必須) | なし |
| `TUSD_BRIDGE_HOST` | バインドアドレス | `0.0.0.0` |
| `TUSD_BRIDGE_GRPC_PORT` | gRPC リッスンポート | `8000` |
| `TUSD_BRIDGE_HTTP_PORT` | HTTP リッスンポート | `8001` |
| `AIRFLOW_BASE_URL` | Airflow REST API のベース URL (必須) | なし |
| `AIRFLOW_AUTH_TOKEN` | Airflow API の認証トークン (Basic Auth, base64 エンコード済み, 必須) | なし |
| `AIRFLOW_DAG_ID` | トリガーする DAG の ID | `file_post_processing` |

## HTTP APIによるデバッグ

### ファイル一覧の取得

```bash
curl http://localhost:8001/files
curl http://localhost:8001/files?status=uploaded,processing&limit=10
```

### 後処理の再実行

```bash
curl -X POST http://localhost:8001/files/{upload_id}/rerun
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
