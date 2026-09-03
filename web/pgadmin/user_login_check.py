##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Implement CDEadmin user validity in the legacy package namespace."""

from functools import wraps
from flask_security import login_required


def pga_login_required(func):
    import pgadmin.authenticate.mfa.utils as mfa_utils

    @wraps(func)
    @mfa_utils.mfa_required
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper
