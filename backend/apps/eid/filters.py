"""Query filters for the EID worklists."""

from __future__ import annotations

import django_filters
from django.utils import timezone

from .models import ArtLinkage, EidAppointment, EidSample


class EidAppointmentFilter(django_filters.FilterSet):
    overdue = django_filters.BooleanFilter(
        method="filter_overdue",
        help_text="True narrows to scheduled appointments already past their date.",
    )

    class Meta:
        model = EidAppointment
        fields = {
            "status": ["exact"],
            "milestone": ["exact"],
            "infant": ["exact"],
            "infant__facility": ["exact"],
            "due_date": ["gte", "lte"],
        }

    def filter_overdue(self, queryset, name, value):
        if value is True:
            return queryset.filter(
                status=EidAppointment.Status.SCHEDULED,
                due_date__lt=timezone.localdate(),
            )
        return queryset


class EidSampleFilter(django_filters.FilterSet):
    unacknowledged_positive = django_filters.BooleanFilter(
        method="filter_unacknowledged_positive",
        help_text="True narrows to positive results nobody has acknowledged.",
    )

    class Meta:
        model = EidSample
        fields = {
            "result": ["exact"],
            "appointment__infant": ["exact"],
            "appointment__infant__facility": ["exact"],
            "collected_on": ["gte", "lte"],
        }

    def filter_unacknowledged_positive(self, queryset, name, value):
        if value is True:
            return queryset.filter(
                result=EidSample.Result.POSITIVE,
                result_acknowledged_at__isnull=True,
            )
        return queryset


class ArtLinkageFilter(django_filters.FilterSet):
    class Meta:
        model = ArtLinkage
        fields = {
            "status": ["exact"],
            "infant__facility": ["exact"],
            "treating_facility": ["exact"],
        }
