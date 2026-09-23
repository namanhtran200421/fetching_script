"""Shared JSON serialization rules for the stored data layers."""

import json


# json.dumps with ensure_ascii=False emits these Unicode line breaks
# literally. They are valid inside a JSON string, but any reader that splits
# a JSONL file on Unicode line boundaries -- Python's str.splitlines() among
# them -- would then read one document as two broken records. Escaping them
# keeps the parsed value identical while guaranteeing one record per line.
_LINE_BREAK_ESCAPES = {
    " ": "\\u2028",
    " ": "\\u2029",
    "\u0085": "\\u0085",
}


def json_line(row):
    """Serialize one JSONL record that is guaranteed to occupy one line."""
    serialized = json.dumps(row, ensure_ascii=False)
    for character, escape in _LINE_BREAK_ESCAPES.items():
        if character in serialized:
            serialized = serialized.replace(character, escape)
    return serialized
