"""The pilot geography fixture: Ogun and Plateau, decided September 2026."""

import pytest
from django.core.management import call_command

from apps.registry.models import LocalGovernmentArea, State


@pytest.mark.django_db
class TestGeographySeed:
    def test_the_fixture_loads_the_two_pilot_states(self):
        call_command("seed_geography")
        assert set(
            State.objects.filter(is_pilot_site=True).values_list("name", flat=True)
        ) == {"Ogun", "Plateau"}
        assert LocalGovernmentArea.objects.filter(state__code="OG").count() == 20
        assert LocalGovernmentArea.objects.filter(state__code="PL").count() == 17

    def test_the_seed_is_idempotent(self):
        call_command("seed_geography")
        call_command("seed_geography")
        assert State.objects.count() == 2
        assert LocalGovernmentArea.objects.count() == 37

    def test_a_rerun_corrects_a_name_but_keeps_flags_set_in_the_admin(self):
        call_command("seed_geography")
        lga = LocalGovernmentArea.objects.get(code="OG-01")
        lga.name = "Misspelled"
        lga.is_pilot_site = True  # a decision made by programme management
        lga.save()

        call_command("seed_geography")
        lga.refresh_from_db()
        assert lga.name == "Abeokuta North"
        # The fixture carries no flag for this LGA, so the admin's decision
        # survives the re-run.
        assert lga.is_pilot_site is True

    def test_no_state_name_appears_in_the_code(self):
        # The rule from the README: geography is data, not code. The fixture
        # and this test are the only places a pilot state may be named.
        import pathlib

        backend = pathlib.Path(__file__).resolve().parents[1]
        offenders = []
        for path in backend.rglob("*.py"):
            if ".venv" in path.parts or path.name == "test_geography_seed.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for name in ("Ogun", "Plateau"):
                if name in text:
                    offenders.append(f"{path.name}: {name}")
        assert offenders == []
