"""Health and readiness endpoints."""

from __future__ import annotations

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def health(request):
    """
    Liveness. Returns 200 whenever the process can serve a request.

    This endpoint does not touch the database. A database fault must not cause
    the load balancer to replace every healthy instance, because replacing them
    does not fix the database and it removes the capacity to recover.
    """
    return JsonResponse({"status": "ok"})


@csrf_exempt
def readiness(request):
    """
    Readiness. Checks the dependencies the application cannot serve without.
    """
    checks: dict[str, str] = {}
    healthy = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"failed: {type(exc).__name__}"
        healthy = False

    try:
        from django.conf import settings
        import redis

        client = redis.from_url(settings.CELERY_BROKER_URL, socket_timeout=2)
        client.ping()
        checks["broker"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["broker"] = f"failed: {type(exc).__name__}"
        healthy = False

    return JsonResponse(
        {"status": "ready" if healthy else "not ready", "checks": checks},
        status=200 if healthy else 503,
    )
