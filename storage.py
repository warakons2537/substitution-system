from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

APP_DIR = Path(__file__).resolve().parent
LOCAL_UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", APP_DIR / "uploads"))


def _safe_name(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^0-9A-Za-zก-๙._ -]+", "_", name)
    return name[:180] or "document.bin"


def storage_mode() -> str:
    return "S3" if os.getenv("S3_BUCKET") else "local"


def storage_status() -> dict:
    if os.getenv("S3_BUCKET"):
        return {
            "mode": "S3",
            "bucket": os.getenv("S3_BUCKET", ""),
            "endpoint": os.getenv("S3_ENDPOINT_URL", "AWS/default"),
            "message": "S3-compatible Storage พร้อมใช้งาน (รองรับ Supabase Storage)",
        }
    return {
        "mode": "local",
        "path": str(LOCAL_UPLOAD_DIR),
        "message": "Local storage เป็นแบบชั่วคราวบน Streamlit Community Cloud; ข้อมูลตารางยังอยู่ใน PostgreSQL แต่ไฟล์ต้นฉบับควรใช้ Supabase Storage",
    }


def _s3_client():
    import boto3
    from botocore.config import Config
    kwargs = {
        "config": Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        )
    }
    if os.getenv("S3_ENDPOINT_URL"):
        kwargs["endpoint_url"] = os.getenv("S3_ENDPOINT_URL")
    if os.getenv("S3_REGION"):
        kwargs["region_name"] = os.getenv("S3_REGION")
    if os.getenv("S3_ACCESS_KEY_ID"):
        kwargs["aws_access_key_id"] = os.getenv("S3_ACCESS_KEY_ID")
    if os.getenv("S3_SECRET_ACCESS_KEY"):
        kwargs["aws_secret_access_key"] = os.getenv("S3_SECRET_ACCESS_KEY")
    return boto3.client("s3", **kwargs)


def store_file(data: bytes, original_name: str, file_hash: str) -> str:
    name = _safe_name(original_name)
    key = f"documents/{file_hash[:2]}/{file_hash}_{name}"
    bucket = os.getenv("S3_BUCKET")
    if bucket:
        _s3_client().put_object(Bucket=bucket, Key=key, Body=data)
        return f"s3://{bucket}/{key}"

    LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCAL_UPLOAD_DIR / f"{file_hash}_{name}"
    if not path.exists():
        path.write_bytes(data)
    return str(path)


def load_file(storage_key: str) -> Optional[bytes]:
    if not storage_key:
        return None
    if storage_key.startswith("s3://"):
        rest = storage_key[5:]
        bucket, key = rest.split("/", 1)
        obj = _s3_client().get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()
    path = Path(storage_key)
    if path.exists() and path.is_file():
        return path.read_bytes()
    return None
