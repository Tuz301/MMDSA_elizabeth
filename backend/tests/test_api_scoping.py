"""
The four rules, exercised through the API.

Deny-by-default scoping, split responsibility, identifier gating and the
absence of telephone numbers from the messaging surface. Each test names the
rule it protects, so a failure reads as a policy violation, not a flake.
"""


import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.registry.models import Client, Facility, LocalGovernmentArea, State


@pytest.fixture
def other_facility(db):
    state = State.objects.create(name="Other State", code="OS", is_pilot_site=True)
    lga = LocalGovernmentArea.objects.create(
        state=state, name="Other LGA", code="OS-01", is_pilot_site=True
    )
    return Facility.objects.create(
        lga=lga, name="Other Health Centre", code="OS-01-001", level="PRIMARY"
    )


@pytest.fixture
def other_client_row(other_facility):
    return Client.objects.create(
        facility=other_facility,
        client_code="CL-OTHER-001",
        full_name="Other Client",
    )


def _client_for(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def lga_coordinator(facility):
    return User.objects.create(
        username="coordinator-test",
        role=Role.LGA_COORDINATOR,
        lga=facility.lga,
        cognito_sub="test-sub-coordinator",
    )


@pytest.fixture
def mm_user(facility, mentor_mother):
    user = User.objects.create(
        username="mm-test",
        role=Role.MENTOR_MOTHER,
        facility=facility,
        cognito_sub="test-sub-mm",
    )
    mentor_mother.user = user
    mentor_mother.save(update_fields=["user"])
    return user


@pytest.mark.django_db
class TestRowScoping:
    def test_a_supervisor_sees_only_their_facilitys_clients(
        self, api_client, client_row, other_client_row
    ):
        response = api_client.get("/api/v1/registry/clients/")
        codes = [row["client_code"] for row in response.json()["results"]]
        assert codes == ["CL-TEST-001"]

    def test_an_out_of_scope_row_is_a_404_not_a_403(
        self, api_client, other_client_row
    ):
        # A 403 would confirm the row exists. The scoped queryset makes it
        # indistinguishable from a row that never existed.
        response = api_client.get(
            f"/api/v1/registry/clients/{other_client_row.pk}/"
        )
        assert response.status_code == 404

    def test_a_write_cannot_target_an_out_of_scope_facility(
        self, api_client, other_facility
    ):
        from django.utils import timezone

        response = api_client.post(
            "/api/v1/registry/clients/",
            {
                "facility": str(other_facility.pk),
                "client_code": "CL-SMUGGLED",
                "full_name": "A Person",
                "consent_given_at": timezone.now().isoformat(),
                "consent_form_reference": "C-1",
            },
            format="json",
        )
        assert response.status_code == 400
        assert not Client.objects.filter(client_code="CL-SMUGGLED").exists()

    def test_a_mentor_mother_sees_only_her_own_caseload(
        self, mm_user, client_row, facility
    ):
        # A second client at the same facility, assigned to nobody. Her scope
        # is her caseload, not her facility.
        Client.objects.create(
            facility=facility,
            client_code="CL-UNASSIGNED",
            full_name="Unassigned Client",
        )
        response = _client_for(mm_user).get("/api/v1/registry/clients/")
        codes = [row["client_code"] for row in response.json()["results"]]
        assert codes == ["CL-TEST-001"]


@pytest.mark.django_db
class TestSplitResponsibility:
    def test_a_mentor_mother_reads_samples_but_cannot_write_them(
        self, mm_user, unacknowledged_positive
    ):
        api = _client_for(mm_user)
        assert api.get("/api/v1/eid/samples/").status_code == 200
        response = api.post(
            f"/api/v1/eid/samples/{unacknowledged_positive.pk}/acknowledge/"
        )
        assert response.status_code == 403

    def test_a_mentor_mother_cannot_enrol_a_client(self, mm_user, facility):
        from django.utils import timezone

        response = _client_for(mm_user).post(
            "/api/v1/registry/clients/",
            {
                "facility": str(facility.pk),
                "client_code": "CL-BY-MM",
                "consent_given_at": timezone.now().isoformat(),
                "consent_form_reference": "C-2",
            },
            format="json",
        )
        assert response.status_code == 403

    def test_a_mentor_mother_acknowledges_her_own_alert_but_cannot_resolve_it(
        self, mm_user, open_alert
    ):
        api = _client_for(mm_user)
        response = api.post(f"/api/v1/alerts/{open_alert.pk}/acknowledge/")
        assert response.status_code == 200
        open_alert.refresh_from_db()
        assert open_alert.status == open_alert.Status.ACKNOWLEDGED

        response = api.post(
            f"/api/v1/alerts/{open_alert.pk}/resolve/",
            {"note": "done"},
            format="json",
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestIdentifierGating:
    def test_a_supervisor_reads_identifiers(self, api_client, client_row):
        row = api_client.get(
            f"/api/v1/registry/clients/{client_row.pk}/"
        ).json()
        assert row["full_name"] == "Test Client"

    def test_an_lga_coordinator_receives_codes_only(
        self, lga_coordinator, client_row
    ):
        row = _client_for(lga_coordinator).get(
            f"/api/v1/registry/clients/{client_row.pk}/"
        ).json()
        assert row["client_code"] == "CL-TEST-001"
        # The identifier keys are absent, not blanked, so a client cannot
        # mistake an empty string for a cleared value.
        for gated in (
            "full_name", "phone_number", "household_address",
            "household_latitude", "household_longitude", "hospital_number",
        ):
            assert gated not in row

    def test_gated_fields_are_ignored_on_input_from_a_gated_role(
        self, lga_coordinator, client_row
    ):
        response = _client_for(lga_coordinator).patch(
            f"/api/v1/registry/clients/{client_row.pk}/",
            {"full_name": "Injected Name", "status": "ACTIVE"},
            format="json",
        )
        assert response.status_code == 200
        client_row.refresh_from_db()
        assert client_row.full_name == "Test Client"


@pytest.mark.django_db
class TestMessagingSurface:
    def test_no_message_payload_carries_a_telephone_number(
        self, api_client, sent_message
    ):
        for url in ("/api/v1/messaging/outbound/",
                    f"/api/v1/messaging/outbound/{sent_message.pk}/"):
            body = api_client.get(url).content.decode()
            assert "+234" not in body
            assert "msisdn" not in body

    def test_an_unauthenticated_caller_gets_nothing(self, sent_message, client_row):
        api = APIClient()
        for url in (
            "/api/v1/registry/clients/",
            "/api/v1/messaging/outbound/",
            "/api/v1/alerts/",
            "/api/v1/metrics/summary/",
        ):
            assert api.get(url).status_code in (401, 403)


@pytest.mark.django_db
class TestAlertOrderingAndSummary:
    def test_critical_alerts_sort_first(self, api_client, facility, open_alert):
        from apps.alerts.models import Alert, AlertRule

        low_rule = AlertRule.objects.create(
            alert_type="EID_DUE", severity="LOW", threshold_value=3
        )
        Alert.objects.create(
            rule=low_rule,
            alert_type="EID_DUE",
            severity="LOW",
            facility=facility,
            subject_type="registry.Facility",
            subject_id=facility.pk,
            deduplication_key="low-1",
            title="A test appointment falls due",
        )
        response = api_client.get("/api/v1/alerts/")
        severities = [row["severity"] for row in response.json()["results"]]
        # The model's lexicographic Meta ordering would put LOW before
        # CRITICAL. The API must not.
        assert severities == ["CRITICAL", "LOW"]

    def test_the_summary_reports_the_unacknowledged_positive(
        self, api_client, unacknowledged_positive
    ):
        response = api_client.get("/api/v1/metrics/summary/")
        assert response.status_code == 200
        body = response.json()
        assert body["eid_cascade"]["unacknowledged_positives_now"] == 1

    def test_the_summary_is_scoped(self, lga_coordinator, unacknowledged_positive):
        # The coordinator's LGA contains the sample's facility, so the number
        # appears; a coordinator elsewhere would see zero.
        response = _client_for(lga_coordinator).get("/api/v1/metrics/summary/")
        assert response.json()["eid_cascade"]["unacknowledged_positives_now"] == 1


@pytest.mark.django_db
class TestDenyByDefault:
    def test_an_unregistered_model_returns_no_rows(self, supervisor):
        # The rule the README names: a model absent from SCOPE_PATHS yields
        # an empty queryset, never the full table.
        from apps.accounts.scoping import scope_queryset
        from apps.audit.models import AuditLog

        assert scope_queryset(AuditLog.objects.all(), supervisor).count() == 0
