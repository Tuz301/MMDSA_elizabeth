"""SMS dispatch and delivery reconciliation."""

from celery import shared_task


@shared_task(
    name="apps.messaging.tasks.send_message",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_message(self, message_id: str) -> dict:
    from .gateway import dispatch

    return dispatch(message_id, task=self)


@shared_task(name="apps.messaging.tasks.reconcile_delivery_reports")
def reconcile_delivery_reports() -> dict:
    from .gateway import reconcile

    return reconcile()
