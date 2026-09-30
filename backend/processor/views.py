from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from celery import current_app
from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .models import Job
from .services.credentials import ConnectionExpired, CredentialVault
from .services.s3 import S3ConnectionError, inspect_columns, is_supported_object, list_supported_objects
from .tasks import process_job


def _json(request: HttpRequest) -> dict[str, Any]:
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object.")
    return body


def _value(payload: dict[str, Any], name: str, *, required: bool = True) -> str:
    value = payload.get(name, "")
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name} is required.")
    return value


def _job_payload(job: Job) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "status": job.status,
        "progress": job.progress,
        "total_rows": job.total_rows,
        "processed_rows": job.processed_rows,
        "source": {"bucket": job.source_bucket, "key": job.source_key},
        "selected_columns": job.selected_columns,
        "requested_operation": job.requested_operation,
        "resolved_operation": job.resolved_operation or None,
        "regex_pattern": job.regex_pattern or None,
        "error": job.error_message or None,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "result_available": job.status == Job.Status.SUCCESS,
    }


@csrf_exempt
@require_http_methods(["POST"])
def connect_s3(request: HttpRequest) -> JsonResponse:
    """Validate a user-owned bucket and return an opaque, short-lived connection id."""
    try:
        payload = _json(request)
        credentials = {
            "access_key_id": _value(payload, "access_key_id"),
            "secret_access_key": _value(payload, "secret_access_key"),
            "session_token": _value(payload, "session_token", required=False),
            "region": _value(payload, "region", required=False),
        }
        bucket = _value(payload, "bucket")
        files = list_supported_objects(credentials, bucket)
        connection_id = CredentialVault.put({**credentials, "bucket": bucket})
        return JsonResponse({"connection_id": connection_id, "bucket": bucket, "files": files})
    except (ValueError, S3ConnectionError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def create_job(request: HttpRequest) -> JsonResponse:
    try:
        payload = _json(request)
        connection_id = _value(payload, "connection_id")
        credentials = CredentialVault.get(connection_id)
        source_key = _value(payload, "source_key")
        if not is_supported_object(source_key):
            raise ValueError("Choose a CSV, XLSX or XLS file.")
        selected_columns = payload.get("selected_columns")
        if not isinstance(selected_columns, list) or not selected_columns or not all(
            isinstance(column, str) and column.strip() for column in selected_columns
        ):
            raise ValueError("selected_columns must be a non-empty list of column names.")
        if len(selected_columns) > 50:
            raise ValueError("At most 50 target columns can be selected per job.")
        operation = _value(payload, "requested_operation", required=False) or "auto"
        if operation not in {"auto", "replace", "normalize_whitespace", "lowercase", "uppercase", "trim"}:
            raise ValueError("Unsupported requested operation.")
        job = Job.objects.create(
            source_bucket=credentials["bucket"],
            source_key=source_key,
            selected_columns=[column.strip() for column in selected_columns],
            natural_language_request=_value(payload, "natural_language_request"),
            replacement_value=_value(payload, "replacement_value", required=False),
            requested_operation=operation,
        )
        try:
            queued = process_job.apply_async(args=[str(job.id), connection_id], queue="processing")
        except Exception as exc:
            job.fail("The job could not be queued. Confirm Redis and the worker are running.")
            raise RuntimeError("The job could not be queued. Confirm Redis and the worker are running.") from exc
        job.celery_task_id = queued.id
        job.save(update_fields=["celery_task_id"])
        return JsonResponse(_job_payload(job), status=202)
    except ConnectionExpired as exc:
        return JsonResponse({"error": str(exc)}, status=410)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except RuntimeError as exc:
        return JsonResponse({"error": str(exc)}, status=503)


@csrf_exempt
@require_http_methods(["POST"])
def inspect_source(request: HttpRequest) -> JsonResponse:
    try:
        payload = _json(request)
        connection_id = _value(payload, "connection_id")
        source_key = _value(payload, "source_key")
        credentials = CredentialVault.get(connection_id)
        columns = inspect_columns(credentials, credentials["bucket"], source_key)
        return JsonResponse({"columns": columns})
    except ConnectionExpired as exc:
        return JsonResponse({"error": str(exc)}, status=410)
    except (ValueError, S3ConnectionError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_GET
def get_job(_: HttpRequest, job_id: str) -> JsonResponse:
    return JsonResponse(_job_payload(get_object_or_404(Job, id=job_id)))


@csrf_exempt
@require_http_methods(["POST"])
def cancel_job(_: HttpRequest, job_id: str) -> JsonResponse:
    job = get_object_or_404(Job, id=job_id)
    if job.status in {Job.Status.SUCCESS, Job.Status.FAILED}:
        return JsonResponse({"error": "Completed jobs cannot be cancelled."}, status=409)
    job.cancel()
    if job.celery_task_id:
        current_app.control.revoke(job.celery_task_id, terminate=False)
    return JsonResponse(_job_payload(job))


@require_GET
def job_results(request: HttpRequest, job_id: str) -> JsonResponse:
    job = get_object_or_404(Job, id=job_id)
    if job.status != Job.Status.SUCCESS:
        return JsonResponse({"error": "Results are available after the job succeeds."}, status=409)
    try:
        page = max(1, int(request.GET.get("page", "1")))
        page_size = min(100, max(1, int(request.GET.get("page_size", "25"))))
    except ValueError:
        return JsonResponse({"error": "page and page_size must be integers."}, status=400)

    preview_path = Path(job.result_path) / "preview.json"
    expected_root = Path(settings.RESULTS_ROOT).resolve()
    try:
        preview_path.resolve().relative_to(expected_root)
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "The saved result preview is unavailable."}, status=410)

    offset = (page - 1) * page_size
    rows = preview[offset: offset + page_size]
    return JsonResponse({
        "columns": job.result_columns,
        "rows": rows,
        "page": page,
        "page_size": page_size,
        "preview_rows": len(preview),
        "total_rows": job.total_rows,
        "has_next": offset + page_size < len(preview),
        "has_previous": page > 1,
        "is_bounded_preview": (job.total_rows or 0) > len(preview),
    })


@require_GET
def health(_: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})
