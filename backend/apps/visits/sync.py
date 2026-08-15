"""
The offline synchronisation endpoint.

A mentor mother records visits in areas with no mobile coverage. The handset
holds them and uploads them when it next finds a signal, which may be days
later. Two properties make that safe.

A repeated upload creates nothing new. The handset generates the UUID, so a
retry after a dropped connection carries the identifier the server already
holds, and the server recognises it. Without this a single flaky upload would
duplicate every visit in the batch.

A partial batch is not lost. One malformed record does not reject the other
forty-nine. The rejected record is reported back with a reason so the handset
can show the field worker what to correct, and the accepted records are already
safe on the server.

The location verdict is computed here, on arrival. It is never accepted from
the handset. A verdict computed on a device can be altered on that device, and
the whole point of location verification is that it cannot be.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.permissions import IsActiveHealthWorker
from apps.accounts.scoping import scope_queryset
from apps.registry.models import Client, Infant, MentorMother
from apps.visits.models import HomeVisit, SyncBatch

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 200


class HomeVisitPushSerializer(serializers.ModelSerializer):
    """
    Accepts a visit created on a handset.

    The server-computed fields are read only. A handset that tries to send
    location_status has that value ignored rather than rejected, because
    rejecting it would break older application versions during a staged
    rollout.
    """

    id = serializers.UUIDField()
    client = serializers.SlugRelatedField(
        slug_field="client_code", queryset=Client.objects.all()
    )
    infant = serializers.SlugRelatedField(
        slug_field="baby_code",
        queryset=Infant.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = HomeVisit
        fields = [
            "id", "client", "infant", "purpose", "result", "visit_date",
            "duration_minutes", "notes", "latitude", "longitude",
            "location_accuracy_metres", "location_captured_at",
            "client_created_at", "device_id",
        ]

    def validate_visit_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError(
                "A visit cannot be dated in the future. Check the date on the "
                "handset."
            )
        return value

    def validate(self, attrs):
        infant = attrs.get("infant")
        client = attrs.get("client")
        if infant is not None and client is not None and infant.mother_id != client.pk:
            raise serializers.ValidationError(
                {"infant": "This infant is not registered to this client."}
            )
        return attrs


class HomeVisitPullSerializer(serializers.ModelSerializer):
    """Returns a visit to a handset. Codes only, never a name."""

    client = serializers.CharField(source="client.client_code", read_only=True)
    infant = serializers.CharField(
        source="infant.baby_code", read_only=True, default=None
    )

    class Meta:
        model = HomeVisit
        fields = [
            "id", "client", "infant", "purpose", "result", "visit_date",
            "duration_minutes", "notes", "latitude", "longitude",
            "location_status", "distance_from_household_metres",
            "flagged_for_review", "review_reason", "sync_version",
            "client_created_at", "server_received_at", "updated_at",
        ]
        read_only_fields = fields


@api_view(["POST"])
@permission_classes([IsActiveHealthWorker])
@throttle_classes([ScopedRateThrottle])
def push(request):
    """
    Upload a batch of records created on a handset.

    Returns a per-record outcome so the handset knows exactly what to retry and
    what to mark as sent. A single overall success flag would force the handset
    to resend the whole batch after any failure.
    """
    push.throttle_scope = "sync"

    records = request.data.get("visits", [])
    device_id = str(request.data.get("device_id", ""))[:64]
    app_version = str(request.data.get("app_version", ""))[:20]

    if not isinstance(records, list):
        return Response(
            {"detail": "The field 'visits' must be a list."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if len(records) > MAX_BATCH_SIZE:
        return Response(
            {
                "detail": (
                    f"A batch may hold at most {MAX_BATCH_SIZE} records. "
                    f"Send the rest in a second batch."
                )
            },
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    profile = getattr(request.user, "mentor_mother_profile", None)
    now = timezone.now()

    accepted: list[str] = []
    duplicates: list[str] = []
    rejected: list[dict] = []
    oldest = None

    for raw in records:
        record_id = raw.get("id")

        # Idempotency. A retry carries an identifier the server already holds.
        if record_id and HomeVisit.all_objects.filter(pk=record_id).exists():
            duplicates.append(str(record_id))
            continue

        serializer = HomeVisitPushSerializer(data=raw)
        if not serializer.is_valid():
            rejected.append({"id": record_id, "errors": serializer.errors})
            continue

        validated = serializer.validated_data
        author = profile or _resolve_mentor_mother(validated["client"])
        if author is None:
            rejected.append(
                {
                    "id": record_id,
                    "errors": {
                        "mentor_mother": [
                            "This account is not linked to a mentor mother "
                            "profile, and the client has none assigned."
                        ]
                    },
                }
            )
            continue

        # A mentor mother may upload only her own visits. Without this check a
        # compromised handset could write records against another worker.
        if profile is not None and validated["client"].mentor_mother_id != profile.id:
            rejected.append(
                {
                    "id": record_id,
                    "errors": {
                        "client": ["This client is assigned to another mentor mother."]
                    },
                }
            )
            continue

        try:
            with transaction.atomic():
                visit = serializer.save(
                    mentor_mother=author,
                    server_received_at=now,
                    device_id=device_id or validated.get("device_id", ""),
                )
                visit.evaluate_location(save=True)
        except Exception as exc:  # noqa: BLE001
            # One bad record must not lose the other forty-nine.
            logger.exception("Could not store visit %s from device %s.", record_id, device_id)
            rejected.append({"id": record_id, "errors": {"server": [type(exc).__name__]}})
            continue

        accepted.append(str(visit.pk))
        created = validated.get("client_created_at")
        if created and (oldest is None or created < oldest):
            oldest = created

    batch_status = (
        SyncBatch.Status.ACCEPTED
        if not rejected
        else SyncBatch.Status.PARTIAL
        if accepted or duplicates
        else SyncBatch.Status.REJECTED
    )
    SyncBatch.objects.create(
        device_id=device_id,
        user=request.user,
        status=batch_status,
        records_submitted=len(records),
        records_accepted=len(accepted),
        records_duplicate=len(duplicates),
        records_rejected=len(rejected),
        rejection_detail=rejected[:50],
        oldest_record_created_at=oldest,
        app_version=app_version,
    )

    return Response(
        {
            "status": batch_status,
            "accepted": accepted,
            "duplicates": duplicates,
            "rejected": rejected,
            "server_time": now.isoformat(),
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsActiveHealthWorker])
@throttle_classes([ScopedRateThrottle])
def pull(request):
    """
    Return records changed since the timestamp the handset supplies.

    The result is scoped, so a mentor mother receives her own caseload and
    nothing else. next_since is the server time, not the newest record time,
    so a record written during the request is not skipped on the next pull.
    """
    pull.throttle_scope = "sync"

    since = request.query_params.get("since")
    now = timezone.now()

    queryset = scope_queryset(HomeVisit.objects.all(), request.user)
    if since:
        parsed = _parse_timestamp(since)
        if parsed is None:
            return Response(
                {"detail": "'since' must be an ISO 8601 timestamp."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        queryset = queryset.filter(updated_at__gt=parsed)

    queryset = queryset.select_related("client", "infant").order_by("updated_at")[
        : MAX_BATCH_SIZE
    ]
    rows = list(queryset)

    return Response(
        {
            "visits": HomeVisitPullSerializer(rows, many=True).data,
            "count": len(rows),
            "has_more": len(rows) == MAX_BATCH_SIZE,
            "next_since": (
                rows[-1].updated_at.isoformat() if len(rows) == MAX_BATCH_SIZE
                else now.isoformat()
            ),
        }
    )


def _resolve_mentor_mother(client: Client) -> MentorMother | None:
    return client.mentor_mother


def _parse_timestamp(value: str):
    from django.utils.dateparse import parse_datetime

    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed
