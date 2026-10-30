import pytest
from pydantic import BaseModel, ValidationError

from k_b.core.quoting import (
    MAX_QUOTED_VALUE_LENGTH,
    locate_first_error,
    quote_value,
    without_values,
)


def test_a_value_is_quoted_with_its_line_breaks_and_control_characters_escaped():
    assert quote_value("a\nb\x1b[31m") == "'a\\nb\\x1b[31m'"


def test_a_long_value_is_cut_to_the_fixed_length():
    quoted = quote_value("x" * 500)

    assert len(quoted) == MAX_QUOTED_VALUE_LENGTH
    assert quoted.startswith("'xxx")


def test_a_long_value_is_cut_to_the_length_given():
    assert len(quote_value("x" * 500, max_length=300)) == 300


class Summary(BaseModel):
    text: str


def test_an_error_without_values_keeps_its_field_and_type_but_not_its_value():
    with pytest.raises(ValidationError) as refusal:
        Summary.model_validate({"text": 1234567})

    described = str(without_values(refusal.value))

    assert "text" in described
    assert "string_type" in described
    assert "1234567" not in described


class Digest(BaseModel):
    summaries: list[Summary]
    days: int


def test_the_first_error_is_located_by_its_place_and_its_type_never_its_value():
    with pytest.raises(ValidationError) as refusal:
        Digest.model_validate(
            {"summaries": [{"text": "ok"}, {"text": 1234567}], "days": 3}
        )

    assert locate_first_error(refusal.value) == "summaries.1.text (string_type)"


def test_the_count_of_the_errors_is_given_when_there_are_more():
    with pytest.raises(ValidationError) as refusal:
        Digest.model_validate({"summaries": [{"text": 1234567}], "days": "three"})

    assert (
        locate_first_error(refusal.value) == "summaries.0.text (string_type), 2 errors"
    )


def test_an_error_on_the_whole_input_is_located_as_the_input():
    with pytest.raises(ValidationError) as refusal:
        Summary.model_validate_json(b"not json 1234567")

    assert locate_first_error(refusal.value) == "input (json_invalid)"


class Counts(BaseModel):
    by_name: dict[str, int]


def test_a_place_that_comes_from_the_input_is_quoted_cut_and_escaped():
    """A key of a dict comes from the input: never written as it is."""
    with pytest.raises(ValidationError) as refusal:
        Counts.model_validate({"by_name": {"a\nERROR forged": "x"}})

    assert (
        locate_first_error(refusal.value) == "by_name.'a\\nERROR forged' (int_parsing)"
    )
    assert "\n" not in locate_first_error(refusal.value)
