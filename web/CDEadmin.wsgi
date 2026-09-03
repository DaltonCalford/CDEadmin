##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Derived from pgAdmin 4. Copyright (C) 2013 - 2026,
# The pgAdmin Development Team. PostgreSQL Licence.
#
##########################################################################

"""Canonical WSGI entry point for CDEadmin."""

import builtins
import os
import sys


if sys.version_info < (3, 9):
    raise Exception('CDEadmin must be run under Python 3.9 or later.')

root = os.path.dirname(os.path.realpath(__file__))
if sys.path[0] != root:
    sys.path.insert(0, root)

builtins.SERVER_MODE = True

import config  # noqa: E402


if not os.path.exists(os.path.dirname(config.SQLITE_PATH)):
    raise Exception(
        'Required CDEadmin configuration directory is not present. '
        'Run setup.py first.'
    )

# The inherited module name is retained solely as a compatibility boundary.
from pgAdmin4 import app as application  # noqa: E402
