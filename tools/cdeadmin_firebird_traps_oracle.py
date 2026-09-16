"""Read native DECFLOAT traps without changing session/transaction state."""


NATIVE_NAMES = {
    'Division_by_zero': 'DIVISION_BY_ZERO',
    'Inexact': 'INEXACT',
    'Invalid_operation': 'INVALID_OPERATION',
    'Overflow': 'OVERFLOW',
    'Underflow': 'UNDERFLOW',
}
DEFAULT_TRAPS = ('DIVISION_BY_ZERO', 'INVALID_OPERATION', 'OVERFLOW')
# Fixed expressions in the attachment gate. Overflow/underflow conversion
# also signals inexact; DecFloat.cpp dec2fb gives inexact higher precedence.
PROBE_SIGNALS = {
    'DIVISION_BY_ZERO': ('DIVISION_BY_ZERO',),
    'INEXACT': ('INEXACT',),
    'INVALID_OPERATION': ('INVALID_OPERATION',),
    'OVERFLOW': ('INEXACT', 'OVERFLOW'),
    'UNDERFLOW': ('INEXACT', 'UNDERFLOW'),
}
QUERY = ("SELECT RDB$GET_CONTEXT('SYSTEM', 'DECFLOAT_TRAPS') "
         'FROM RDB$DATABASE')


def first_trapped_condition(probe, active):
    """Native error precedence for the five fixed qualification expressions."""
    if probe not in PROBE_SIGNALS or any(
            name not in NATIVE_NAMES.values() for name in active):
        raise ValueError('Unknown trap qualification condition')
    return next((name for name in NATIVE_NAMES.values()
                 if name in active and name in PROBE_SIGNALS[probe]), None)


def observe_traps(connection):
    """Decode Firebird 5's exact context names; never guess missing state."""
    with connection.cursor() as cursor:
        cursor.execute(QUERY)
        row = cursor.fetchone()
    if row is None or len(row) != 1 or not isinstance(row[0], str):
        raise RuntimeError('Native trap observation is incomplete')
    if row[0] == 'None':
        return []
    names = row[0].split(',')
    if len(set(names)) != len(names) or any(
            name not in NATIVE_NAMES for name in names):
        raise RuntimeError('Native trap observation contains unknown state')
    return sorted(NATIVE_NAMES[name] for name in names)
