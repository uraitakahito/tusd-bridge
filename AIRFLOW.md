# Airflow 連携

tusd-bridge はファイルアップロード完了時に後処理を自動実行する仕組みを備えています。
将来的には [Apache Airflow](https://airflow.apache.org/) の DAG を起動し、ファイル変換などの柔軟な後処理パイプラインを実行する設計です。

## 現在の実装状態

| 機能 | 状態 |
|---|---|
| アップロード完了時の後処理トリガー | スタブ実装 (即時成功) |
| Airflow REST API 呼び出し | 未実装 (コメントアウト) |
| Webhook コールバックエンドポイント | 未実装 (TODO) |
| 後処理の再実行 API | 実装済み |

現在の実装ではアップロード完了 (`post-finish`) 時に `processing.triggered` → `processing.completed` のイベントが即座に記録され、Airflow への実際の通信は行われません。

## アーキテクチャ

```
tusd ──[gRPC Hook]──→ tusd-bridge ──[REST API]──→ Airflow
                           ↑                          │
                           └────[Webhook callback]────┘
                           ↑
                    tusd-bridge-client
```

1. tusd がファイルアップロード完了時に `post-finish` フックを gRPC で tusd-bridge に送信
2. tusd-bridge が Airflow REST API (`POST /api/v1/dags/{dag_id}/dagRuns`) で DAG を起動
3. Airflow DAG の最終タスクが tusd-bridge の Webhook エンドポイントに処理結果を POST
4. tusd-bridge-client は再実行 API で後処理を再トリガー可能

## ステータス遷移

```
creating → created → uploading → finishing → uploaded → processing → processed
                                                  ↑          │
                                                  │          ↓
                                                  ←─── failed
                                                  (再実行で processing に戻る)
```

| ステータス | 意味 |
|---|---|
| `uploaded` | アップロード完了。後処理がまだ開始されていない、または開始直前 |
| `processing` | 後処理を実行中 (Airflow DAG が稼働中) |
| `processed` | 後処理が正常に完了 |
| `failed` | 後処理が失敗 |

再実行時は `processed` や `failed` から `processing` に戻ります。`processing.triggered` イベントはステータス優先度チェックをバイパスし、常にステータスを `processing` に上書きします。

## ドメインイベント

後処理に関連するドメインイベントは `stream_type: "processing"` として `domain_events` テーブルに記録されます。

| イベント種別 | 発生タイミング | payload の内容 |
|---|---|---|
| `processing.triggered` | 後処理の開始時 | `upload_id`, `download_url`, `dag_run_id` |
| `processing.completed` | 後処理の正常完了時 | `upload_id`, `dag_run_id`, `result` |
| `processing.failed` | 後処理の失敗時 (将来) | `upload_id`, `dag_run_id`, `error` |

これらのイベントは SSE (`GET /files/events`) でもリアルタイムに通知されます。

## HTTP API

### 後処理の再実行

```bash
curl -X POST http://localhost:8001/files/{upload_id}/rerun
```

レスポンス:

```json
{"upload_id": "abc123...", "status": "triggered"}
```

存在しない `upload_id` を指定した場合は `404` が返ります。

### SSE での後処理イベントの受信

```bash
curl -N http://localhost:8001/files/events
```

後処理に関連する SSE イベントの例:

```
event: file_status_changed
id: 10
data: {"upload_id": "abc123...", "display_status": "processing", ...}

event: file_status_changed
id: 11
data: {"upload_id": "abc123...", "display_status": "processed", ...}
```

## Airflow 連携の実装予定

### 1. Airflow REST API によるDAG トリガー

`src/tusd_bridge/processing.py` 内のコメントアウトされた箇所を実装します。

```python
# POST /api/v1/dags/{dag_id}/dagRuns
# リクエストボディ:
{
    "conf": {
        "upload_id": "abc123...",
        "download_url": "http://localhost:8080/files/abc123..."
    }
}
```

DAG 側では `dag_run.conf['upload_id']` と `dag_run.conf['download_url']` でアップロード情報にアクセスできます。

必要な環境変数:

| 環境変数 | 説明 |
|---|---|
| `AIRFLOW_BASE_URL` | Airflow REST API のベース URL (例: `http://airflow:8080`) |
| `AIRFLOW_AUTH_TOKEN` | Airflow API の認証トークン |
| `AIRFLOW_DAG_ID` | トリガーする DAG の ID |

### 2. Webhook コールバックエンドポイント

Airflow DAG の最終タスクが処理結果を tusd-bridge に通知するためのエンドポイントです。

```
POST /webhooks/processing-result
```

リクエストボディ:

```json
{
    "upload_id": "abc123...",
    "status": "completed",
    "dag_run_id": "manual__2026-03-08T10:00:00+00:00",
    "outputs": [
        {"format": "mp4", "path": "/converted/abc123.mp4", "size": 12345}
    ]
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `upload_id` | string | 対象のアップロード ID |
| `status` | string | `"completed"` または `"failed"` |
| `dag_run_id` | string | Airflow の DAG Run ID |
| `outputs` | array | 変換結果のファイル情報 (成功時) |

`status` に応じて `processing.completed` または `processing.failed` イベントが記録され、`file_list_view` のステータスが更新されます。

### 3. Airflow DAG の実装例

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime
import requests

def convert_file(**context):
    conf = context["dag_run"].conf
    upload_id = conf["upload_id"]
    download_url = conf["download_url"]
    # ファイルをダウンロードして変換処理を行う
    # ...

def notify_result(**context):
    conf = context["dag_run"].conf
    requests.post(
        "http://tusd-bridge:8001/webhooks/processing-result",
        json={
            "upload_id": conf["upload_id"],
            "status": "completed",
            "dag_run_id": context["dag_run"].run_id,
            "outputs": [],
        },
    )

with DAG(
    "file_post_processing",
    schedule=None,  # 外部トリガー専用
    start_date=datetime(2026, 1, 1),
    catchup=False,
) as dag:
    convert = PythonOperator(task_id="convert", python_callable=convert_file)
    notify = PythonOperator(task_id="notify", python_callable=notify_result)
    convert >> notify
```

## 関連ソースファイル

| ファイル | 説明 |
|---|---|
| `src/tusd_bridge/processing.py` | 後処理トリガーロジック (Airflow API 呼び出しのスタブ) |
| `src/tusd_bridge/projector.py` | ステータス遷移の定義 (`STATUS_PRIORITY`, `EVENT_TYPE_TO_DISPLAY_STATUS`) |
| `src/tusd_bridge/server.py` | `post-finish` フック受信時の `trigger_processing` 呼び出し |
| `src/tusd_bridge/http_app.py` | 再実行エンドポイント・Webhook TODO |
