import pytest

from src.excel_templates import AS9102_FORM3, DEFAULT_TEMPLATE_ID, GENERIC_FLAT, get_template, list_templates
from src.exceptions import UnknownTemplateError


def test_default_template_id_is_as9102_form3():
    assert DEFAULT_TEMPLATE_ID == AS9102_FORM3.template_id


def test_get_template_with_none_returns_the_default():
    assert get_template(None) is AS9102_FORM3


def test_get_template_with_empty_string_returns_the_default():
    assert get_template("") is AS9102_FORM3


def test_get_template_resolves_a_known_id():
    assert get_template("generic-flat") is GENERIC_FLAT


def test_get_template_rejects_an_unknown_id():
    with pytest.raises(UnknownTemplateError, match="not-a-real-template"):
        get_template("not-a-real-template")


def test_unknown_template_error_message_lists_valid_ids():
    with pytest.raises(UnknownTemplateError) as excinfo:
        get_template("nope")
    assert "as9102-form3" in str(excinfo.value)
    assert "generic-flat" in str(excinfo.value)


def test_list_templates_is_sorted_and_covers_both_registered_templates():
    ids = [t.template_id for t in list_templates()]
    assert ids == sorted(ids)
    assert set(ids) == {"as9102-form3", "generic-flat"}
