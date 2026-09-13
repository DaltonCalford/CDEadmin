"""Regression checks for the read-only catalog handoff audit."""
import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    'catalog_qa_audit', Path(__file__).resolve().parents[1] /
    'cdeadmin_catalog_qa_audit.py')
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class CatalogAuditTests(unittest.TestCase):
    def test_demo_authentication_is_not_fabricated(self):
        route = AUDIT.native_demo_route('mongodb', {
            'port': 57017, 'database': 'demo'}, '127.0.0.1')
        self.assertNotIn('username', route)
        self.assertNotIn('user', route)
        self.assertNotIn('password', route)

    def test_cql_does_not_receive_sql_database_route(self):
        route = AUDIT.native_demo_route('cassandra', {
            'port': 19042, 'database': 'demo', 'local_dc': 'dc1',
            'user': 'cassandra'}, '127.0.0.1')
        self.assertNotIn('database', route)
        self.assertEqual('cassandra', route['username'])

    def test_secret_lease_is_wiped(self):
        lease = AUDIT.Lease('test-only')
        with lease:
            self.assertEqual(b'test-only', lease.use(bytes))
        self.assertFalse(any(lease.value))

    def test_suppressed_query_failure_is_observed_without_sql(self):
        class Cursor:
            def execute(self, *_args):
                raise ValueError('private detail')

        class Connection:
            def cursor(self):
                return Cursor()

        failures = []
        connection = AUDIT.CatalogConnection(Connection(), failures)
        with self.assertRaises(ValueError):
            connection.cursor().execute('private SQL', ('private binding',))
        self.assertEqual('ValueError', failures[0]['error_type'])
        self.assertEqual(64, len(failures[0]['query_sha256']))
        self.assertNotIn('private', str(failures))

    def test_writes_are_not_authorized(self):
        permissions = AUDIT.Permissions('test-only')
        for permission in ('data_write', 'schema_write', 'admin'):
            self.assertFalse(permissions.allows(permission))
            with self.assertRaises(PermissionError):
                permissions.require(permission)

    def test_normalized_table_descendants_are_reached(self):
        resources = [{
            'resource_id': identity, 'resource_kind': kind,
            'display_name': name, 'display_path': path,
            'authority_path': [identity],
        } for identity, kind, name, path in (
            ('t', 'table', 'orders', ['main', 'orders']),
            ('c', 'column', 'id', ['main', 'orders', 'id']),
        )]
        result = AUDIT.walk(resources, 'demo.db')
        self.assertEqual(2, result['reached_count'])
        self.assertFalse(result['errors'])
        self.assertFalse(result['unreachable_resource_ids'])

    def test_ambiguous_child_placement_blocks_signoff(self):
        resources = [{
            'resource_id': identity, 'resource_kind': kind,
            'display_name': name, 'display_path': path,
            'authority_path': [identity],
        } for identity, kind, name, path in (
            ('t', 'table', 'orders', ['orders']),
            ('s', 'type', 'orders', ['orders']),
            ('c', 'column', 'id', ['orders', 'id']),
        )]
        result = AUDIT.walk(resources, 'demo')
        self.assertEqual(['c'], result['multiply_presented_resource_ids'])
