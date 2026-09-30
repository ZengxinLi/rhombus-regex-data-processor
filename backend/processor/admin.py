from django.contrib import admin

from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "source_bucket", "source_key", "progress", "created_at")
    list_filter = ("status",)
    search_fields = ("id", "source_bucket", "source_key")
    readonly_fields = ("id", "created_at", "started_at", "completed_at")
