"""The provisioning command: one act creates both halves of an identity."""

from unittest import mock

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import Role, User

SUB = "11111111-2222-3333-4444-555555555555"


def _idp_mock(existing: bool = False):
    idp = mock.MagicMock()
    idp.exceptions.UsernameExistsException = type(
        "UsernameExistsException", (Exception,), {}
    )
    if existing:
        idp.admin_create_user.side_effect = idp.exceptions.UsernameExistsException()
        idp.admin_get_user.return_value = {
            "UserAttributes": [{"Name": "sub", "Value": SUB}]
        }
    else:
        idp.admin_create_user.return_value = {
            "User": {"Attributes": [{"Name": "sub", "Value": SUB}]}
        }
    return idp


@pytest.fixture
def cognito_settings(settings):
    settings.COGNITO = {
        **settings.COGNITO,
        "USER_POOL_ID": "af-south-1_TESTPOOL",
        "REGION": "af-south-1",
    }
    return settings


@pytest.mark.django_db
class TestProvisionUser:
    def test_provisions_a_supervisor_end_to_end(self, cognito_settings, facility):
        idp = _idp_mock()
        with mock.patch("boto3.client", return_value=idp):
            call_command(
                "provision_user", "aisha.bello", "FACILITY_SUPERVISOR",
                "--phone", "+2348012345678", "--full-name", "Aisha Bello",
                "--facility-code", facility.code,
            )
        user = User.objects.get(cognito_sub=SUB)
        assert user.role == Role.FACILITY_SUPERVISOR
        assert user.facility == facility
        idp.admin_add_user_to_group.assert_called_once()
        assert (
            idp.admin_add_user_to_group.call_args.kwargs["GroupName"]
            == "facility-supervisor"
        )
        # The command never sees a password: Cognito delivers it itself.
        assert "TemporaryPassword" not in idp.admin_create_user.call_args.kwargs

    def test_an_existing_cognito_account_is_adopted_not_duplicated(
        self, cognito_settings, facility
    ):
        idp = _idp_mock(existing=True)
        with mock.patch("boto3.client", return_value=idp):
            call_command(
                "provision_user", "aisha.bello", "FACILITY_SUPERVISOR",
                "--phone", "+2348012345678", "--full-name", "Aisha Bello",
                "--facility-code", facility.code,
            )
        assert User.objects.filter(cognito_sub=SUB).count() == 1
        idp.admin_get_user.assert_called_once()

    def test_rerunning_updates_the_row_in_place(self, cognito_settings, facility):
        for _ in range(2):
            with mock.patch("boto3.client", return_value=_idp_mock()):
                call_command(
                    "provision_user", "aisha.bello", "FACILITY_SUPERVISOR",
                    "--phone", "+2348012345678", "--full-name", "Aisha Bello",
                    "--facility-code", facility.code,
                )
        assert User.objects.filter(cognito_sub=SUB).count() == 1

    def test_a_missing_scope_code_is_a_clear_error_before_cognito_is_touched(
        self, cognito_settings, facility
    ):
        idp = _idp_mock()
        with mock.patch("boto3.client", return_value=idp):
            with pytest.raises(CommandError, match="facility-code"):
                call_command(
                    "provision_user", "aisha.bello", "FACILITY_SUPERVISOR",
                    "--phone", "+2348012345678", "--full-name", "Aisha Bello",
                )
        idp.admin_create_user.assert_not_called()

    def test_an_unknown_code_names_the_fix(self, cognito_settings):
        with mock.patch("boto3.client", return_value=_idp_mock()):
            with pytest.raises(CommandError, match="seed_geography"):
                call_command(
                    "provision_user", "aisha.bello", "FACILITY_SUPERVISOR",
                    "--phone", "+2348012345678", "--full-name", "Aisha Bello",
                    "--facility-code", "NOPE-01",
                )

    def test_dry_run_touches_nothing(self, cognito_settings, facility):
        with mock.patch("boto3.client") as client:
            call_command(
                "provision_user", "aisha.bello", "FACILITY_SUPERVISOR",
                "--phone", "+2348012345678", "--full-name", "Aisha Bello",
                "--facility-code", facility.code, "--dry-run",
            )
        client.assert_not_called()
        assert not User.objects.filter(username="aisha.bello").exists()
