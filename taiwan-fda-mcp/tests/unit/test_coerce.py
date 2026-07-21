# path: tests/unit/test_coerce.py
# brief: Unit tests for JSON-array-string coercion (MCP client stringify quirk).

from taiwan_fda_mcp.coerce import coerce_json_array


def test_json_array_string_is_parsed_to_list():
    """A JSON-encoded array string (some MCP clients stringify list args) → list."""
    assert coerce_json_array('["contraindications", "warnings"]') == [
        "contraindications",
        "warnings",
    ]


def test_real_list_passes_through_unchanged():
    """A well-behaved client's actual list is returned untouched."""
    assert coerce_json_array(["indication"]) == ["indication"]


def test_enum_string_is_not_coerced():
    """Bare enum values ("all"/"key_fields") don't start with '[' → left for union."""
    assert coerce_json_array("all") == "all"
    assert coerce_json_array("key_fields") == "key_fields"


def test_bare_field_name_string_passes_through():
    """A non-bracketed string is left unchanged so union validation still rejects it."""
    assert coerce_json_array("contraindications") == "contraindications"


def test_malformed_json_array_passes_through():
    """Bracketed-but-invalid JSON is returned unchanged → standard ValidationError, not swallowed."""
    assert coerce_json_array('["bad') == '["bad'


def test_none_passes_through():
    """None (the default) is untouched."""
    assert coerce_json_array(None) is None


def test_empty_json_array_string_becomes_empty_list():
    """'[]' faithfully coerces to [] — same as a real empty array would."""
    assert coerce_json_array("[]") == []


def test_json_array_of_enum_value_becomes_list():
    """'["all"]' → ["all"] (a list); "all" is then treated as a field name downstream."""
    assert coerce_json_array('["all"]') == ["all"]
