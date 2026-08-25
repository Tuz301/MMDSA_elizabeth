from django.urls import path
from rest_framework.routers import SimpleRouter

from . import sync, views

app_name = "visits"

router = SimpleRouter()
router.register("records", views.HomeVisitViewSet, basename="record")
router.register("anomalies", views.GeospatialAnomalyViewSet, basename="anomaly")
router.register("sync-batches", views.SyncBatchViewSet, basename="sync-batch")

urlpatterns = [
    path("sync/push/", sync.push, name="sync-push"),
    path("sync/pull/", sync.pull, name="sync-pull"),
    *router.urls,
]
