"""Alert engine jobs. Rule evaluation is implemented in engine.py."""

from celery import shared_task


@shared_task(name="apps.alerts.tasks.run_alert_rules")
def run_alert_rules() -> dict:
    from .engine import evaluate_all_rules

    return evaluate_all_rules()


@shared_task(name="apps.alerts.tasks.escalate_overdue_alerts")
def escalate_overdue_alerts() -> dict:
    from .engine import escalate_overdue

    return escalate_overdue()


@shared_task(name="apps.alerts.tasks.send_appointment_reminders")
def send_appointment_reminders() -> dict:
    from .engine import dispatch_appointment_reminders

    return dispatch_appointment_reminders()
