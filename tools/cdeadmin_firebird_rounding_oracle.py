"""Distinguishing DECFLOAT oracle for isolated Firebird qualification sessions.

These SQL observations belong only in explicitly owned test transactions;
they are not a production transaction-state inspection implementation.
"""

import decimal


INPUTS = ('1.01', '-1.01', '1.15', '-1.15',
          '1.19', '-1.19', '1.25', '-1.25')
MODES = ('CEILING', 'UP', 'HALF_UP', 'HALF_EVEN',
         'HALF_DOWN', 'DOWN', 'FLOOR', 'REROUND')
QUERY = 'SELECT ' + ', '.join([
    "RDB$GET_CONTEXT('SYSTEM', 'DECFLOAT_ROUND')",
    *(f"QUANTIZE(CAST('{value}' AS DECFLOAT(16)), "
      "CAST('0.1' AS DECFLOAT(16)))" for value in INPUTS),
]) + ' FROM RDB$DATABASE'


def expected_rounding(mode):
    mode = 'HALF_UP' if mode == 'NATIVE_DEFAULT' else mode
    if mode not in MODES:
        raise ValueError('Unknown qualification rounding mode')
    rounding = (decimal.ROUND_05UP if mode == 'REROUND' else
                getattr(decimal, 'ROUND_' + mode))
    with decimal.localcontext(decimal.Context(prec=16, traps=[])):
        values = [str(decimal.Decimal(value).quantize(
            decimal.Decimal('0.1'), rounding=rounding)) for value in INPUTS]
    return {'mode': mode, 'values': values}


def observe_rounding(connection):
    """Read mode and eight samples without committing or rolling back."""
    with connection.cursor() as cursor:
        cursor.execute(QUERY)
        row = cursor.fetchone()
    if row is None or len(row) != len(INPUTS) + 1:
        raise RuntimeError('Native rounding observation is incomplete')
    return {'mode': str(row[0]), 'values': [str(value) for value in row[1:]]}
