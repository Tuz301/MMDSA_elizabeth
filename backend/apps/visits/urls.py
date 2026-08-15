from django.urls import path

from . import sync

app_name = "visits"

urlpatterns = [
    path("sync/push/", sync.push, name="sync-push"),
    path("sync/pull/", sync.pull, name="sync-pull"),
]
