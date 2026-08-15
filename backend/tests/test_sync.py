"""
Tests for the offline synchronisation endpoint.

Every test here describes a condition that occurs in the field, not a
theoretical edge case. Mobile coverage in the pilot areas is intermittent, so a
dropped upload halfway through a batch is normal rather than exceptional.
"""

import datetime as dt
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone


def _visit_payload(client_code, *, visit_id=None, **overrides):
    payload = {
        "id": str(visit_id or uuid.uuid4()),
        "client": client_code,
        "purpose": "ROUTINE",
        "result": "COMPLETED",
        "visit_date": str(dt.date.today()),
        "latitude": "9.076500",
        "longitude": "7.398600",
        "location_accuracy_metres": 20,
        "location_captured_at": timezone.now().isoformat(),
        "client_created_at": timezone.now().isoformat(),
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestPush:
    def test_a_batch_is_accepted(self, api_client, client_row, programme_settings):
        response = api_client.post(
            reverse("visits:sync-push"),
            {"device_id": "handset-01", "visits": [_visit_payload(client_row.client_code)]},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["status"] == "ACCEPTED"
        assert len(response.data["accepted"]) == 1

    def test_a_repeated_upload_creates_nothing_new(
        self, api_client, client_row, programme_settings
    ):
        from apps.visits.models import HomeVisit

        # The handset uploaded, the connection dropped before the reply
        # arrived, and it retried the same batch.
        visit_id = uuid.uuid4()
        payload = {
            "device_id": "handset-01",
            "visits": [_visit_payload(client_row.client_code, visit_id=visit_id)],
        }
        url = reverse("visits:sync-push")

        api_client.post(url, payload, format="json")
        second = api_client.post(url, payload, format="json")

        assert second.data["duplicates"] == [str(visit_id)]
        assert HomeVisit.objects.count() == 1

    def test_one_bad_record_does_not_lose_the_others(
        self, api_client, client_row, programme_settings
    ):
        from apps.visits.models import HomeVisit

        good_one = _visit_payload(client_row.client_code)
        bad = _visit_payload(client_row.client_code, purpose="NOT_A_PURPOSE")
        good_two = _visit_payload(client_row.client_code)

        response = api_client.post(
            reverse("visits:sync-push"),
            {"device_id": "handset-01", "visits": [good_one, bad, good_two]},
            format="json",
        )

        assert response.data["status"] == "PARTIAL"
        assert len(response.data["accepted"]) == 2
        assert len(response.data["rejected"]) == 1
        assert HomeVisit.objects.count() == 2

    def test_a_future_dated_visit_is_rejected(
        self, api_client, client_row, programme_settings
    ):
        # Usually a handset with the wrong date, not a fabricated record.
        payload = _visit_payload(
            client_row.client_code,
            visit_date=str(dt.date.today() + dt.timedelta(days=3)),
        )
        response = api_client.post(
            reverse("visits:sync-push"),
            {"device_id": "handset-01", "visits": [payload]},
            format="json",
        )
        assert response.data["status"] == "REJECTED"
        assert "visit_date" in response.data["rejected"][0]["errors"]

    def test_an_oversized_batch_is_refused(
        self, api_client, client_row, programme_settings
    ):
        payload = [_visit_payload(client_row.client_code) for _ in range(201)]
        response = api_client.post(
            reverse("visits:sync-push"),
            {"device_id": "handset-01", "visits": payload},
            format="json",
        )
        assert response.status_code == 413

    def test_the_location_verdict_is_computed_by_the_server(
        self, api_client, client_row, programme_settings
    ):
        from apps.visits.models import HomeVisit

        # The handset claims the visit is verified. The server ignores that and
        # decides for itself. This is the whole point of the verification.
        client_row.household_latitude = 9.0765
        client_row.household_longitude = 7.3986
        client_row.save()

        payload = _visit_payload(
            client_row.client_code,
            latitude="9.500000",  # Roughly 47 km away.
            longitude="7.398600",
            location_status="VERIFIED",
        )
        api_client.post(
            reverse("visits:sync-push"),
            {"device_id": "handset-01", "visits": [payload]},
            format="json",
        )

        visit = HomeVisit.objects.get()
        assert visit.location_status == "OUT_OF_RANGE"
        assert visit.flagged_for_review

    def test_a_batch_writes_a_sync_record(
        self, api_client, client_row, programme_settings
    ):
        from apps.visits.models import SyncBatch

        api_client.post(
            reverse("visits:sync-push"),
            {
                "device_id": "handset-07",
                "app_version": "1.2.0",
                "visits": [_visit_payload(client_row.client_code)],
            },
            format="json",
        )

        batch = SyncBatch.objects.get()
        assert batch.device_id == "handset-07"
        assert batch.records_accepted == 1


@pytest.mark.django_db
class TestPull:
    def test_a_supervisor_receives_facility_visits(
        self, api_client, client_row, programme_settings
    ):
        api_client.post(
            reverse("visits:sync-push"),
            {"device_id": "handset-01", "visits": [_visit_payload(client_row.client_code)]},
            format="json",
        )
        response = api_client.get(reverse("visits:sync-pull"))
        assert response.status_code == 200
        assert response.data["count"] == 1

    def test_a_pull_returns_codes_and_no_names(
        self, api_client, client_row, programme_settings
    ):
        api_client.post(
            reverse("visits:sync-push"),
            {"device_id": "handset-01", "visits": [_visit_payload(client_row.client_code)]},
            format="json",
        )
        row = api_client.get(reverse("visits:sync-pull")).data["visits"][0]

        assert row["client"] == client_row.client_code
        assert "full_name" not in row
        assert "Test Client" not in str(row)

    def test_a_malformed_since_value_is_refused(self, api_client, programme_settings):
        response = api_client.get(reverse("visits:sync-pull"), {"since": "yesterday"})
        assert response.status_code == 400
