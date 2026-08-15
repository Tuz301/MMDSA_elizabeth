"""
The rule evaluation engine.

Each rule is a function that returns the rows meeting its condition. The engine
turns each row into an alert, unless an identical alert is already open.

Two properties matter more than anything else here.

The engine must be safe to run repeatedly. It runs every fifteen minutes, and a
supervisor must not receive the same alert ninety-six times a day. The
deduplication key gives that property.

The engine must not stop on the first failure. One malformed row must not
prevent every other alert in the run from being raised, because an unraised
alert about a positive infant result is exactly the harm the pilot exists to
prevent. Each rule is therefore evaluated inside its own error boundary.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Callable

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Alert, AlertEscalation, AlertRule, AlertType, Severity

logger = logging.getLogger(__name__)

RuleFunction = Callable[[AlertRule], int]
_REGISTRY: dict[str, RuleFunction] = {}


def rule(alert_type: str) -> Callable[[RuleFunction], RuleFunction]:
    def decorator(function: RuleFunction) -> RuleFunction:
        _REGISTRY[alert_type] = function
        return function

    return decorator


def _raise_alert(
    *,
    rule_row: AlertRule,
    facility,
    subject,
    dedup_key: str,
    title: str,
    detail: str = "",
    mentor_mother=None,
) -> bool:
    """Create an alert unless one with the same key already exists."""
    if Alert.all_objects.filter(deduplication_key=dedup_key).exists():
        return False

    Alert.objects.create(
        rule=rule_row,
        alert_type=rule_row.alert_type,
        severity=rule_row.severity,
        facility=facility,
        assigned_mentor_mother=mentor_mother,
        subject_type=subject._meta.label,
        subject_id=subject.pk,
        deduplication_key=dedup_key,
        title=title,
        detail=detail,
    )
    return True


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@rule(AlertType.POSITIVE_RESULT_UNACKNOWLEDGED)
def positive_result_unacknowledged(rule_row: AlertRule) -> int:
    """
    A positive infant result has been entered and nobody has acknowledged it.

    This is the highest severity rule in the system. It watches the exact
    interval the pilot exists to shorten.
    """
    from apps.eid.models import EidSample

    cutoff = timezone.now() - timedelta(hours=rule_row.threshold_value)
    samples = EidSample.objects.filter(
        result=EidSample.Result.POSITIVE,
        result_acknowledged_at__isnull=True,
        result_entered_at__lt=cutoff,
    ).select_related("appointment__infant__facility", "appointment__infant__mother")

    raised = 0
    for sample in samples:
        infant = sample.appointment.infant
        hours = (timezone.now() - sample.result_entered_at).total_seconds() / 3600
        raised += _raise_alert(
            rule_row=rule_row,
            facility=infant.facility,
            subject=sample,
            dedup_key=f"POS_UNACK:{sample.pk}",
            title=f"Infant {infant.baby_code} needs immediate follow up",
            detail=(
                f"A result for {infant.baby_code} was entered "
                f"{hours:.0f} hours ago and has not been acknowledged. "
                f"Acknowledge it and record the linkage plan."
            ),
            mentor_mother=infant.mother.mentor_mother,
        )
    return raised


@rule(AlertType.ART_LINKAGE_OVERDUE)
def art_linkage_overdue(rule_row: AlertRule) -> int:
    """A positive infant has still not started treatment."""
    from apps.eid.models import ArtLinkage

    linkages = ArtLinkage.objects.filter(
        status=ArtLinkage.Status.PENDING
    ).select_related("infant__facility", "infant__mother", "triggering_sample")

    raised = 0
    for linkage in linkages:
        outstanding = linkage.days_outstanding
        if outstanding is None or outstanding < rule_row.threshold_value:
            continue
        # The key includes the week, so the alert repeats weekly while the
        # linkage stays open. A single alert that is acknowledged once and then
        # forgotten is how an infant is lost.
        week = timezone.localdate().isocalendar()
        raised += _raise_alert(
            rule_row=rule_row,
            facility=linkage.infant.facility,
            subject=linkage,
            dedup_key=f"LINK_OVERDUE:{linkage.pk}:{week[0]}-W{week[1]}",
            title=f"Infant {linkage.infant.baby_code} has not started treatment",
            detail=(
                f"{outstanding} days have passed since the result was issued. "
                f"Record the barrier if linkage is blocked."
            ),
            mentor_mother=linkage.infant.mother.mentor_mother,
        )
    return raised


@rule(AlertType.EID_APPOINTMENT_DUE)
def appointment_due(rule_row: AlertRule) -> int:
    """An infant test appointment falls due within the threshold."""
    from apps.eid.models import EidAppointment

    horizon = timezone.localdate() + timedelta(days=rule_row.threshold_value)
    appointments = EidAppointment.objects.filter(
        status=EidAppointment.Status.SCHEDULED,
        due_date__lte=horizon,
        due_date__gte=timezone.localdate(),
    ).select_related("infant__facility", "infant__mother")

    raised = 0
    for appointment in appointments:
        raised += _raise_alert(
            rule_row=rule_row,
            facility=appointment.infant.facility,
            subject=appointment,
            dedup_key=f"EID_DUE:{appointment.pk}",
            title=(
                f"{appointment.infant.baby_code} is due for a test on "
                f"{appointment.due_date:%d %B}"
            ),
            detail=f"Milestone: {appointment.get_milestone_display()}.",
            mentor_mother=appointment.infant.mother.mentor_mother,
        )
    return raised


@rule(AlertType.EID_APPOINTMENT_MISSED)
def appointment_missed(rule_row: AlertRule) -> int:
    """An appointment passed and no sample was collected."""
    from apps.eid.models import EidAppointment

    cutoff = timezone.localdate() - timedelta(days=rule_row.threshold_value)
    appointments = EidAppointment.objects.filter(
        status=EidAppointment.Status.SCHEDULED, due_date__lt=cutoff
    ).select_related("infant__facility", "infant__mother")

    raised = 0
    for appointment in appointments:
        raised += _raise_alert(
            rule_row=rule_row,
            facility=appointment.infant.facility,
            subject=appointment,
            dedup_key=f"EID_MISSED:{appointment.pk}",
            title=f"{appointment.infant.baby_code} missed a test appointment",
            detail=f"The appointment was due {appointment.days_overdue} days ago.",
            mentor_mother=appointment.infant.mother.mentor_mother,
        )
    return raised


@rule(AlertType.SAMPLE_RESULT_OVERDUE)
def sample_result_overdue(rule_row: AlertRule) -> int:
    """
    A sample has been at the laboratory longer than the expected turnaround.

    This alert is directed at the supervisor so that the sample can be chased.
    The delay itself is a laboratory problem, and the pilot report must not
    count it as a relay delay.
    """
    from apps.eid.models import EidSample

    cutoff = timezone.localdate() - timedelta(days=rule_row.threshold_value)
    samples = EidSample.objects.filter(
        result=EidSample.Result.PENDING,
        dispatched_on__isnull=False,
        dispatched_on__lt=cutoff,
    ).select_related("appointment__infant__facility")

    raised = 0
    for sample in samples:
        days = (timezone.localdate() - sample.dispatched_on).days
        raised += _raise_alert(
            rule_row=rule_row,
            facility=sample.appointment.infant.facility,
            subject=sample,
            dedup_key=f"SAMPLE_OVERDUE:{sample.pk}:{timezone.localdate():%Y-%W}",
            title=f"Sample {sample.sample_identifier} has no result",
            detail=f"Dispatched {days} days ago. Contact the laboratory.",
        )
    return raised


@rule(AlertType.VISIT_FLAGGED_FOR_REVIEW)
def visit_flagged(rule_row: AlertRule) -> int:
    """
    A visit was flagged because its position did not match the household.

    The wording matters. This alert asks the supervisor to check whether the
    registered household point is correct. In practice a wrong household point
    is the most common cause, and an alert phrased as an accusation would damage
    the relationship the whole mentor mother model depends on.
    """
    from apps.visits.models import HomeVisit

    visits = HomeVisit.objects.filter(
        flagged_for_review=True, reviewed_at__isnull=True
    ).select_related("client__facility", "mentor_mother")

    raised = 0
    for visit in visits:
        raised += _raise_alert(
            rule_row=rule_row,
            facility=visit.client.facility,
            subject=visit,
            dedup_key=f"VISIT_FLAG:{visit.pk}",
            title=f"Confirm the household point for {visit.client.client_code}",
            detail=(
                f"{visit.review_reason} Check the registered household position "
                f"with the mentor mother before drawing any conclusion."
            ),
            mentor_mother=visit.mentor_mother,
        )
    return raised


@rule(AlertType.SYNC_BACKLOG)
def sync_backlog(rule_row: AlertRule) -> int:
    """A handset holds visit records that have not reached the server."""
    from apps.visits.models import HomeVisit

    cutoff = timezone.now() - timedelta(hours=rule_row.threshold_value)
    stale = (
        HomeVisit.objects.filter(
            server_received_at__isnull=True, client_created_at__lt=cutoff
        )
        .select_related("mentor_mother__facility")
        .order_by("mentor_mother_id", "client_created_at")
    )

    seen: set = set()
    raised = 0
    for visit in stale:
        mentor_mother = visit.mentor_mother
        if mentor_mother.pk in seen:
            continue
        seen.add(mentor_mother.pk)
        raised += _raise_alert(
            rule_row=rule_row,
            facility=mentor_mother.facility,
            subject=mentor_mother,
            dedup_key=f"SYNC_BACKLOG:{mentor_mother.pk}:{timezone.localdate()}",
            title=f"Handset for {mentor_mother.staff_code} has not synchronised",
            detail=(
                "Visit records are held on the handset and no supervisor can "
                "act on them. Ask the mentor mother to open the application "
                "where there is network coverage."
            ),
            mentor_mother=mentor_mother,
        )
    return raised


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


def evaluate_all_rules() -> dict:
    """
    Run every enabled rule.

    One rule that fails must not stop the others. See the module docstring.
    """
    results: dict[str, int | str] = {}
    for rule_row in AlertRule.objects.filter(is_enabled=True):
        function = _REGISTRY.get(rule_row.alert_type)
        if function is None:
            results[rule_row.alert_type] = "no implementation registered"
            logger.warning("Rule %s has no implementation.", rule_row.alert_type)
            continue
        try:
            with transaction.atomic():
                results[rule_row.alert_type] = function(rule_row)
        except Exception as exc:  # noqa: BLE001
            results[rule_row.alert_type] = f"failed: {type(exc).__name__}"
            logger.exception("Rule %s failed.", rule_row.alert_type)
    return results


def escalate_overdue() -> dict:
    """
    Escalate any alert that has passed its acknowledgement deadline.

    Escalation moves one tier up the access matrix: from a facility supervisor
    to an LGA coordinator, and from there to a state programme manager. An alert
    can escalate twice. After that it stays at the top tier and keeps appearing
    on the state dashboard, because there is nobody further to send it to and it
    must not disappear.
    """
    from apps.accounts.models import Role, User

    now = timezone.now()
    overdue = Alert.objects.filter(
        status__in=[Alert.Status.OPEN, Alert.Status.ESCALATED],
        acknowledge_by__lt=now,
        escalation_level__lt=2,
    ).select_related("facility__lga__state", "rule")

    escalated = 0
    for alert in overdue:
        level = alert.escalation_level + 1
        if level == 1:
            recipient = User.objects.filter(
                role=Role.LGA_COORDINATOR, lga_id=alert.facility.lga_id, is_active=True
            ).first()
        else:
            recipient = User.objects.filter(
                role=Role.STATE_MANAGER,
                state_id=alert.facility.lga.state_id,
                is_active=True,
            ).first()

        if recipient is None:
            logger.warning(
                "Alert %s is overdue but no tier %s recipient exists for %s.",
                alert.pk,
                level,
                alert.facility.code,
            )
            continue

        with transaction.atomic():
            AlertEscalation.objects.create(
                alert=alert,
                level=level,
                escalated_to=recipient,
                reason=(
                    f"Not acknowledged by {alert.acknowledge_by:%d %b %H:%M}."
                ),
            )
            alert.escalation_level = level
            alert.status = Alert.Status.ESCALATED
            alert.escalated_at = now
            alert.assigned_user = recipient
            alert.save(
                update_fields=[
                    "escalation_level",
                    "status",
                    "escalated_at",
                    "assigned_user",
                    "updated_at",
                ]
            )
        escalated += 1

    return {"escalated": escalated}


def dispatch_appointment_reminders() -> dict:
    """
    Send the daily appointment reminders.

    A reminder goes to the mentor mother, and to the client only where the
    client agreed to SMS contact. Consent to join the programme is not consent
    to receive a message.
    """
    from apps.messaging.services import queue_templated_message
    from apps.eid.models import EidAppointment

    horizon = timezone.localdate() + timedelta(days=3)
    appointments = EidAppointment.objects.filter(
        status=EidAppointment.Status.SCHEDULED,
        due_date__lte=horizon,
        due_date__gte=timezone.localdate(),
        reminder_sent_at__isnull=True,
    ).select_related("infant__facility", "infant__mother__mentor_mother")

    sent = 0
    for appointment in appointments:
        infant = appointment.infant
        mentor_mother = infant.mother.mentor_mother
        if mentor_mother is None:
            continue

        queue_templated_message(
            template_key="eid_reminder",
            recipient_kind="MENTOR_MOTHER",
            msisdn=mentor_mother.phone_number,
            recipient_reference=mentor_mother.staff_code,
            facility=infant.facility,
            arguments={
                "baby_code": infant.baby_code,
                "due_date_words": f"{appointment.due_date:%d %B}",
            },
        )
        appointment.reminder_sent_at = timezone.now()
        appointment.reminder_count += 1
        appointment.save(update_fields=["reminder_sent_at", "reminder_count"])
        sent += 1

    return {"reminders_queued": sent}


#: Default rule configuration, loaded by the seed command. Thresholds are the
#: starting values agreed at design. A programme manager may change any of them
#: in the administration interface, and every change is audited.
DEFAULT_RULES = [
    {
        "alert_type": AlertType.POSITIVE_RESULT_UNACKNOWLEDGED,
        "severity": Severity.CRITICAL,
        "threshold_value": 2,
        "threshold_unit": "HOURS",
        "notify_facility_supervisor": True,
        "notify_lga_coordinator": True,
        "send_sms": True,
        "sms_template_key": "urgent_follow_up",
        "escalate_after_hours": 4,
    },
    {
        "alert_type": AlertType.ART_LINKAGE_OVERDUE,
        "severity": Severity.CRITICAL,
        "threshold_value": 7,
        "threshold_unit": "DAYS",
        "notify_mentor_mother": True,
        "notify_facility_supervisor": True,
        "send_sms": True,
        "sms_template_key": "linkage_overdue",
        "escalate_after_hours": 24,
    },
    {
        "alert_type": AlertType.EID_APPOINTMENT_DUE,
        "severity": Severity.MEDIUM,
        "threshold_value": 3,
        "threshold_unit": "DAYS",
        "notify_mentor_mother": True,
        "send_sms": True,
        "sms_template_key": "eid_reminder",
    },
    {
        "alert_type": AlertType.EID_APPOINTMENT_MISSED,
        "severity": Severity.HIGH,
        "threshold_value": 3,
        "threshold_unit": "DAYS",
        "notify_mentor_mother": True,
        "notify_facility_supervisor": True,
        "send_sms": True,
        "sms_template_key": "appointment_missed",
        "escalate_after_hours": 48,
    },
    {
        "alert_type": AlertType.SAMPLE_RESULT_OVERDUE,
        "severity": Severity.HIGH,
        "threshold_value": 14,
        "threshold_unit": "DAYS",
        "notify_facility_supervisor": True,
        "send_sms": False,
    },
    {
        "alert_type": AlertType.VISIT_FLAGGED_FOR_REVIEW,
        "severity": Severity.LOW,
        "threshold_value": 1,
        "threshold_unit": "COUNT",
        "notify_facility_supervisor": True,
        "send_sms": False,
    },
    {
        "alert_type": AlertType.SYNC_BACKLOG,
        "severity": Severity.MEDIUM,
        "threshold_value": settings.PROGRAMME["SYNC_BACKLOG_ALARM_HOURS"],
        "threshold_unit": "HOURS",
        "notify_mentor_mother": True,
        "notify_facility_supervisor": True,
        "send_sms": True,
        "sms_template_key": "sync_reminder",
    },
]
