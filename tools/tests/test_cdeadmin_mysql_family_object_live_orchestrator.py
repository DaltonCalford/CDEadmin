##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""MySQL-family exact-container orchestration tests."""

import unittest

from tools.cdeadmin_mysql_family_object_live_orchestrator import (
    IMAGES,
    _published_port,
)


class MySQLFamilyObjectLiveOrchestratorTests(unittest.TestCase):

    def test_exact_reference_images_are_pinned(self):
        self.assertEqual('mysql:9.7.0', IMAGES['mysql'])
        self.assertEqual('mariadb:12.2.2', IMAGES['mariadb'])

    def test_published_port_parser_accepts_ipv4_and_ipv6_output(self):
        self.assertEqual(33061, _published_port('127.0.0.1:33061\n'))
        self.assertEqual(33062, _published_port('[::1]:33062\n'))
        with self.assertRaisesRegex(RuntimeError, 'usable database port'):
            _published_port('not-published\n')


if __name__ == '__main__':
    unittest.main()
