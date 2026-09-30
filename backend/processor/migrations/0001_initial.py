# Generated manually so the application starts with a deterministic schema.
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Job",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("QUEUED", "Queued"), ("RUNNING", "Running"), ("SUCCESS", "Success"), ("FAILED", "Failed"), ("CANCELLED", "Cancelled")], db_index=True, default="QUEUED", max_length=16)),
                ("source_bucket", models.CharField(max_length=255)),
                ("source_key", models.TextField()),
                ("selected_columns", models.JSONField(default=list)),
                ("natural_language_request", models.TextField()),
                ("replacement_value", models.TextField(blank=True)),
                ("requested_operation", models.CharField(default="auto", max_length=32)),
                ("resolved_operation", models.CharField(blank=True, max_length=32)),
                ("regex_pattern", models.TextField(blank=True)),
                ("progress", models.PositiveSmallIntegerField(default=0)),
                ("total_rows", models.BigIntegerField(blank=True, null=True)),
                ("processed_rows", models.BigIntegerField(default=0)),
                ("error_message", models.TextField(blank=True)),
                ("result_path", models.TextField(blank=True)),
                ("result_columns", models.JSONField(default=list)),
                ("celery_task_id", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
