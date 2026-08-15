"""
Programme metrics published to CloudWatch.

The observability stack alarms on these. They are named there and here, so keep
the two lists together when either changes.

A metric never carries a patient identifier as a dimension. The only dimensions
are the environment and, where useful, the facility code.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

logger = logging.getLogger(__name__)

NAMESPACE = "MMDSA/Programme"

METRIC_NAMES = [
    "AlertsRaised",
    "AlertsAcknowledged",
    "AlertsEscalated",
    "AlertsPastSla",
    "SmsDispatched",
    "SmsDelivered",
    "SmsDeliveryFailed",
    "SmsReplyParsed",
    "VisitLocationVerifiedPercent",
    "SyncRecordsPending",
    "SyncRecordsOlderThan72h",
    "EidResultsUnacknowledged48h",
    "EidResultToActionHours",
]


def put_metrics(datapoints: Iterable[dict]) -> None:
    """
    Publish datapoints. Each entry is {"name", "value", optional "unit",
    optional "facility_code"}.

    A failure to publish is logged and swallowed. Losing a metric is an
    observability problem. Raising here would turn it into an outage, and the
    caller is usually a scheduled job that has already done useful work.
    """
    try:
        import boto3

        client = boto3.client("cloudwatch")
        environment = os.environ.get("MMDSA_ENV", "dev")

        payload = []
        for point in datapoints:
            dimensions = [{"Name": "Environment", "Value": environment}]
            if point.get("facility_code"):
                dimensions.append(
                    {"Name": "Facility", "Value": point["facility_code"]}
                )
            payload.append(
                {
                    "MetricName": point["name"],
                    "Value": float(point["value"]),
                    "Unit": point.get("unit", "None"),
                    "Dimensions": dimensions,
                }
            )

        for batch_start in range(0, len(payload), 20):
            client.put_metric_data(
                Namespace=NAMESPACE, MetricData=payload[batch_start : batch_start + 20]
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not publish metrics: %s", exc)
