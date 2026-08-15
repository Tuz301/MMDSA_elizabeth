"""
Load the starting configuration: alert rules and message templates.

Geography is deliberately absent from this command. The pilot states, local
government areas and facilities are loaded from a fixture file, because the
pilot site is a programme decision that must not require a code change. See
apps/registry/fixtures/README.md.

The command is safe to run more than once. It creates what is missing and
leaves what exists, so a rerun after a deployment cannot overwrite a threshold
a programme manager has changed.
"""

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Load the default alert rules and SMS templates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help=(
                "Reset every rule and template to its default. This discards "
                "any threshold a programme manager has changed."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.alerts.engine import DEFAULT_RULES
        from apps.alerts.models import AlertRule
        from apps.messaging.models import MessageTemplate
        from apps.messaging.services import DEFAULT_TEMPLATES

        overwrite = options["overwrite"]
        created_rules = updated_rules = 0

        for spec in DEFAULT_RULES:
            spec = dict(spec)
            alert_type = spec.pop("alert_type")
            row, created = AlertRule.objects.get_or_create(
                alert_type=alert_type, defaults=spec
            )
            if created:
                created_rules += 1
            elif overwrite:
                for field, value in spec.items():
                    setattr(row, field, value)
                row.save()
                updated_rules += 1

        created_templates = blocked_templates = 0
        for spec in DEFAULT_TEMPLATES:
            spec = dict(spec)
            key, language = spec.pop("key"), spec.pop("language")

            # Every template passes the privacy guard before it is stored. A
            # default template that fails is a defect in this repository, and
            # the command reports it rather than loading it.
            from apps.messaging.guards import check_template_placeholders

            result = check_template_placeholders(spec["body"])
            if not result.passed:
                blocked_templates += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"Template {key}/{language} failed the privacy guard "
                        f"and was not loaded: {result.violations}"
                    )
                )
                continue

            _, created = MessageTemplate.objects.get_or_create(
                key=key, language=language, defaults=spec
            )
            if created:
                created_templates += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Alert rules: {created_rules} created, {updated_rules} updated. "
                f"Templates: {created_templates} created, "
                f"{blocked_templates} blocked."
            )
        )
        if blocked_templates:
            raise SystemExit(1)
