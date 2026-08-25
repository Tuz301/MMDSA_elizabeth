from rest_framework.routers import SimpleRouter

from . import views

app_name = "alerts"

router = SimpleRouter()
router.register("rules", views.AlertRuleViewSet, basename="rule")
router.register("", views.AlertViewSet, basename="alert")

urlpatterns = router.urls
