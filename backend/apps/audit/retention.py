"""
Audit archival and the retention policy.

The compliance annex states retention periods. This module is what makes those
statements true. A written policy that no job enforces is a commitment the
programme is not keeping, and an auditor will find that faster than anything
else in the document.

apply_policy defaults to a dry run. A job that deletes patient records must be
inspected before it is trusted, and the default must never be the destructive
one.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import AuditLog, RetentionRun

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000


def archive_unarchived() -> dict:
    """
    Copy audit records to the S3 archive.

    The bucket has Object Lock in compliance mode, and the instance role holds
    PutObject and nothing else. Once a record is written, neither the
    application nor an attacker who takes the application can alter it.
    """
    pending = AuditLog.objects.filter(archived_to_s3=False).order_by("occurred_at")[
        :BATCH_SIZE
    ]
    rows = list(pending)
    if not rows:
        return {"archived": 0}

    payload = [
        {
            "id": str(row.id),
            "occurred_at": row.occurred_at.isoformat(),
            "actor": row.actor_username,
            "role": row.actor_role,
            "action": row.action,
            "classification": row.classification,
            "object_type": row.object_type,
            "object_id": row.object_id,
            "object_reference": row.object_reference,
            "fields": row.fields_touched,
            "ip": row.ip_address,
            "path": row.request_path,
            "method": row.request_method,
            "status": row.response_status,
        }
        for row in rows
    ]

    bucket = os.environ.get("AUDIT_BUCKET", "")
    if not bucket:
        logger.warning("AUDIT_BUCKET is not set. Archival was skipped.")
        return {"archived": 0, "reason": "no bucket configured"}

    try:
        import boto3

        key = f"audit/{timezone.now():%Y/%m/%d}/{rows[0].id}.jsonl"
        body = "\n".join(json.dumps(entry) for entry in payload).encode()
        boto3.client("s3").put_object(
            Bucket=bucket, Key=key, Body=body, ContentType="application/x-ndjson"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not archive the audit batch.")
        return {"archived": 0, "error": type(exc).__name__}

    now = timezone.now()
    AuditLog.objects.filter(id__in=[row.id for row in rows]).update(
        archived_to_s3=True, archived_at=now
    )
    return {"archived": len(rows), "key": key}


def apply_policy(dry_run: bool = True) -> dict:
    """
    Apply the retention policy.

    Clinical records past the retention period are anonymised, not deleted. The
    programme still needs the counts for its cascade reporting, and an
    anonymised row supports that while holding nothing that identifies anyone.
    """
    from apps.registry.models import Client

    run = RetentionRun.objects.create(
        policy_name="clinical_records",
        outcome=RetentionRun.Outcome.DRY_RUN if dry_run else RetentionRun.Outcome.COMPLETED,
    )

    years = settings.DATA_PROTECTION["CLINICAL_RETENTION_YEARS"]
    cutoff = timezone.localdate() - timedelta(days=365 * years)

    candidates = Client.all_objects.filter(
        date_exited__isnull=False, date_exited__lt=cutoff
    ).exclude(full_name="")

    run.records_examined = candidates.count()

    if not dry_run:
        anonymised = 0
        for client in candidates.iterator(chunk_size=500):
            client.full_name = ""
            client.phone_number = ""
            client.alternate_phone_number = ""
            client.household_address = ""
            client.hospital_number = ""
            client.household_latitude = None
            client.household_longitude = None
            client.save(
                update_fields=[
                    "full_name", "phone_number", "alternate_phone_number",
                    "household_address", "hospital_number",
                    "household_latitude", "household_longitude", "updated_at",
                ]
            )
            anonymised += 1
        run.records_anonymised = anonymised

    run.finished_at = timezone.now()
    run.save()
    return {
        "run": str(run.id),
        "examined": run.records_examined,
        "anonymised": run.records_anonymised,
        "dry_run": dry_run,
    }
