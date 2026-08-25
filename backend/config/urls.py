"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.common.summary import metrics_summary
from apps.common.views import health, readiness

urlpatterns = [
    path("admin/", admin.site.urls),
    # Used by the load balancer target group health check. No authentication,
    # and no information about the system beyond whether it is serving.
    path("api/v1/health/", health, name="health"),
    # Used by the deployment pipeline. Checks the database and the broker.
    path("api/v1/readiness/", readiness, name="readiness"),
    path("api/v1/accounts/", include("apps.accounts.urls")),
    path("api/v1/registry/", include("apps.registry.urls")),
    path("api/v1/visits/", include("apps.visits.urls")),
    path("api/v1/eid/", include("apps.eid.urls")),
    path("api/v1/alerts/", include("apps.alerts.urls")),
    path("api/v1/messaging/", include("apps.messaging.urls")),
    path("api/v1/metrics/summary/", metrics_summary, name="metrics-summary"),
    # The schema and its viewer are authenticated. See SPECTACULAR_SETTINGS.
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/v1/schema/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="schema-docs",
    ),
]
