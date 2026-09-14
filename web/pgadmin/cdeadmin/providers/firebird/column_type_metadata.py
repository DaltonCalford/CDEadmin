##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Lossless native type facts for editor prefill, without precision guesses."""

import re


def _number(field, key, default=None):
    value = field.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not re.fullmatch(r'-?\d+', str(value)):
        raise ValueError('Invalid native numeric metadata')
    return int(value)


def type_editor_values(field):
    """Empty means explicit user choice is needed, never an INTEGER fallback.

    This describes a scalar base type/domain. Array dimensions, collation and
    nullability are separate facts, not inferred from a storage byte length.
    Legacy fixed-point storage without declared precision is not guessed.
    """
    domain = field.get('domain')
    if isinstance(domain, str) and domain and not domain.startswith('RDB$'):
        return {'data_type': 'DOMAIN', 'domain': domain}
    try:
        kind = _number(field, 'field_type')
        subtype = _number(field, 'field_sub_type', 0)
        scale = _number(field, 'field_scale', 0)
        values = {}
        if kind in {7, 8, 16, 26}:
            if subtype == 0 and scale == 0:
                values['data_type'] = {
                    7: 'SMALLINT', 8: 'INTEGER', 16: 'BIGINT', 26: 'INT128',
                }[kind]
            elif subtype in {1, 2}:
                precision = _number(field, 'field_precision')
                if precision is None or not 1 <= precision <= 38 or \
                        not 0 <= -scale <= precision:
                    return {}
                values.update(data_type='NUMERIC' if subtype == 1 else
                              'DECIMAL', precision=precision, scale=-scale)
            else:
                return {}
        elif kind in {14, 37}:
            length = _number(field, 'character_length')
            if (length is None or not 1 <= length <= 32767 or
                    subtype not in {0, 1}):
                return {}
            values.update(data_type={
                (14, 0): 'CHAR', (37, 0): 'VARCHAR',
                (14, 1): 'BINARY', (37, 1): 'VARBINARY',
            }[(kind, subtype)], length=length)
        elif kind == 261:
            if not -32768 <= subtype <= 32767:
                return {}
            values.update(data_type='BLOB', blob_subtype=subtype)
            segment = _number(field, 'segment_length')
            if segment is not None:
                if not 0 <= segment <= 65535:
                    return {}
                values['segment_size'] = segment
        elif kind in {24, 25}:
            values.update(data_type='DECFLOAT',
                          precision=16 if kind == 24 else 34)
        elif kind in {13, 28, 29, 35}:
            values.update(data_type='TIME' if kind in {13, 28} else
                          'TIMESTAMP', time_zone='WITH TIME ZONE' if
                          kind in {28, 29} else 'WITHOUT TIME ZONE')
        elif kind == 27 and scale != 0:
            return {}
        else:
            name = {10: 'FLOAT', 12: 'DATE', 23: 'BOOLEAN',
                    27: 'DOUBLE PRECISION'}.get(kind)
            if name is None:
                return {}
            values['data_type'] = name
        if (kind in {14, 37} and subtype == 0) or \
                (kind == 261 and subtype == 1):
            charset = field.get('character_set')
            if isinstance(charset, str) and charset:
                values['character_set'] = charset
        return values
    except ValueError:
        return {}
