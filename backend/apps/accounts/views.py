"""The caller's own identity, and user administration."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import User
from .permissions import CanAdministerUsers, IsActiveHealthWorker
from .scoping import scope_user_queryset
from .serializers import DisableUserSerializer, MeSerializer, UserSerializer


class MeView(APIView):
    """
    GET /api/v1/accounts/me/

    Returns the caller's role and scope. Also touches last_active_at, which
    feeds the account-inactivity job: an account that never calls anything is
    an account that should be disabled.
    """

    permission_classes = [IsActiveHealthWorker]

    @extend_schema(responses=MeSerializer)
    def get(self, request):
        request.user.touch()
        return Response(MeSerializer(request.user).data)


class UserViewSet(viewsets.ModelViewSet):
    """
    User administration, for a state programme manager or a system
    administrator. Row visibility comes from scope_user_queryset, because a
    user row's scope is one of three fields and SCOPE_PATHS cannot express
    that OR.
    """

    permission_classes = [IsActiveHealthWorker, CanAdministerUsers]
    http_method_names = ["get", "post", "patch", "head", "options"]
    serializer_class = UserSerializer
    filterset_fields = ["role", "facility", "lga", "state", "is_active"]
    ordering_fields = ["username", "last_active_at", "date_joined"]
    ordering = ["username"]

    def get_queryset(self):
        return scope_user_queryset(
            User.objects.select_related("facility", "lga", "state"),
            self.request.user,
        )

    @extend_schema(request=DisableUserSerializer, responses=UserSerializer)
    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        serializer = DisableUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = self.get_object()
        user.disable(serializer.validated_data["reason"])
        return Response(UserSerializer(user, context={"request": request}).data)

    @extend_schema(request=None, responses=UserSerializer)
    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        user = self.get_object()
        user.is_active = True
        user.disabled_at = None
        user.disabled_reason = ""
        user.save(update_fields=["is_active", "disabled_at", "disabled_reason"])
        return Response(
            UserSerializer(user, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )
