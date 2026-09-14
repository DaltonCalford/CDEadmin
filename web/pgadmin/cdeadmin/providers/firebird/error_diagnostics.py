##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Bounded native status codes without SQL or message arguments."""


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
