##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Feature smoke proving the test provider is absent from the product UI."""

from regression.feature_utils.base_feature_test import BaseFeatureTest


class CDEadminProductionExclusionFeatureTest(BaseFeatureTest):
    """Keep the non-operational fixture outside production presentation."""

    scenarios = [(
        'CDEadmin production fixture exclusion',
        {},
    )]

    def runTest(self):
        self.assertNotIn(
            'org.cdeadmin.fixture.non_operational',
            self.page.driver.page_source,
        )
