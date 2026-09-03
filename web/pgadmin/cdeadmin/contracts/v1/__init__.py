##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""CDEadmin provider contract version 1."""

from .runtime import (
    ContractValidationError,
    admit_capability,
    load_contract_schema,
    validate_contract,
)

__all__ = [
    'ContractValidationError',
    'admit_capability',
    'load_contract_schema',
    'validate_contract',
]
