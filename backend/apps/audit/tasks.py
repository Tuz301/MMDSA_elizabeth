"""Audit archival and retention jobs."""

from celery import shared_task


@shared_task(name="apps.audit.tasks.archive_to_s3")
def archive_to_s3() -> dict:
    from .retention import archive_unarchived

    return archive_unarchived()


@shared_task(name="apps.audit.tasks.apply_retention_policy")
def apply_retention_policy(dry_run: bool = True) -> dict:
    from .retention import apply_policy

    return apply_policy(dry_run=dry_run)
