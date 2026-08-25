from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views, webhooks

app_name = "messaging"

router = SimpleRouter()
router.register("outbound", views.OutboundMessageViewSet, basename="outbound")
router.register("inbound", views.InboundMessageViewSet, basename="inbound")
router.register("templates", views.MessageTemplateViewSet, basename="template")

urlpatterns = [
    path("webhooks/termii/", webhooks.termii_webhook, name="termii-webhook"),
    *router.urls,
]
