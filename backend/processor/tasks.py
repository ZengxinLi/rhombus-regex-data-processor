from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd
from celery import shared_task
from django.conf import settings
from django.db import close_old_connections
from pyspark.sql import DataFrame, SparkSession, functions as F

from .models import Job
from .services.credentials import ConnectionExpired, CredentialVault
from .services.patterns import PatternError, PatternResolver
from .services.s3 import S3ConnectionError, client_for, is_supported_object


def get_spark() -> SparkSession:
    """Create one local Spark session per Celery worker process.

    In a multi-node deployment, set SPARK_MASTER to the cluster URL; the code and
    partitioning policy remain the same.
    """
    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    return (
        SparkSession.builder.appName("regex-data-processor")
        .master(settings.SPARK_MASTER)
        .config("spark.sql.shuffle.partitions", str(settings.SPARK_SHUFFLE_PARTITIONS))
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )


def load_dataframe(spark: SparkSession, source_path: str, object_key: str) -> DataFrame:
    suffix = Path(object_key).suffix.lower()
    if suffix == ".csv":
        # CSV is read as text to preserve values such as account numbers with leading zeroes.
        return spark.read.option("header", True).option("inferSchema", False).csv(source_path)
    if suffix in {".xlsx", ".xls"}:
        # Excel has no native Spark reader. It is read once and immediately converted
        # into a distributed DataFrame, after which all transformations are Spark-native.
        local_frame = pd.read_excel(source_path, dtype=str)
        local_frame = local_frame.where(local_frame.notna(), None)
        return spark.createDataFrame(local_frame)
    raise ValueError("Only CSV, XLSX and XLS files can be processed.")


def apply_operation(frame: DataFrame, columns: list[str], operation: str, regex: str | None, replacement: str) -> DataFrame:
    result = frame
    for column in columns:
        # Backticks make literal headers such as `contact.email` or `First Name`
        # safe instead of treating dots as nested-field access.
        source = F.col(f"`{column.replace('`', '``')}`").cast("string")
        if operation == "replace":
            # Spark's replacement string supports $n. Escape it so the user-provided
            # replacement is always literal, including currency values such as "$0".
            literal_replacement = replacement.replace("\\", "\\\\").replace("$", "\\$")
            transformed = F.regexp_replace(source, regex or "", literal_replacement)
        elif operation == "normalize_whitespace":
            transformed = F.trim(F.regexp_replace(source, r"\s+", " "))
        elif operation == "lowercase":
            transformed = F.lower(source)
        elif operation == "uppercase":
            transformed = F.upper(source)
        elif operation == "trim":
            transformed = F.trim(source)
        else:
            raise ValueError("Unsupported transformation operation.")
        result = result.withColumn(column, transformed)
    return result


def _cancelled(job_id: str) -> bool:
    return Job.objects.filter(id=job_id, status=Job.Status.CANCELLED).exists()


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, ConnectionExpired):
        return str(exc)
    if isinstance(exc, S3ConnectionError):
        return str(exc)
    if isinstance(exc, PatternError):
        return str(exc)
    if isinstance(exc, ValueError):
        return str(exc)[:600]
    return "The processing job failed unexpectedly. Inspect worker logs using the job id; credentials are never logged."


@shared_task(bind=True, name="processor.process_job")
def process_job(self, job_id: str, connection_id: str) -> None:
    """Download once from S3, transform with Spark, persist parquet and bounded UI preview."""
    close_old_connections()
    job = Job.objects.get(id=job_id)
    if _cancelled(job_id):
        return
    job.start()

    try:
        credentials = CredentialVault.get(connection_id)
        if not is_supported_object(job.source_key):
            raise ValueError("The selected file type is not supported.")

        with tempfile.TemporaryDirectory(prefix="regex-job-") as temporary_dir:
            source_path = str(Path(temporary_dir) / f"source{Path(job.source_key).suffix.lower()}")
            try:
                client_for(credentials).download_file(job.source_bucket, job.source_key, source_path)
            except Exception as exc:
                raise S3ConnectionError(
                    "Unable to download the selected file. Check bucket access and that the object still exists."
                ) from exc

            if _cancelled(job_id):
                return
            job.set_progress(15)
            rule = PatternResolver().resolve(job.natural_language_request, job.requested_operation)
            job.resolved_operation = rule.operation
            job.regex_pattern = rule.regex or ""
            job.save(update_fields=["resolved_operation", "regex_pattern"])
            job.set_progress(25)

            spark = get_spark()
            input_frame = load_dataframe(spark, source_path, job.source_key)
            missing = sorted(set(job.selected_columns) - set(input_frame.columns))
            if missing:
                raise ValueError(f"Selected column(s) not found in the source: {', '.join(missing)}.")
            if not job.selected_columns:
                raise ValueError("Select at least one target column.")

            total_rows = input_frame.count()
            job.total_rows = total_rows
            job.save(update_fields=["total_rows"])
            if _cancelled(job_id):
                return
            job.set_progress(40)

            partitions = max(
                1,
                min(
                    settings.SPARK_SHUFFLE_PARTITIONS,
                    max(1, math.ceil(total_rows / settings.SPARK_ROWS_PER_PARTITION)),
                ),
            )
            distributed = input_frame.repartition(partitions)
            transformed = apply_operation(
                distributed, job.selected_columns, rule.operation, rule.regex, job.replacement_value
            ).withColumn("__row_id", F.monotonically_increasing_id())

            job_dir = Path(settings.RESULTS_ROOT) / str(job.id)
            parquet_dir = job_dir / "parquet"
            if job_dir.exists():
                shutil.rmtree(job_dir)
            parquet_dir.mkdir(parents=True, exist_ok=True)
            job.set_progress(55)
            transformed.write.mode("overwrite").parquet(str(parquet_dir))
            if _cancelled(job_id):
                return
            job.set_progress(85, processed_rows=total_rows)

            # This is intentionally bounded: the browser receives pages from the
            # preview rather than an unbounded collect of a multi-million-row dataset.
            preview = [json.loads(line) for line in transformed.drop("__row_id").limit(settings.RESULT_PREVIEW_LIMIT).toJSON().collect()]
            preview_path = job_dir / "preview.json"
            preview_path.write_text(json.dumps(preview, ensure_ascii=False), encoding="utf-8")
            job.succeed(result_path=str(job_dir), columns=[field.name for field in input_frame.schema.fields])
    except Exception as exc:
        failed_job = Job.objects.get(id=job_id)
        failed_job.fail(_safe_error(exc))
        raise
    finally:
        close_old_connections()
