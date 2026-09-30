from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("s3/connect/", views.connect_s3, name="connect-s3"),
    path("s3/inspect/", views.inspect_source, name="inspect-source"),
    path("jobs/", views.create_job, name="create-job"),
    path("jobs/<uuid:job_id>/", views.get_job, name="get-job"),
    path("jobs/<uuid:job_id>/cancel/", views.cancel_job, name="cancel-job"),
    path("jobs/<uuid:job_id>/results/", views.job_results, name="job-results"),
]
