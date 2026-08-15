"""
Tests for home visit location verification.

The M and E target is at least 85 percent of visits carrying a verified
location record. The target is not 100 percent, and these tests pin down the
honest reasons a genuine visit fails verification. A design that treated every
failure as misconduct would be both wrong and harmful.
"""

import pytest

from apps.visits.models import haversine_metres

PROGRAMME = {
    "LOCATION_MATCH_RADIUS_METRES": 250,
    "LOCATION_MAX_ACCURACY_METRES": 100,
    "LOCATION_VERIFIED_TARGET_PERCENT": 85,
    "ALERT_SLA_HOURS": {"CRITICAL": 4, "HIGH": 24, "MEDIUM": 72, "LOW": 168},
    "EID_SCHEDULE_WEEKS": {"BIRTH": 0, "WEEK_6": 6, "MONTH_9": 39, "MONTH_18": 78},
    "SYNC_BACKLOG_ALARM_HOURS": 72,
}


class TestDistanceCalculation:
    def test_the_same_point_is_zero_metres_apart(self):
        assert haversine_metres(9.0765, 7.3986, 9.0765, 7.3986) == pytest.approx(0)

    def test_a_known_separation_is_measured_correctly(self):
        # Abuja city centre to Gwagwalada, 38 km on the great circle.
        distance = haversine_metres(9.0765, 7.3986, 8.9434, 7.0800)
        assert distance == pytest.approx(38_000, rel=0.02)

    def test_a_short_separation_is_measured_correctly(self):
        # One tenth of a degree of latitude is close to 11.1 km.
        distance = haversine_metres(9.0000, 7.4000, 9.1000, 7.4000)
        assert distance == pytest.approx(11_119, rel=0.01)


@pytest.mark.django_db
@pytest.mark.usefixtures("programme_settings")
class TestVerdicts:
    def test_a_position_inside_the_radius_verifies(self, visit_factory):
        visit = visit_factory(
            household=(9.0765, 7.3986),
            reported=(9.0770, 7.3990),  # Roughly 70 m away.
            accuracy=20,
        )
        assert visit.evaluate_location(save=False) == "VERIFIED"

    def test_a_position_outside_the_radius_is_flagged(self, visit_factory):
        visit = visit_factory(
            household=(9.0765, 7.3986),
            reported=(9.0900, 7.4100),  # Roughly 1.9 km away.
            accuracy=20,
        )
        assert visit.evaluate_location(save=False) == "OUT_OF_RANGE"
        assert visit.flagged_for_review

    def test_an_imprecise_reading_is_not_treated_as_a_failure(self, visit_factory):
        # A weak satellite fix under a roof is not misconduct.
        visit = visit_factory(
            household=(9.0765, 7.3986), reported=(9.0765, 7.3986), accuracy=400
        )
        assert visit.evaluate_location(save=False) == "LOW_ACCURACY"
        assert not visit.flagged_for_review

    def test_no_fix_is_recorded_without_a_flag(self, visit_factory):
        visit = visit_factory(household=(9.0765, 7.3986), reported=None, accuracy=None)
        assert visit.evaluate_location(save=False) == "NO_FIX"
        assert not visit.flagged_for_review

    def test_a_missing_household_point_is_not_the_workers_fault(self, visit_factory):
        # The household point was never captured at enrolment.
        visit = visit_factory(
            household=None, reported=(9.0765, 7.3986), accuracy=20
        )
        assert visit.evaluate_location(save=False) == "NO_REFERENCE"
        assert not visit.flagged_for_review
