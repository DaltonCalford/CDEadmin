#######################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

from .registry import TestModuleBase


# This class will be registered with TestModuleRegistry (registry)
class TestModule1(TestModuleBase):
    pass
