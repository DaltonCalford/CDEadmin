##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Derived from pgAdmin 4. Copyright (C) 2013 - 2026,
# The pgAdmin Development Team. PostgreSQL Licence.
#
##########################################################################

"""Canonical CDEadmin application entry point.

The inherited ``pgAdmin4`` module remains the implementation and compatibility
entry point until its import graph can be migrated without breaking extensions
or deployments.
"""

from pgAdmin4 import app, main


if __name__ == '__main__':
    main()
