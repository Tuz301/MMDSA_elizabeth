"""
Provision one health worker: the Cognito account and the Django row, together.

Authentication rejects any token whose subject has no Django row, so a user
created in only one of the two systems cannot sign in. Doing the pair by hand
in two consoles is exactly the error-prone process that locks a rural health
worker out in week one. This command does both, links them by the Cognito
subject, and is safe to re-run: an existing Cognito account is looked up
rather than duplicated, and the Django row is updated in place.

The instance role already carries the cognito-idp:Admin* permissions this
needs; run it from an application instance or any credentialed shell:

    python manage.py provision_user aisha.bello FACILITY_SUPERVISOR \
        --phone +2348012345678 --email aisha@example.org \
        --full-name "Aisha Bello" --facility-code OG-05-001
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Role, User

#: Cognito group per role, mirroring the groups the AppStack creates.
GROUP_BY_ROLE = {
    Role.MENTOR_MOTHER: "mentor-mother",
    Role.FACILITY_SUPERVISOR: "facility-supervisor",
    Role.LGA_COORDINATOR: "lga-coordinator",
    Role.STATE_MANAGER: "state-manager",
    Role.SYSTEM_ADMIN: "system-admin",
}


class Command(BaseCommand):
    help = "Create or link a Cognito account and its Django user row."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("role", choices=[r.value for r in Role])
        parser.add_argument("--phone", required=True,
                            help="E.164 number the temporary password is sent to.")
        parser.add_argument("--email", default="")
        parser.add_argument("--full-name", required=True)
        parser.add_argument("--facility-code", default="")
        parser.add_argument("--lga-code", default="")
        parser.add_argument("--state-code", default="")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Validate and report without touching Cognito or the database.",
        )

    def handle(self, *args, **options):
        role = Role(options["role"])
        scope = self._resolve_scope(role, options)

        pool_id = settings.COGNITO["USER_POOL_ID"]
        if not pool_id:
            raise CommandError(
                "COGNITO_USER_POOL_ID is not set. Provisioning needs the pool."
            )

        if options["dry_run"]:
            self.stdout.write(
                f"Would provision {options['username']} as "
                f"{role.label} with scope {scope or 'none'}."
            )
            return

        import boto3

        idp = boto3.client("cognito-idp", region_name=settings.COGNITO["REGION"])
        sub = self._ensure_cognito_account(idp, pool_id, role, options)

        with transaction.atomic():
            user, created = User.objects.update_or_create(
                cognito_sub=sub,
                defaults={
                    "username": options["username"],
                    "email": options["email"],
                    "role": role,
                    "phone_number": options["phone"],
                    **scope,
                },
            )
            user.full_clean(exclude=["password"])
            user.save()

        self.stdout.write(
            f"{'Created' if created else 'Updated'} {user.username} "
            f"({role.label}), Cognito subject {sub}."
        )

    # -- Cognito -------------------------------------------------------------

    def _ensure_cognito_account(self, idp, pool_id: str, role: Role, options) -> str:
        """Create the account, or adopt one that already exists."""
        username = options["username"]
        attributes = [
            {"Name": "phone_number", "Value": options["phone"]},
            {"Name": "name", "Value": options["full_name"]},
            {"Name": "custom:role", "Value": role.value},
        ]
        if options["email"]:
            attributes.append({"Name": "email", "Value": options["email"]})
        if options["facility_code"]:
            attributes.append(
                {"Name": "custom:facility_code", "Value": options["facility_code"]}
            )
        if options["lga_code"]:
            attributes.append({"Name": "custom:lga_code", "Value": options["lga_code"]})

        try:
            response = idp.admin_create_user(
                UserPoolId=pool_id,
                Username=username,
                UserAttributes=attributes,
                # Cognito generates and delivers the temporary password
                # itself. This command must never see, print or log one.
                DesiredDeliveryMediums=["SMS"],
            )
            cognito_user = response["User"]
        except idp.exceptions.UsernameExistsException:
            cognito_user = idp.admin_get_user(UserPoolId=pool_id, Username=username)
            idp.admin_update_user_attributes(
                UserPoolId=pool_id, Username=username, UserAttributes=attributes
            )

        idp.admin_add_user_to_group(
            UserPoolId=pool_id, Username=username, GroupName=GROUP_BY_ROLE[role]
        )

        for attribute in cognito_user.get(
            "Attributes", cognito_user.get("UserAttributes", [])
        ):
            if attribute["Name"] == "sub":
                return attribute["Value"]
        raise CommandError("Cognito returned no subject identifier.")

    # -- Scope ----------------------------------------------------------------

    def _resolve_scope(self, role: Role, options) -> dict:
        """Translate programme codes into the one scope row the role needs."""
        from apps.registry.models import Facility, LocalGovernmentArea, State

        def require(code: str, model, label: str):
            if not code:
                raise CommandError(
                    f"A {role.label} needs --{label.replace('_', '-')}."
                )
            row = model.objects.filter(code=code).first()
            if row is None:
                raise CommandError(
                    f"No {label} exists with the code {code!r}. "
                    f"Run seed_geography, or check the code."
                )
            return row

        if role in {Role.MENTOR_MOTHER, Role.FACILITY_SUPERVISOR}:
            return {
                "facility": require(options["facility_code"], Facility, "facility_code"),
                "lga": None, "state": None,
            }
        if role == Role.LGA_COORDINATOR:
            return {
                "lga": require(options["lga_code"], LocalGovernmentArea, "lga_code"),
                "facility": None, "state": None,
            }
        if role == Role.STATE_MANAGER:
            return {
                "state": require(options["state_code"], State, "state_code"),
                "facility": None, "lga": None,
            }
        return {"facility": None, "lga": None, "state": None}
