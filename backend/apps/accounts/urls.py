from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

app_name = "accounts"

router = SimpleRouter()
router.register("users", views.UserViewSet, basename="user")

urlpatterns = [
    path("me/", views.MeView.as_view(), name="me"),
    *router.urls,
]
