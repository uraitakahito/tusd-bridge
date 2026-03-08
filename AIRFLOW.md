# Airflow 連携

tusd-bridge はファイルアップロード完了時に [Apache Airflow](https://airflow.apache.org/) の DAG を起動し、ファイル変換などの柔軟な後処理パイプラインを実行します。
Airflow 環境は [tusd-bridge-airflow](https://github.com/uraitakahito/tusd-bridge-airflow) で構築します。

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

## 環境変数

| 環境変数 | 説明 | デフォルト値 |
|---|---|---|
| `AIRFLOW_BASE_URL` | Airflow REST API のベース URL (必須) | なし |
| `AIRFLOW_AUTH_TOKEN` | Airflow API の認証トークン (Basic Auth, base64 エンコード済み, 必須) | なし |
| `AIRFLOW_DAG_ID` | トリガーする DAG の ID | `file_post_processing` |

`AIRFLOW_AUTH_TOKEN` は `echo -n "admin:admin" | base64` のように生成します。

## 起動例

```bash
TUSD_DOWNLOAD_BASE_URL=http://localhost:8080/files/ \
AIRFLOW_BASE_URL=http://host.docker.internal:8082 \
AIRFLOW_AUTH_TOKEN=$(echo -n "admin:admin" | base64) \
AIRFLOW_DAG_ID=file_post_processing \
uv run tusd-bridge
```

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
| `failed` | 後処理が失敗 (Airflow API 呼び出し失敗、または DAG が失敗を報告) |

再実行時は `processed` や `failed` から `processing` に戻ります。`processing.triggered` イベントはステータス優先度チェックをバイパスし、常にステータスを `processing` に上書きします。

## ドメインイベント

後処理に関連するドメインイベントは `stream_type: "processing"` として `domain_events` テーブルに記録されます。

| イベント種別 | 発生タイミング | payload の内容 |
|---|---|---|
| `processing.triggered` | Airflow DAG トリガー成功時 | `upload_id`, `download_url`, `filename`, `filetype`, `dag_run_id` |
| `processing.completed` | Webhook で正常完了を受信時 | Webhook リクエストボディ全体 |
| `processing.failed` | Airflow API 呼び出し失敗時、または Webhook で失敗を受信時 | エラー情報 (`upload_id`, `download_url`, `filename`, `filetype`, `error`) または Webhook リクエストボディ全体 |

これらのイベントは SSE (`GET /files/events`) でもリアルタイムに通知されます。

## HTTP API

### DAG トリガー (Airflow REST API)

アップロード完了 (`post-finish`) 時に自動で呼ばれます。

```
POST /api/v1/dags/{dag_id}/dagRuns
```

リクエストボディ:

```json
{
    "conf": {
        "upload_id": "abc123...",
        "download_url": "http://localhost:8080/files/abc123...",
        "filename": "video.mov",
        "filetype": "video/quicktime"
    }
}
```

DAG 側では `dag_run.conf['upload_id']`、`dag_run.conf['download_url']`、`dag_run.conf['filename']`、`dag_run.conf['filetype']` でアップロード情報にアクセスできます。`filename` は必須、`filetype` はクライアントが指定しなかった場合 `null` になります。

### Webhook コールバックエンドポイント

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
        {
            "filename": "abc123.mp4",
            "filetype": "video/mp4",
            "url": "http://localhost:8080/converted/abc123.mp4",
            "size": 12345
        }
    ]
}
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `upload_id` | string | Yes | 対象のアップロード ID |
| `status` | string | Yes | `"completed"` または `"failed"` |
| `dag_run_id` | string | No | Airflow の DAG Run ID |
| `outputs` | array | Yes (`status` が `completed` の場合) | 変換結果のファイル情報 |

`outputs` 配列の各要素:

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `filename` | string | Yes | ファイル名 |
| `filetype` | string | Yes | MIME タイプ |
| `url` | string | Yes | ダウンロード URL |
| `size` | int | Yes | ファイルサイズ (bytes) |

`status` に応じて `processing.completed` または `processing.failed` イベントが記録され、`file_list_view` のステータスが更新されます。

レスポンス:

```json
{"upload_id": "abc123...", "status": "completed"}
```

エラーレスポンス:
- `400`: `upload_id` 未指定、または `status` が不正
- `404`: 指定した `upload_id` が存在しない

### 後処理の再実行

```bash
curl -X POST http://localhost:8001/files/{upload_id}/rerun
```

レスポンス:

```json
{"upload_id": "abc123...", "status": "triggered"}
```

エラーレスポンス:
- `400`: `filename` が未設定のアップロードに対して再実行を要求した場合
- `404`: 指定した `upload_id` が存在しない

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

## 関連ソースファイル

| ファイル | 説明 |
|---|---|
| `src/tusd_bridge/airflow_client.py` | Airflow REST API クライアント (`AirflowClient`) |
| `src/tusd_bridge/processing.py` | 後処理トリガーロジック (DAG 起動、失敗時のイベント記録) |
| `src/tusd_bridge/projector.py` | ステータス遷移の定義 (`STATUS_PRIORITY`, `EVENT_TYPE_TO_DISPLAY_STATUS`) |
| `src/tusd_bridge/server.py` | `post-finish` フック受信時の `trigger_processing` 呼び出し、環境変数定義 |
| `src/tusd_bridge/http_app.py` | 再実行エンドポイント、Webhook コールバックエンドポイント |
