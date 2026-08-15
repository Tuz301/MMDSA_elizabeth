"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

from apps.common.views import health, readiness

urlpatterns = [
    path("admin/", admin.site.urls),
    # Used by the load balancer target group health check. No authentication,
    # and no information about the system beyond whether it is serving.
    path("api/v1/health/", health, name="health"),
    # Used by the deployment pipeline. Checks the database and the broker.
    path("api/v1/readiness/", readiness, name="readiness"),
    path("api/v1/visits/", include("apps.visits.urls")),
]
