from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import Client

from processor.models import Job


@pytest.mark.django_db
@patch("processor.views.list_supported_objects")
def test_connect_returns_opaque_id_not_credentials(mock_list):
    mock_list.return_value = [{"key": "people.csv", "size": 14, "last_modified": None, "file_type": "csv"}]
    response = Client().post(
        "/api/s3/connect/",
        data={
            "access_key_id": "AKIA_TEST",
            "secret_access_key": "very-secret",
            "region": "ap-southeast-2",
            "bucket": "private-bucket",
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    assert "connection_id" in response.json()
    assert "very-secret" not in response.content.decode()
    assert "AKIA_TEST" not in response.content.decode()


@pytest.mark.django_db
@patch("processor.views.process_job.apply_async")
@patch("processor.views.list_supported_objects")
def test_create_job_queues_without_persisting_s3_secret(mock_list, mock_async):
    mock_list.return_value = []
    mock_async.return_value = SimpleNamespace(id="celery-task-id")
    client = Client()
    connection = client.post(
        "/api/s3/connect/",
        data={"access_key_id": "key", "secret_access_key": "secret", "bucket": "private-bucket"},
        content_type="application/json",
    ).json()["connection_id"]
    response = client.post(
        "/api/jobs/",
        data={
            "connection_id": connection,
            "source_key": "people.csv",
            "selected_columns": ["email"],
            "natural_language_request": "find email addresses",
            "replacement_value": "REDACTED",
            "requested_operation": "auto",
        },
        content_type="application/json",
    )
    assert response.status_code == 202
    job = Job.objects.get()
    assert "secret" not in str(job.__dict__)
    assert job.celery_task_id == "celery-task-id"
