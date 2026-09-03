##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""CDEadmin product identity and coexistence primitives."""

from .coexistence import isolated_profile  # noqa: F401
from .identity import (  # noqa: F401
    DEFAULT_IDENTITY_PATH,
    ProductIdentityError,
    load_identity,
    namespace_collisions,
    namespace_values,
    validate_identity,
)
