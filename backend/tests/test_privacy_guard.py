"""
Tests for the outbound privacy guard.

These tests exist because the guard is the only thing standing between a
template author's mistake and an accidental HIV disclosure on a family's
handset. Every test below describes a real way that disclosure could happen.
"""

import pytest

from apps.messaging.guards import (
    check_outbound_body,
    check_template_placeholders,
    redact_for_log,
)


class TestClinicalWordsAreBlocked:
    @pytest.mark.parametrize(
        "body",
        [
            "IHVN: Baby BX7K2MQ4 tested positive. Come to the clinic.",
            "Your HIV result is ready at the clinic.",
            "Bring baby BX7K2MQ4 for antiretroviral treatment today.",
            "PMTCT follow up needed for BX7K2MQ4.",
            "The test result is available for collection.",
        ],
    )
    def test_a_clinical_word_blocks_the_message(self, body):
        result = check_outbound_body(body)
        assert not result.passed
        assert result.violations


class TestSafeMessagesPass:
    @pytest.mark.parametrize(
        "body",
        [
            "IHVN: Baby BX7K2MQ4 has a clinic appointment on 14 September. "
            "Please support the family to attend. Reply SEEN to confirm.",
            "IHVN URGENT: Baby BX7K2MQ4 needs a same-day clinic visit. "
            "Please contact the family now and reply DONE when you have.",
            "IHVN: Your records have not reached the office. Please open the "
            "app where there is network so it can send them.",
        ],
    )
    def test_a_baby_code_message_passes(self, body):
        result = check_outbound_body(body)
        assert result.passed, result.violations


class TestIdentifiersAreBlocked:
    def test_a_numeric_date_is_blocked(self):
        # A date of birth plus a facility can identify one child.
        assert not check_outbound_body("Appointment for BX7K2MQ4 on 14/09/2026").passed

    def test_a_telephone_number_is_blocked(self):
        assert not check_outbound_body("Call the mother on 08031234567").passed

    def test_a_street_address_is_blocked(self):
        assert not check_outbound_body("Visit 12 Ahmadu Bello Road today").passed

    def test_an_overlong_body_is_blocked(self):
        assert not check_outbound_body("A" * 400).passed


class TestTemplateValidation:
    def test_a_name_placeholder_is_rejected(self):
        result = check_template_placeholders(
            "IHVN: Please visit {full_name} about baby {baby_code}."
        )
        assert not result.passed
        assert any("full_name" in violation for violation in result.violations)

    def test_a_result_placeholder_is_rejected(self):
        result = check_template_placeholders("Baby {baby_code} result: {result}")
        assert not result.passed

    def test_a_safe_template_is_accepted(self):
        result = check_template_placeholders(
            "IHVN: Baby {baby_code} has an appointment on {due_date_words}. "
            "Reply SEEN."
        )
        assert result.passed, result.violations


class TestBabyCodeFormat:
    """
    A generated Baby Code must match the pattern the guard and the reply parser
    both rely on. If the generator and the pattern ever drift apart, a reply
    stops matching an infant and the loop silently breaks.
    """

    def test_a_generated_code_matches_the_recognised_pattern(self):
        from apps.messaging.guards import BABY_CODE_PATTERN
        from apps.registry.models import generate_baby_code

        for _ in range(200):
            code = generate_baby_code()
            assert len(code) == 8
            assert BABY_CODE_PATTERN.fullmatch(code), code

    def test_a_generated_code_avoids_confusable_characters(self):
        from apps.registry.models import generate_baby_code

        # A code is read aloud over the telephone and typed into a reply. I, O,
        # 0 and 1 are excluded so that a misheard character cannot select the
        # wrong infant.
        for _ in range(200):
            assert not (set(generate_baby_code()[1:]) & set("IO01"))


class TestLogRedaction:
    def test_the_body_is_not_written_to_the_log(self):
        body = "IHVN: Baby BX7K2MQ4 has an appointment on 14 September."
        redacted = redact_for_log(body)
        assert "appointment" not in redacted
        assert "BX7K2MQ4" in redacted
