"""
Tests for the two way SMS loop and the alert engine.

The reply loop is the causal mechanism of the whole intervention. A supervisor
enters a result, an alert is raised, a mentor mother is messaged, she acts, and
her reply closes the loop. If any link fails the theory of change fails with
it, and no amount of good architecture repairs that.

The deduplication tests matter for a different reason. The engine runs every
fifteen minutes. An engine that re-raises the same alert ninety-six times a day
trains supervisors to ignore alerts, and an ignored alert is worse than no
alert, because it looks like the system is working.
"""

import datetime as dt

import pytest
from django.utils import timezone


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReplyParsing:
    """
    Real replies are messy. They arrive in lower case, with extra words, with
    the keyword and the code in either order.

    The parser is forgiving about everything except the code, which must match
    an infant exactly. Acting on the wrong infant is worse than failing to
    parse, so an ambiguous code yields nothing rather than a guess.
    """

    def test_a_clean_reply_is_parsed(self, infant):
        from apps.messaging.gateway import parse_reply

        keyword, code = parse_reply(f"DONE {infant.baby_code}")
        assert keyword == "DONE"
        assert code == infant.baby_code

    def test_a_lower_case_reply_is_parsed(self, infant):
        from apps.messaging.gateway import parse_reply

        keyword, code = parse_reply(f"done {infant.baby_code.lower()}")
        assert keyword == "DONE"
        assert code == infant.baby_code

    def test_extra_words_do_not_prevent_parsing(self, infant):
        from apps.messaging.gateway import parse_reply

        keyword, code = parse_reply(
            f"ok i have visited the family today {infant.baby_code} thank you"
        )
        assert keyword == "OK"
        assert code == infant.baby_code

    def test_the_order_of_keyword_and_code_does_not_matter(self, infant):
        from apps.messaging.gateway import parse_reply

        keyword, code = parse_reply(f"{infant.baby_code} SEEN")
        assert keyword == "SEEN"
        assert code == infant.baby_code

    def test_an_unknown_code_is_not_returned(self, db):
        from apps.messaging.gateway import parse_reply

        # The code is well formed but matches no infant. Returning it would let
        # a stray message act on a record that does not exist.
        keyword, code = parse_reply("DONE BZZZZZZZ")
        assert keyword == "DONE"
        assert code == ""

    def test_a_keyword_without_a_code_still_parses(self, db):
        from apps.messaging.gateway import parse_reply

        keyword, code = parse_reply("DONE")
        assert keyword == "DONE"
        assert code == ""

    def test_an_empty_reply_yields_nothing(self, db):
        from apps.messaging.gateway import parse_reply

        assert parse_reply("") == ("", "")
        assert parse_reply("...") == ("", "")


@pytest.mark.django_db
class TestReplyClosesTheLoop:
    def test_a_reply_acknowledges_the_alert(self, open_alert, sent_message):
        from apps.messaging.gateway import handle_inbound
        from apps.messaging.models import InboundMessage

        inbound = handle_inbound("+2348010000000", "DONE")

        assert inbound.parse_status == InboundMessage.ParseStatus.MATCHED
        open_alert.refresh_from_db()
        assert open_alert.status == open_alert.Status.ACKNOWLEDGED
        assert open_alert.acknowledgement_channel == "SMS"

    def test_a_negative_reply_does_not_acknowledge(self, open_alert, sent_message):
        from apps.messaging.gateway import handle_inbound

        # "NO" means she could not act. Treating that as an acknowledgement
        # would close an alert about an infant nobody has reached.
        handle_inbound("+2348010000000", "NO")

        open_alert.refresh_from_db()
        assert open_alert.status == open_alert.Status.OPEN

    def test_an_unmatched_reply_is_kept_not_discarded(self, db):
        from apps.messaging.gateway import handle_inbound
        from apps.messaging.models import InboundMessage

        # A pattern of unmatched replies usually means the instruction text is
        # unclear, and that is information the programme needs.
        inbound = handle_inbound("+2349999999999", "DONE")
        assert inbound.parse_status == InboundMessage.ParseStatus.UNMATCHED
        assert inbound.pk is not None


