import json
import logging
import os
import tempfile
from datetime import datetime

import requests
import trimesh
from airflow import DAG
from airflow.operators.python import BranchPythonOperator, PythonOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.http.hooks.http import HttpHook

logger = logging.getLogger(__name__)

ALLOWED_EXTENSION = ".glb"
MINIO_CONN_ID = "minio"
MINIO_BUCKET = "conversions"
MINIO_EXTERNAL_ENDPOINT = "http://localhost:9000"
PRESIGNED_URL_EXPIRY = 86400  # 24時間


def validate_file(**context):
    conf = context["dag_run"].conf
    filename = conf["filename"]
    if filename.lower().endswith(ALLOWED_EXTENSION):
        logger.info(f"Validation passed: {filename}")
        return "convert_file"
    logger.warning(f"Validation failed: {filename} (expected {ALLOWED_EXTENSION})")
    return "reject_file"


def convert_file(**context):
    conf = context["dag_run"].conf
    upload_id = conf["upload_id"]
    download_url = conf["download_url"]
    filename = conf["filename"]
    filetype = conf["filetype"]
    logger.info(
        f"Processing file: upload_id={upload_id}, filename={filename}, "
        f"filetype={filetype}, download_url={download_url}"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        # GLB をダウンロード
        # download_url はクライアント向け (localhost) のため、コンテナ内では tusd サービス名に置き換える
        internal_url = download_url.replace(
            "://localhost:8080", "://tusd:8080"
        )
        glb_path = os.path.join(tmpdir, f"{upload_id}.glb")
        resp = requests.get(internal_url, timeout=300)
        resp.raise_for_status()
        with open(glb_path, "wb") as f:
            f.write(resp.content)
        logger.info(f"Downloaded GLB: {os.path.getsize(glb_path)} bytes")

        # STL に変換
        stl_path = os.path.join(tmpdir, f"{upload_id}.stl")
        mesh = trimesh.load(glb_path, force="mesh")
        mesh.export(stl_path)
        stl_size = os.path.getsize(stl_path)
        logger.info(f"Converted to STL: {stl_size} bytes")

        # MinIO にアップロード
        s3_key = f"{upload_id}.stl"
        s3 = S3Hook(aws_conn_id=MINIO_CONN_ID)
        s3.load_file(
            filename=stl_path,
            key=s3_key,
            bucket_name=MINIO_BUCKET,
            replace=True,
        )
        logger.info(f"Uploaded to MinIO: {MINIO_BUCKET}/{s3_key}")

        # Presigned URL を生成
        # S3Hook は Connection の内部エンドポイント (minio:9000) で URL を生成するため、
        # クライアントからアクセス可能な外部エンドポイントに置き換える
        presigned_url = s3.generate_presigned_url(
            client_method="get_object",
            params={"Bucket": MINIO_BUCKET, "Key": s3_key},
            expires_in=PRESIGNED_URL_EXPIRY,
        )
        presigned_url = presigned_url.replace(
            "http://minio:9000", MINIO_EXTERNAL_ENDPOINT
        )
        logger.info(f"Presigned URL generated (expires in {PRESIGNED_URL_EXPIRY}s)")

    # 後続タスクに出力情報を渡す
    stl_filename = os.path.splitext(filename)[0] + ".stl"
    context["ti"].xcom_push(
        key="outputs",
        value=[
            {
                "filename": stl_filename,
                "filetype": "model/stl",
                "url": presigned_url,
                "size": stl_size,
            }
        ],
    )


def reject_file(**context):
    conf = context["dag_run"].conf
    payload = {
        "upload_id": conf["upload_id"],
        "filename": conf["filename"],
        "filetype": conf["filetype"],
        "status": "failed",
        "dag_run_id": context["dag_run"].run_id,
        "error": f"Unsupported file format. Only {ALLOWED_EXTENSION} files are accepted.",
    }
    logger.error(f"File rejected: {payload}")
    hook = HttpHook(method="POST", http_conn_id="tusd_bridge")
    response = hook.run(
        endpoint="/webhooks/processing-result",
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    logger.info(f"Rejection notification response: {response.status_code}")


def notify_result(**context):
    conf = context["dag_run"].conf
    outputs = context["ti"].xcom_pull(task_ids="convert_file", key="outputs") or []
    payload = {
        "upload_id": conf["upload_id"],
        "filename": conf["filename"],
        "filetype": conf["filetype"],
        "status": "completed",
        "dag_run_id": context["dag_run"].run_id,
        "outputs": outputs,
    }
    logger.info(f"Notifying tusd-bridge: {payload}")
    hook = HttpHook(method="POST", http_conn_id="tusd_bridge")
    response = hook.run(
        endpoint="/webhooks/processing-result",
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    logger.info(f"Notification response: {response.status_code}")


with DAG(
    "file_post_processing",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["tusd-bridge"],
) as dag:
    validate = BranchPythonOperator(
        task_id="validate_file", python_callable=validate_file
    )
    convert = PythonOperator(task_id="convert_file", python_callable=convert_file)
    reject = PythonOperator(task_id="reject_file", python_callable=reject_file)
    notify = PythonOperator(task_id="notify_result", python_callable=notify_result)

    validate >> [convert, reject]
    convert >> notify
