from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile

import boto3
import pandas as pd
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


SUPPORTED_SUFFIXES = (".csv", ".xlsx", ".xls")


class S3ConnectionError(ValueError):
    pass


def is_supported_object(key: str) -> bool:
    return key.lower().endswith(SUPPORTED_SUFFIXES)


def client_for(credentials: dict[str, str]):
    return boto3.client(
        "s3",
        aws_access_key_id=credentials["access_key_id"],
        aws_secret_access_key=credentials["secret_access_key"],
        aws_session_token=credentials.get("session_token") or None,
        region_name=credentials.get("region") or None,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )


def list_supported_objects(credentials: dict[str, str], bucket: str, prefix: str = "") -> list[dict[str, str | int | None]]:
    try:
        response = client_for(credentials).list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1000)
    except (ClientError, BotoCoreError) as exc:
        # Do not return provider messages because they can include request metadata.
        raise S3ConnectionError("Unable to reach that bucket. Check the region, bucket name and AWS credentials.") from exc

    items = []
    for item in response.get("Contents", []):
        key = item["Key"]
        if not is_supported_object(key):
            continue
        modified: datetime | None = item.get("LastModified")
        items.append({
            "key": key,
            "size": item.get("Size", 0),
            "last_modified": modified.isoformat() if modified else None,
            "file_type": key.rsplit(".", 1)[-1].lower(),
        })
    return items


def inspect_columns(credentials: dict[str, str], bucket: str, key: str) -> list[str]:
    """Read only the tabular header so the UI can offer target-column choices."""
    if not is_supported_object(key):
        raise S3ConnectionError("Only CSV, XLSX and XLS files can be inspected.")
    suffix = Path(key).suffix.lower()
    try:
        with tempfile.TemporaryDirectory(prefix="regex-schema-") as temporary_dir:
            source = str(Path(temporary_dir) / f"source{suffix}")
            client_for(credentials).download_file(bucket, key, source)
            if suffix == ".csv":
                columns = list(pd.read_csv(source, nrows=0).columns)
            else:
                columns = list(pd.read_excel(source, nrows=0).columns)
    except (ClientError, BotoCoreError, OSError, ValueError) as exc:
        raise S3ConnectionError("Unable to read column names from the selected object.") from exc
    cleaned = [str(column) for column in columns if str(column).strip()]
    if not cleaned:
        raise S3ConnectionError("The selected file does not contain a usable header row.")
    return cleaned
