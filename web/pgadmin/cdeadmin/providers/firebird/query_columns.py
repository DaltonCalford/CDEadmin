"""Firebird-native result columns, including modern types and duplicate labels.

The pinned firebird-driver exposes its decoded IMessageMetadata through the
statement's internal output descriptor. Retain its provenance and fall back to
the public DB-API description when that internal descriptor is unavailable.
Never infer a native SQL type from a sampled value.
"""

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


_SQL_NAMES = {
    'TEXT': 'CHAR', 'VARYING': 'VARCHAR', 'SHORT': 'SMALLINT',
    'LONG': 'INTEGER', 'INT64': 'BIGINT', 'INT128': 'INT128',
    'FLOAT': 'FLOAT', 'DOUBLE': 'DOUBLE PRECISION',
    'TIMESTAMP': 'TIMESTAMP', 'TIME': 'TIME', 'DATE': 'DATE',
    'TIMESTAMP_TZ': 'TIMESTAMP WITH TIME ZONE',
    'TIMESTAMP_TZ_EX': 'TIMESTAMP WITH TIME ZONE',
    'TIME_TZ': 'TIME WITH TIME ZONE', 'TIME_TZ_EX': 'TIME WITH TIME ZONE',
    'DEC16': 'DECFLOAT(16)', 'DEC34': 'DECFLOAT(34)',
    'BOOLEAN': 'BOOLEAN', 'BLOB': 'BLOB', 'ARRAY': 'ARRAY',
}


def describe_columns(cursor):
    description = cursor.description or ()
    native = getattr(getattr(cursor, 'statement', None), '_out_desc', None)
    available = isinstance(native, (list, tuple))
    if available and len(native) != len(description):
        raise RelationalClientError(
            'Firebird native result metadata does not match its column count')
    labels = [str(item[0]) for item in description]
    bases = [label.strip() or f'column_{index + 1}'
             for index, label in enumerate(labels)]
    reserved = set(bases)
    used = set()
    columns = []
    for ordinal, item in enumerate(description):
        label = labels[ordinal]
        base = bases[ordinal]
        name = base
        suffix = 2
        while name in used:
            name = base + '#' + str(suffix)
            suffix += 1
            if name in reserved:
                name = base
        used.add(name)
        column = {
            'name': name, 'native_name': label, 'ordinal': ordinal,
            'native_type': str(item[1]) if len(item) > 1 else None,
            'metadata_source': 'firebird-driver.DB-API-description',
        }
        if len(item) >= 7:
            column.update(display_size=item[2], internal_size=item[3],
                          precision=item[4] or None, scale=item[5],
                          nullable=item[6])
        if available:
            meta = native[ordinal]
            code = meta.datatype.name
            sql_type = _SQL_NAMES.get(code)
            binary_text = code in {'TEXT', 'VARYING'} and meta.charset == 1
            if binary_text:
                sql_type += ' CHARACTER SET OCTETS'
            if code in {'SHORT', 'LONG', 'INT64', 'INT128'}:
                if meta.subtype in (1, 2):
                    sql_type = 'NUMERIC' if meta.subtype == 1 else 'DECIMAL'
            column.update({
                'native_type': sql_type,
                'native_type_code': int(meta.datatype),
                'native_type_name': code,
                'native_subtype': meta.subtype,
                'native_scale': meta.scale,
                'internal_size': meta.length,
                'charset_id': meta.charset,
                'nullable': meta.nullable,
                'source_relation': meta.relation or None,
                'source_field': meta.field or None,
                'source_owner': meta.owner or None,
                'metadata_source': 'firebird-driver.IMessageMetadata',
            })
            # Do not round exact numerics or strip time-zone/microsecond data
            # through generic JavaScript Number/Date cell formatting.
            column['cell_type'] = (
                'boolean' if code == 'BOOLEAN' else
                'json' if code == 'ARRAY' or binary_text or (
                    code == 'BLOB' and meta.subtype != 1) else 'text')
        columns.append(column)
    return columns
