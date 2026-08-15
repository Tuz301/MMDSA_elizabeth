"""
The outbound message guard.

An SMS crosses a public mobile network and then sits in plain text on a handset
that other people in the household may use. A message that names a child and
implies an HIV exposure can lead to that family being harmed. In parts of
Nigeria the consequence of an accidental disclosure is violence, eviction or
abandonment, not embarrassment.

So the rule is absolute. A message that leaves this system identifies an infant
by the Baby Code and by nothing else. No name. No mother's name. No clinical
word. No facility address.

The rule is enforced here, in code, on every outbound message body, after the
template has been rendered. It is not enforced by asking template authors to be
careful. A template author will eventually be careless, and the guard is what
catches that.

The guard denies by default. If a check cannot run, the message does not go.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Words that reveal an HIV context. Absent from every outbound message.
FORBIDDEN_TERMS = frozenset(
    {
        "hiv", "aids", "positive", "negative", "retroviral", "antiretroviral",
        "art", "arv", "nevirapine", "viral load", "cd4", "pcr", "dna pcr",
        "seropositive", "reactive", "non-reactive", "infected", "exposure",
        "exposed", "pmtct", "test result", "result is", "diagnosis",
        "treatment", "medication", "drugs", "clinic result", "eid",
    }
)

#: Patterns that reveal a person or a place.
IDENTIFIER_PATTERNS: list[tuple[str, str]] = [
    (r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "a full date, which can identify a person"),
    (r"\b\d{11}\b", "an eleven digit number, which may be a telephone number"),
    (r"\bNIN[:\s-]*\d+", "a national identity number"),
    # One or more capitalised words may sit between the house number and the
    # thoroughfare word. "12 Ahmadu Bello Road" has two.
    (
        r"\b\d{1,4}\s+(?:[A-Z][A-Za-z'-]*\s+){1,4}"
        r"(street|road|close|avenue|lane|crescent|way|drive)\b",
        "a street address",
    ),
]

#: A Baby Code. One capital B followed by seven characters from the safe set.
BABY_CODE_PATTERN = re.compile(r"\bB[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{7}\b")

#: A staff or client code. Safe to send.
PROGRAMME_CODE_PATTERN = re.compile(r"\b(MM|CL)[A-Z0-9-]{4,20}\b")


class PhiGuardError(Exception):
    """Raised when a message body fails the check. The message is not sent."""


@dataclass
class GuardResult:
    passed: bool
    violations: list[str] = field(default_factory=list)

    def raise_if_failed(self, body: str) -> None:
        if not self.passed:
            raise PhiGuardError(
                "This message was blocked before it was sent. "
                + "; ".join(self.violations)
                + f" Body: {body[:60]!r}"
            )


def check_outbound_body(body: str, *, allow_names: bool = False) -> GuardResult:
    """
    Check a rendered message body.

    allow_names stays False for every message about a client or an infant. It is
    True only for a message to a health worker about that worker's own account,
    such as a one time passcode, where the worker's own name carries no
    disclosure risk.
    """
    violations: list[str] = []
    lowered = body.casefold()

    for term in FORBIDDEN_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            violations.append(
                f"The body contains the word {term!r}, which reveals a clinical "
                f"context to anyone who reads the handset."
            )

    if not allow_names:
        for pattern, description in IDENTIFIER_PATTERNS:
            if re.search(pattern, body, flags=re.IGNORECASE):
                violations.append(f"The body appears to contain {description}.")

    if len(body) > 320:
        violations.append(
            f"The body is {len(body)} characters. The limit is 320, which is "
            f"two message segments."
        )

    return GuardResult(passed=not violations, violations=violations)


def check_template_placeholders(template_body: str) -> GuardResult:
    """
    Check a template before it is saved.

    Catching a bad placeholder at template save time is better than catching a
    bad body at send time, because the template failure is visible to the person
    who can fix it.
    """
    violations: list[str] = []
    placeholders = set(re.findall(r"\{(\w+)\}", template_body))

    forbidden_placeholders = {
        "full_name", "given_name", "mother_name", "client_name",
        "infant_name", "phone_number", "household_address", "date_of_birth",
        "result", "hiv_status", "hospital_number",
    }
    for name in sorted(placeholders & forbidden_placeholders):
        violations.append(
            f"The placeholder {{{name}}} would insert a direct identifier or a "
            f"clinical value. Use {{baby_code}} or {{client_code}} instead."
        )

    static_text = re.sub(r"\{\w+\}", " ", template_body)
    static_result = check_outbound_body(static_text)
    violations.extend(static_result.violations)

    return GuardResult(passed=not violations, violations=violations)


def redact_for_log(body: str) -> str:
    """
    Return a body safe to write into an application log.

    Codes are kept because an operator needs them to trace a message. Everything
    else is removed, so that a log aggregator never becomes a second copy of the
    patient register.
    """
    kept = BABY_CODE_PATTERN.findall(body) + PROGRAMME_CODE_PATTERN.findall(body)
    return f"[redacted body, {len(body)} chars] codes={sorted(set(kept))}"
