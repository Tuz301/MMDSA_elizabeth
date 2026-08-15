"""Test fixtures."""

import datetime as dt

import pytest
from django.utils import timezone


PROGRAMME_TEST_SETTINGS = {
    "LOCATION_MATCH_RADIUS_METRES": 250,
    "LOCATION_MAX_ACCURACY_METRES": 100,
    "LOCATION_VERIFIED_TARGET_PERCENT": 85,
    "ALERT_SLA_HOURS": {"CRITICAL": 4, "HIGH": 24, "MEDIUM": 72, "LOW": 168},
    "EID_SCHEDULE_WEEKS": {"BIRTH": 0, "WEEK_6": 6, "MONTH_9": 39, "MONTH_18": 78},
    "SYNC_BACKLOG_ALARM_HOURS": 72,
}


@pytest.fixture
def programme_settings(settings):
    """Pin the programme thresholds so a settings change cannot break a test."""
    settings.PROGRAMME = PROGRAMME_TEST_SETTINGS
    return settings.PROGRAMME


@pytest.fixture(autouse=True)
def encryption_key(settings):
    """
    Supply a throwaway encryption key for every test.

    A fresh key per run means no test fixture can be decrypted outside the run
    that created it.
    """
    from cryptography.fernet import Fernet

    settings.DATA_PROTECTION = {
        **settings.DATA_PROTECTION,
        "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
    return settings.DATA_PROTECTION["FIELD_ENCRYPTION_KEY"]


@pytest.fixture
def programme_geography(db):
    from apps.registry.models import Facility, LocalGovernmentArea, State

    # Placeholder geography. The real pilot states are loaded from a fixture,
    # never hard coded. See registry/fixtures/README.
    state = State.objects.create(name="Test State", code="TS", is_pilot_site=True)
    lga = LocalGovernmentArea.objects.create(
        state=state, name="Test LGA", code="TS-01", is_pilot_site=True
    )
    facility = Facility.objects.create(
        lga=lga, name="Test Health Centre", code="TS-01-001", level="PRIMARY"
    )
    return facility


@pytest.fixture
def visit_factory(programme_geography):
    from apps.registry.models import Client, MentorMother
    from apps.visits.models import HomeVisit

    counter = {"n": 0}

    def _make(*, household, reported, accuracy):
        counter["n"] += 1
        mentor_mother = MentorMother.objects.create(
            facility=programme_geography,
            staff_code=f"MM-TEST-{counter['n']:03d}",
            full_name="Test Worker",
            phone_number="+2348010000000",
        )
        client = Client.objects.create(
            facility=programme_geography,
            mentor_mother=mentor_mother,
            client_code=f"CL-TEST-{counter['n']:03d}",
            full_name="Test Client",
            household_latitude=household[0] if household else None,
            household_longitude=household[1] if household else None,
        )
        return HomeVisit(
            client=client,
            mentor_mother=mentor_mother,
            purpose="ROUTINE",
            result="COMPLETED",
            visit_date=dt.date.today(),
            latitude=reported[0] if reported else None,
            longitude=reported[1] if reported else None,
            location_accuracy_metres=accuracy,
        )

    return _make


@pytest.fixture
def facility(programme_geography):
    return programme_geography


@pytest.fixture
def mentor_mother(facility):
    from apps.registry.models import MentorMother

    return MentorMother.objects.create(
        facility=facility,
        staff_code="MM-TEST-001",
        full_name="Test Worker",
        phone_number="+2348010000000",
    )


@pytest.fixture
def client_row(facility, mentor_mother):
    from apps.registry.models import Client

    return Client.objects.create(
        facility=facility,
        mentor_mother=mentor_mother,
        client_code="CL-TEST-001",
        full_name="Test Client",
        consent_given_at=timezone.now(),
        sms_contact_permitted=True,
    )


@pytest.fixture
def infant(facility, client_row):
    from apps.registry.models import Infant

    return Infant.objects.create(
        mother=client_row,
        facility=facility,
        date_of_birth=dt.date.today() - dt.timedelta(days=60),
        sex="F",
    )


@pytest.fixture
def sample_factory(infant, programme_settings):
    """Build an EID sample positioned at a chosen point in the relay chain."""
    from apps.eid.models import EidAppointment, EidSample

    counter = {"n": 0}

    def _make(*, entered_hours_ago: float, result: str = "POSITIVE",
              acknowledged: bool = False):
        counter["n"] += 1
        appointment = EidAppointment.objects.create(
            infant=infant,
            milestone=["BIRTH", "WEEK_6", "MONTH_9", "MONTH_18"][counter["n"] % 4],
            due_date=dt.date.today() - dt.timedelta(days=14),
            status="ATTENDED",
        )
        entered = timezone.now() - dt.timedelta(hours=entered_hours_ago)
        return EidSample.objects.create(
            appointment=appointment,
            sample_identifier=f"SMP-{counter['n']:04d}",
            collected_on=dt.date.today() - dt.timedelta(days=14),
            dispatched_on=dt.date.today() - dt.timedelta(days=13),
            result_issued_on=dt.date.today() - dt.timedelta(days=3),
            result_entered_at=entered,
            result_acknowledged_at=timezone.now() if acknowledged else None,
            result=result,
        )

    return _make


@pytest.fixture
def unacknowledged_positive(sample_factory):
    """A positive result entered 24 hours ago that nobody has acknowledged."""
    return sample_factory(entered_hours_ago=24)


@pytest.fixture
def open_alert(facility, mentor_mother, programme_settings):
    from apps.alerts.models import Alert, AlertRule

    rule = AlertRule.objects.create(
        alert_type="POS_UNACK",
        severity="CRITICAL",
        threshold_value=2,
        threshold_unit="HOURS",
    )
    return Alert.objects.create(
        rule=rule,
        alert_type="POS_UNACK",
        severity="CRITICAL",
        facility=facility,
        assigned_mentor_mother=mentor_mother,
        subject_type="registry.Facility",
        subject_id=facility.pk,
        deduplication_key="loop-test",
        title="Follow up needed",
    )


@pytest.fixture
def sent_message(facility, mentor_mother, open_alert):
    """A message already sent to the mentor mother, awaiting her reply."""
    from apps.messaging.models import OutboundMessage

    return OutboundMessage.objects.create(
        alert=open_alert,
        facility=facility,
        recipient_kind="MENTOR_MOTHER",
        recipient_msisdn="+2348010000000",
        recipient_reference=mentor_mother.staff_code,
        template_key="urgent_follow_up",
        render_arguments={},
        status=OutboundMessage.Status.SENT,
        sent_at=timezone.now(),
        expects_reply=True,
    )


@pytest.fixture
def supervisor(facility):
    """A facility supervisor. May enter clinical data, scoped to one facility."""
    from apps.accounts.models import Role, User

    return User.objects.create(
        username="supervisor-test",
        role=Role.FACILITY_SUPERVISOR,
        facility=facility,
        cognito_sub="test-sub-supervisor",
    )


@pytest.fixture
def api_client(supervisor):
    """
    An authenticated API client.

    force_authenticate is used so that the tests exercise the sync logic rather
    than the Cognito token path, which is covered separately.
    """
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=supervisor)
    return client
