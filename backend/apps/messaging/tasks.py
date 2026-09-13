"""SMS dispatch and delivery reconciliation."""

from celery import shared_task


@shared_task(
    name="apps.messaging.tasks.send_message",
    bind=True,
    max_retries=3,
    # Exponential backoff with jitter: 60s, then ~120s, then ~240s. Hammering
    # a struggling gateway at a fixed interval makes its recovery slower, and
    # dispatch is idempotent to retry because the task re-checks the message
    # status before sending.
    retry_backoff=60,
    retry_backoff_max=600,
    retry_jitter=True,
)
def send_message(self, message_id: str) -> dict:
    from .gateway import dispatch

    return dispatch(message_id, task=self)


@shared_task(name="apps.messaging.tasks.reconcile_delivery_reports")
def reconcile_delivery_reports() -> dict:
    from .gateway import reconcile

    return reconcile()
