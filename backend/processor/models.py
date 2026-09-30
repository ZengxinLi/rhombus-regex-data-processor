from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    source_bucket = models.CharField(max_length=255)
    source_key = models.TextField()
    selected_columns = models.JSONField(default=list)
    natural_language_request = models.TextField()
    replacement_value = models.TextField(blank=True)
    requested_operation = models.CharField(max_length=32, default="auto")
    resolved_operation = models.CharField(max_length=32, blank=True)
    regex_pattern = models.TextField(blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    total_rows = models.BigIntegerField(null=True, blank=True)
    processed_rows = models.BigIntegerField(default=0)
    error_message = models.TextField(blank=True)
    result_path = models.TextField(blank=True)
    result_columns = models.JSONField(default=list)
    celery_task_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def start(self) -> None:
        self.status = self.Status.RUNNING
        self.progress = max(2, self.progress)
        self.started_at = timezone.now()
        self.error_message = ""
        self.save(update_fields=["status", "progress", "started_at", "error_message"])

    def set_progress(self, progress: int, *, processed_rows: int | None = None) -> None:
        if self.status == self.Status.CANCELLED:
            return
        self.progress = min(99, max(self.progress, progress))
        fields = ["progress"]
        if processed_rows is not None:
            self.processed_rows = processed_rows
            fields.append("processed_rows")
        self.save(update_fields=fields)

    def succeed(self, *, result_path: str, columns: list[str]) -> None:
        self.status = self.Status.SUCCESS
        self.progress = 100
        self.processed_rows = self.total_rows or self.processed_rows
        self.result_path = result_path
        self.result_columns = columns
        self.completed_at = timezone.now()
        self.save(update_fields=[
            "status", "progress", "processed_rows", "result_path", "result_columns", "completed_at"
        ])

    def fail(self, message: str) -> None:
        if self.status == self.Status.CANCELLED:
            return
        self.status = self.Status.FAILED
        self.error_message = message[:1000]
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "error_message", "completed_at"])

    def cancel(self) -> None:
        if self.status in {self.Status.SUCCESS, self.Status.FAILED}:
            return
        self.status = self.Status.CANCELLED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at"])