# ---------------------------------------------------------------------------
# Alert deduplication
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAlertDeduplication:
    def test_running_the_engine_twice_raises_one_alert(self, unacknowledged_positive):
        from apps.alerts.engine import positive_result_unacknowledged
        from apps.alerts.models import Alert

        rule = _rule("POS_UNACK", "CRITICAL", threshold=2, unit="HOURS")

        assert positive_result_unacknowledged(rule) == 1
        assert positive_result_unacknowledged(rule) == 0
        assert Alert.objects.filter(alert_type="POS_UNACK").count() == 1

    def test_a_result_inside_the_threshold_raises_nothing(self, sample_factory):
        from apps.alerts.engine import positive_result_unacknowledged

        # Entered thirty minutes ago. A supervisor has not had time to act, and
        # alerting now would be noise.
        sample_factory(entered_hours_ago=0.5)
        rule = _rule("POS_UNACK", "CRITICAL", threshold=2, unit="HOURS")
        assert positive_result_unacknowledged(rule) == 0

    def test_an_acknowledged_result_raises_nothing(self, sample_factory):
        from apps.alerts.engine import positive_result_unacknowledged

        sample_factory(entered_hours_ago=48, acknowledged=True)
        rule = _rule("POS_UNACK", "CRITICAL", threshold=2, unit="HOURS")
        assert positive_result_unacknowledged(rule) == 0

    def test_a_negative_result_raises_nothing(self, sample_factory):
        from apps.alerts.engine import positive_result_unacknowledged

        sample_factory(entered_hours_ago=48, result="NEGATIVE")
        rule = _rule("POS_UNACK", "CRITICAL", threshold=2, unit="HOURS")
        assert positive_result_unacknowledged(rule) == 0


@pytest.mark.django_db
class TestAlertDeadlines:
    def test_the_deadline_comes_from_the_severity(self, programme_settings, facility):

        alert = _alert(facility, severity="CRITICAL")
        elapsed = (alert.acknowledge_by - alert.raised_at).total_seconds() / 3600
        assert elapsed == pytest.approx(4, abs=0.01)

        alert = _alert(facility, severity="LOW", key="dedup-low")
        elapsed = (alert.acknowledge_by - alert.raised_at).total_seconds() / 3600
        assert elapsed == pytest.approx(168, abs=0.01)

    def test_an_alert_past_its_deadline_is_reported_as_such(
        self, programme_settings, facility
    ):
        alert = _alert(facility, severity="CRITICAL")
        alert.raised_at = timezone.now() - dt.timedelta(hours=10)
        alert.acknowledge_by = alert.raised_at + dt.timedelta(hours=4)
        alert.save()
        assert alert.is_past_deadline

    def test_an_acknowledged_alert_is_not_past_its_deadline(
        self, programme_settings, facility
    ):
        alert = _alert(facility, severity="CRITICAL")
        alert.raised_at = timezone.now() - dt.timedelta(hours=10)
        alert.acknowledge_by = alert.raised_at + dt.timedelta(hours=4)
        alert.save()
        alert.acknowledge()
        assert not alert.is_past_deadline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule(alert_type: str, severity: str, *, threshold: int, unit: str):
    from apps.alerts.models import AlertRule

    return AlertRule.objects.create(
        alert_type=alert_type,
        severity=severity,
        threshold_value=threshold,
        threshold_unit=unit,
    )


def _alert(facility, *, severity: str, key: str = "dedup-test"):
    from apps.alerts.models import Alert

    rule = _rule(f"T{key[-3:]}", severity, threshold=1, unit="COUNT")
    return Alert.objects.create(
        rule=rule,
        alert_type="POS_UNACK",
        severity=severity,
        facility=facility,
        subject_type="registry.Facility",
        subject_id=facility.pk,
        deduplication_key=key,
        title="Test alert",
    )
