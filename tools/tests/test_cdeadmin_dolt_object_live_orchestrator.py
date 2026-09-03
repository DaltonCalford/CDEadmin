##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Dolt exact-container object qualification orchestration tests."""

import unittest

from tools.cdeadmin_dolt_object_live_orchestrator import (
    IMAGE,
    _published_port,
)


class DoltObjectLiveOrchestratorTests(unittest.TestCase):

    def test_exact_reference_image_is_pinned(self):
        self.assertEqual('dolthub/dolt-sql-server:1.86.6', IMAGE)

    def test_published_port_parser_accepts_ipv4_and_ipv6_output(self):
        self.assertEqual(33061, _published_port('127.0.0.1:33061\n'))
        self.assertEqual(33062, _published_port('[::1]:33062\n'))
        with self.assertRaisesRegex(RuntimeError, 'usable Dolt SQL port'):
            _published_port('not-published\n')


if __name__ == '__main__':
    unittest.main()
