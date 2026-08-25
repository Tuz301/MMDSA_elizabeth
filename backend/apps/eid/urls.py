from rest_framework.routers import SimpleRouter

from . import views

app_name = "eid"

router = SimpleRouter()
router.register("appointments", views.EidAppointmentViewSet, basename="appointment")
router.register("samples", views.EidSampleViewSet, basename="sample")
router.register("linkages", views.ArtLinkageViewSet, basename="linkage")

urlpatterns = router.urls
