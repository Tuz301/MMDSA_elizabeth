from rest_framework.routers import SimpleRouter

from . import views

app_name = "registry"

router = SimpleRouter()
router.register("states", views.StateViewSet, basename="state")
router.register("lgas", views.LgaViewSet, basename="lga")
router.register("facilities", views.FacilityViewSet, basename="facility")
router.register("mentor-mothers", views.MentorMotherViewSet, basename="mentor-mother")
router.register("clients", views.ClientViewSet, basename="client")
router.register("infants", views.InfantViewSet, basename="infant")

urlpatterns = router.urls
