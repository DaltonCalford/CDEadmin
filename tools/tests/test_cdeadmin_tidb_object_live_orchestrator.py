##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""TiDB exact-container object qualification orchestration tests."""

import unittest

from tools.cdeadmin_tidb_object_live_orchestrator import (
    IMAGE,
    _published_port,
)


class TiDBObjectLiveOrchestratorTests(unittest.TestCase):

    def test_exact_reference_image_is_pinned(self):
        self.assertEqual('pingcap/tidb:v8.5.6', IMAGE)

    def test_published_port_parser_accepts_ipv4_and_ipv6_output(self):
        self.assertEqual(40001, _published_port('127.0.0.1:40001\n'))
        self.assertEqual(40002, _published_port('[::1]:40002\n'))
        with self.assertRaisesRegex(RuntimeError, 'usable TiDB SQL port'):
            _published_port('not-published\n')


if __name__ == '__main__':
    unittest.main()
