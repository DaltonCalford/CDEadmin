##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Bounded native status codes without SQL or message arguments."""

import re


def execution_identity(error):
    """Admit bounded numeric codes and five-character ASCII SQLSTATE only."""
    result = []
    for attribute in ('errno', 'sqlstate'):
        try:
            value = getattr(error, attribute, None)
        except Exception:
            continue
        if attribute == 'errno':
            valid = type(value) is int and -2147483648 <= value <= 2147483647
        else:
            valid = (type(value) is str and
                     re.fullmatch(r'[0-9A-Z]{5}', value) is not None)
        if valid:
            result.append(f'{attribute}={value}')
    return result


def status_codes(error):
    try:
        values = getattr(error, 'gds_codes', ())
    except Exception:
        return ()
    if not isinstance(values, (tuple, list)) or len(values) > 32:
        return ()
    if any(type(value) is not int or not 0 < value <= 2147483647
           for value in values):
        return ()
    return tuple(values)
