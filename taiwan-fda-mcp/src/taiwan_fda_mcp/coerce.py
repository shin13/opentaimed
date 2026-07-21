# path: src/taiwan_fda_mcp/coerce.py
# brief: Coerce JSON-array-string args back to list (some MCP clients stringify list params).

import json
from typing import Any


def coerce_json_array(value: Any) -> Any:
    """Parse a JSON-encoded array string back into a list.

    Some MCP clients (observed with Claude Desktop) serialise a list-typed
    tool argument as a JSON string, e.g. ``'["a", "b"]'`` instead of the array
    ``["a", "b"]``. This runs as a Pydantic ``BeforeValidator`` so such input is
    parsed back into a list before normal union validation.

    Only strings that look like a JSON array (``[`` ... ``]``) are touched.
    Anything else — real lists, bare enum strings like ``"all"``, or malformed
    input — is returned unchanged so the normal validation path still applies
    (and still rejects genuinely invalid input rather than silently dropping it).

    Args:
        value: The raw parameter value before validation.

    Returns:
        The parsed list when ``value`` is a JSON-array string; otherwise ``value``
        unchanged.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return value
            if isinstance(parsed, list):
                return parsed
    return value
