"""
Load the pilot geography from the fixture.

The states and local government areas live in
apps/registry/fixtures/geography.json, never in code. The command is
idempotent: rows are matched on their code, names are corrected in place, and
a state removed from the fixture is never deleted here — removal would
cascade into facilities and patient records and belongs to a supervised
administrative process, not a seed command.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.registry.models import LocalGovernmentArea, State

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "geography.json"


class Command(BaseCommand):
    help = "Load or refresh the pilot states and LGAs from the geography fixture."

    def handle(self, *args, **options):
        with open(FIXTURE, encoding="utf-8") as handle:
            data = json.load(handle)

        states_created = states_updated = 0
        lgas_created = lgas_updated = 0

        with transaction.atomic():
            for state_row in data["states"]:
                state, created = State.objects.update_or_create(
                    code=state_row["code"],
                    defaults={
                        "name": state_row["name"],
                        "is_pilot_site": state_row.get("is_pilot_site", False),
                    },
                )
                states_created += created
                states_updated += not created

                for lga_row in state_row["lgas"]:
                    defaults = {"state": state, "name": lga_row["name"]}
                    # Pilot and reserve flags are set by programme management
                    # in the admin once readiness is assessed. The seed only
                    # applies a flag the fixture states explicitly, so a
                    # re-run cannot silently clear a decision made later.
                    for flag in ("is_pilot_site", "is_reserve_site"):
                        if flag in lga_row:
                            defaults[flag] = lga_row[flag]
                    _, created = LocalGovernmentArea.objects.update_or_create(
                        code=lga_row["code"], defaults=defaults
                    )
                    lgas_created += created
                    lgas_updated += not created

        self.stdout.write(
            f"States: {states_created} created, {states_updated} updated. "
            f"LGAs: {lgas_created} created, {lgas_updated} updated."
        )
