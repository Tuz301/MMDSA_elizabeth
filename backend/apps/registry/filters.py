"""Query filters for the registry endpoints. Codes, never names: the name
columns are encrypted and cannot be searched, which is by design."""

from __future__ import annotations

import django_filters

from .models import Client, Infant, MentorMother


class MentorMotherFilter(django_filters.FilterSet):
    class Meta:
        model = MentorMother
        fields = {
            "facility": ["exact"],
            "status": ["exact"],
            "has_smartphone": ["exact"],
            "staff_code": ["exact"],
        }


class ClientFilter(django_filters.FilterSet):
    class Meta:
        model = Client
        fields = {
            "facility": ["exact"],
            "mentor_mother": ["exact"],
            "status": ["exact"],
            "pregnancy_stage": ["exact"],
            "client_code": ["exact"],
            "expected_delivery_date": ["gte", "lte"],
        }


class InfantFilter(django_filters.FilterSet):
    awaiting_art_linkage = django_filters.BooleanFilter(
        method="filter_awaiting_art_linkage",
        help_text="True narrows to infants who tested positive and are not on treatment.",
    )

    class Meta:
        model = Infant
        fields = {
            "facility": ["exact"],
            "mother": ["exact"],
            "outcome": ["exact"],
            "sex": ["exact"],
            "baby_code": ["exact"],
            "date_of_birth": ["gte", "lte"],
        }

    def filter_awaiting_art_linkage(self, queryset, name, value):
        if value is True:
            return queryset.filter(outcome=Infant.Outcome.HIV_POSITIVE_NOT_LINKED)
        if value is False:
            return queryset.exclude(outcome=Infant.Outcome.HIV_POSITIVE_NOT_LINKED)
        return queryset
