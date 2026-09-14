"""Lossless Firebird values at the JSON/browser query-result boundary.

Exact numerics outside JavaScript's safe integer range are text, not floats.
Binary values carry an explicit reversible encoding. Native BLOB streams must
be consumed and closed before the owning cursor/transaction can be released.
"""

import base64
from datetime import date, datetime, time
from decimal import Decimal
import math

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


JS_SAFE_INTEGER = (1 << 53) - 1


def normalize_value(value, blob_type=()):
    if isinstance(value, blob_type):
        reader = value
        try:
            value = reader.read()
        finally:
            reader.close()
        return normalize_value(value, blob_type)
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value if abs(value) <= JS_SAFE_INTEGER else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=' ')
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {'encoding': 'base64',
                'data': base64.b64encode(raw).decode('ascii'),
                'byte_length': len(raw)}
    if isinstance(value, (list, tuple)):
        return [normalize_value(item, blob_type) for item in value]
    raise RelationalClientError(
        'Firebird result value type is not supported (' +
        type(value).__name__ + ')')
