"""
The supervisor's clinical workflow through the API, and its invariants.

The invariants under test are the ones that protect the measurement: entry
never sets acknowledgement, acknowledgement is write-once, a positive result
opens the linkage record, and acknowledging a positive closes its alert.
"""

import datetime as dt

import pytest
from django.utils import timezone

from apps.alerts.models import Alert
from apps.eid.models import ArtLinkage, EidAppointment, EidSample
from apps.registry.models import Infant


@pytest.fixture
def scheduled_appointment(infant):
    return EidAppointment.objects.create(
        infant=infant,
        milestone="WEEK_6",
        due_date=dt.date.today() - dt.timedelta(days=3),
    )


@pytest.fixture
def pending_sample(scheduled_appointment):
    return EidSample.objects.create(
        appointment=scheduled_appointment,
        sample_identifier="SMP-API-001",
        collected_on=dt.date.today() - dt.timedelta(days=3),
    )


def _enter_result(api_client, sample, result="POSITIVE", issued_days_ago=1):
    return api_client.post(
        f"/api/v1/eid/samples/{sample.pk}/result/",
        {
            "result": result,
            "result_issued_on": str(dt.date.today() - dt.timedelta(days=issued_days_ago)),
        },
        format="json",
    )


@pytest.mark.django_db
class TestSampleRegistration:
    def test_registering_a_sample_marks_the_appointment_attended(
        self, api_client, scheduled_appointment
    ):
        response = api_client.post(
            "/api/v1/eid/samples/",
            {
                "appointment": str(scheduled_appointment.pk),
                "sample_identifier": "SMP-API-010",
                "collected_on": str(dt.date.today()),
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        scheduled_appointment.refresh_from_db()
        assert scheduled_appointment.status == EidAppointment.Status.ATTENDED
        assert scheduled_appointment.attended_date == dt.date.today()

    def test_a_second_sample_for_the_same_appointment_is_refused(
        self, api_client, pending_sample
    ):
        response = api_client.post(
            "/api/v1/eid/samples/",
            {
                "appointment": str(pending_sample.appointment.pk),
                "sample_identifier": "SMP-API-011",
                "collected_on": str(dt.date.today()),
            },
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestResultEntry:
    def test_entry_sets_entry_fields_and_never_acknowledgement(
        self, api_client, pending_sample, supervisor
    ):
        response = _enter_result(api_client, pending_sample)
        assert response.status_code == 200, response.content
        pending_sample.refresh_from_db()
        assert pending_sample.result == EidSample.Result.POSITIVE
        assert pending_sample.result_entered_at is not None
        assert pending_sample.entered_by == supervisor
        # The gap from here to acknowledgement is the relay delay. Entry must
        # leave it open.
        assert pending_sample.result_acknowledged_at is None

    def test_a_positive_result_opens_the_pending_linkage_and_flips_the_outcome(
        self, api_client, pending_sample, infant
    ):
        _enter_result(api_client, pending_sample)
        infant.refresh_from_db()
        assert infant.outcome == Infant.Outcome.HIV_POSITIVE_NOT_LINKED
        linkage = ArtLinkage.objects.get(infant=infant)
        assert linkage.status == ArtLinkage.Status.PENDING
        assert linkage.triggering_sample == pending_sample

    def test_a_negative_result_does_not_touch_the_outcome(
        self, api_client, pending_sample, infant
    ):
        _enter_result(api_client, pending_sample, result="NEGATIVE")
        infant.refresh_from_db()
        assert infant.outcome == Infant.Outcome.IN_FOLLOW_UP
        assert not ArtLinkage.objects.exists()

    def test_re_entering_a_result_is_refused(self, api_client, pending_sample):
        _enter_result(api_client, pending_sample)
        response = _enter_result(api_client, pending_sample, result="NEGATIVE")
        assert response.status_code == 409

    def test_a_result_cannot_be_issued_before_collection(
        self, api_client, pending_sample
    ):
        response = api_client.post(
            f"/api/v1/eid/samples/{pending_sample.pk}/result/",
            {
                "result": "POSITIVE",
                "result_issued_on": str(dt.date.today() - dt.timedelta(days=30)),
            },
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestAcknowledgement:
    def test_acknowledging_before_entry_is_refused(self, api_client, pending_sample):
        response = api_client.post(
            f"/api/v1/eid/samples/{pending_sample.pk}/acknowledge/"
        )
        assert response.status_code == 409

    def test_acknowledging_sets_the_fields_and_resolves_the_alert(
        self, api_client, pending_sample, supervisor, open_alert
    ):
        _enter_result(api_client, pending_sample)
        # Re-point the fixture alert at this sample, as the engine would.
        open_alert.subject_type = "eid.EidSample"
        open_alert.subject_id = pending_sample.pk
        open_alert.save(update_fields=["subject_type", "subject_id"])

        response = api_client.post(
            f"/api/v1/eid/samples/{pending_sample.pk}/acknowledge/"
        )
        assert response.status_code == 200
        pending_sample.refresh_from_db()
        assert pending_sample.result_acknowledged_at is not None
        assert pending_sample.acknowledged_by == supervisor
        open_alert.refresh_from_db()
        assert open_alert.status == Alert.Status.RESOLVED

    def test_acknowledgement_is_write_once(self, api_client, pending_sample):
        _enter_result(api_client, pending_sample)
        api_client.post(f"/api/v1/eid/samples/{pending_sample.pk}/acknowledge/")
        pending_sample.refresh_from_db()
        first = pending_sample.result_acknowledged_at

        response = api_client.post(
            f"/api/v1/eid/samples/{pending_sample.pk}/acknowledge/"
        )
        assert response.status_code == 200
        pending_sample.refresh_from_db()
        # A double tap must not rewrite the relay delay measurement.
        assert pending_sample.result_acknowledged_at == first

    def test_caregiver_informed_requires_acknowledgement_first(
        self, api_client, pending_sample
    ):
        _enter_result(api_client, pending_sample)
        response = api_client.post(
            f"/api/v1/eid/samples/{pending_sample.pk}/caregiver-informed/"
        )
        assert response.status_code == 409

        api_client.post(f"/api/v1/eid/samples/{pending_sample.pk}/acknowledge/")
        response = api_client.post(
            f"/api/v1/eid/samples/{pending_sample.pk}/caregiver-informed/"
        )
        assert response.status_code == 200
        pending_sample.refresh_from_db()
        assert pending_sample.caregiver_informed_at is not None


@pytest.mark.django_db
class TestArtLinkage:
    def _positive_linkage(self, api_client, pending_sample, infant):
        _enter_result(api_client, pending_sample)
        return ArtLinkage.objects.get(infant=infant)

    def test_started_without_a_date_is_refused(
        self, api_client, pending_sample, infant
    ):
        linkage = self._positive_linkage(api_client, pending_sample, infant)
        response = api_client.patch(
            f"/api/v1/eid/linkages/{linkage.pk}/",
            {"status": "STARTED"},
            format="json",
        )
        assert response.status_code == 400

    def test_started_with_a_date_flips_the_outcome_to_on_art(
        self, api_client, pending_sample, infant
    ):
        linkage = self._positive_linkage(api_client, pending_sample, infant)
        response = api_client.patch(
            f"/api/v1/eid/linkages/{linkage.pk}/",
            {"status": "STARTED", "art_start_date": str(dt.date.today())},
            format="json",
        )
        assert response.status_code == 200, response.content
        infant.refresh_from_db()
        assert infant.outcome == Infant.Outcome.HIV_POSITIVE_ON_ART

    def test_refused_requires_a_barrier_note_and_keeps_the_outcome(
        self, api_client, pending_sample, infant
    ):
        linkage = self._positive_linkage(api_client, pending_sample, infant)
        response = api_client.patch(
            f"/api/v1/eid/linkages/{linkage.pk}/",
            {"status": "REFUSED"},
            format="json",
        )
        assert response.status_code == 400

        response = api_client.patch(
            f"/api/v1/eid/linkages/{linkage.pk}/",
            {"status": "REFUSED", "barrier_note": "Caregiver declined at the visit."},
            format="json",
        )
        assert response.status_code == 200
        infant.refresh_from_db()
        # The infant is still awaiting linkage; the weekly alarm keeps firing.
        assert infant.outcome == Infant.Outcome.HIV_POSITIVE_NOT_LINKED


@pytest.mark.django_db
class TestInfantRegistration:
    def test_registering_an_infant_builds_the_testing_schedule(
        self, api_client, client_row, facility, programme_settings
    ):
        response = api_client.post(
            "/api/v1/registry/infants/",
            {
                "mother": client_row.client_code,
                "facility": str(facility.pk),
                "date_of_birth": str(dt.date.today()),
                "sex": "F",
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        baby_code = response.json()["baby_code"]
        infant = Infant.objects.get(baby_code=baby_code)
        milestones = set(
            infant.eid_appointments.values_list("milestone", flat=True)
        )
        assert milestones == {"BIRTH", "WEEK_6", "MONTH_9", "MONTH_18"}

    def test_an_infant_cannot_be_registered_without_the_mothers_consent(
        self, api_client, facility, mentor_mother
    ):
        from apps.registry.models import Client

        no_consent = Client.objects.create(
            facility=facility,
            mentor_mother=mentor_mother,
            client_code="CL-NOCONSENT",
            full_name="Test Client",
        )
        response = api_client.post(
            "/api/v1/registry/infants/",
            {
                "mother": no_consent.client_code,
                "facility": str(facility.pk),
                "date_of_birth": str(dt.date.today()),
                "sex": "M",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_a_client_cannot_be_created_without_consent(self, api_client, facility):
        response = api_client.post(
            "/api/v1/registry/clients/",
            {
                "facility": str(facility.pk),
                "client_code": "CL-API-001",
                "full_name": "A Person",
            },
            format="json",
        )
        assert response.status_code == 400

        response = api_client.post(
            "/api/v1/registry/clients/",
            {
                "facility": str(facility.pk),
                "client_code": "CL-API-001",
                "full_name": "A Person",
                "consent_given_at": timezone.now().isoformat(),
                "consent_form_reference": "CONSENT-2026-001",
            },
            format="json",
        )
        assert response.status_code == 201, response.content
