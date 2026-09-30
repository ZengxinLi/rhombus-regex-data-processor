import pytest

from processor.services.patterns import PatternError, PatternResolver, validate_regex


def test_local_email_request_resolves_to_safe_regex():
    resolved = PatternResolver().resolve("Find email addresses")
    assert resolved.operation == "replace"
    assert resolved.regex == r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"


def test_local_additional_transformation_is_resolved():
    resolved = PatternResolver().resolve("Normalize whitespace in this name", "auto")
    assert resolved.operation == "normalize_whitespace"
    assert resolved.regex is None


@pytest.mark.parametrize("unsafe", [r"(a+)+$", r"(.*)*", r"(\w+){2,}"])
def test_nested_quantifiers_are_rejected(unsafe):
    with pytest.raises(PatternError):
        validate_regex(unsafe)
