##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""MongoDB document provider, driver, and administration tests."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.mongodb.client import (  # noqa: E402
    MongoDBClient,
    MongoDBClientError,
)
from pgadmin.cdeadmin.providers.mongodb.provider import (  # noqa: E402
    MongoDBPilotProvider,
    PROFILE,
)
from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    ProviderVisualAdministration,
)


class SecretLease:
    def __init__(self, value):
        self.value = bytearray(value)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for offset in range(len(self.value)):
            self.value[offset] = 0
        self.closed = True

    def use(self, callback):
        return callback(memoryview(self.value))


class Cursor:
    def __init__(self, documents):
        self.documents = list(documents)
        self.index = 0

    def sort(self, _sort):
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def batch_size(self, _count):
        return self

    def close(self):
        return None

    def try_next(self):
        if not self.documents:
            return None
        return self.documents.pop(0)

    def __iter__(self):
        return self

    def __next__(self):
        if self.index >= len(self.documents):
            raise StopIteration
        value = self.documents[self.index]
        self.index += 1
        return value


class Collection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    def find(self, *_args, **_kwargs):
        return Cursor(self.documents)

    def aggregate(self, _pipeline, **_kwargs):
        return Cursor(self.documents)

    def watch(self, **_kwargs):
        value = Cursor(self.documents)
        value.resume_token = {'token': 1}
        return value

    def count_documents(self, _selector, **_kwargs):
        return len(self.documents)

    def insert_many(self, documents, **_kwargs):
        self.documents.extend(documents)
        return SimpleNamespace(acknowledged=True)

    @staticmethod
    def list_indexes():
        return [{'name': '_id_', 'key': {'_id': 1}, 'v': 2}]


class Database:
    def __init__(self, name, client):
        self.name = name
        self.client = client
        self.collections = {
            'widgets': Collection([{'_id': 1, 'name': 'first'}]),
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())

    def command(self, command, **_kwargs):
        name = command if isinstance(command, str) else next(iter(command))
        if name == 'ping':
            return {'ok': 1.0}
        if name == 'buildInfo':
            return {
                'version': '8.2.6',
                'gitVersion': '5d25c835745d06f712320b6cdae9d50b7b43663e',
            }
        if name == 'hello':
            return {
                'setName': 'cdeadmin-rs', 'maxWireVersion': 27,
                'minWireVersion': 0,
            }
        if name == 'connectionStatus':
            return {'authInfo': {
                'authenticatedUsers': [{'user': 'operator', 'db': 'admin'}],
                'authenticatedUserRoles': [],
                'authenticatedUserPrivileges': [],
            }}
        if name == 'usersInfo':
            return {'users': []}
        if name == 'rolesInfo':
            return {'roles': []}
        return {'ok': 1.0, 'command': name}

    @staticmethod
    def list_collections():
        return [{
            'name': 'widgets', 'type': 'collection',
            'options': {'validator': {'name': {'$type': 'string'}}},
        }]


class DriverSession:
    in_transaction = False
    has_ended = False
    session_id = {'id': b'opaque-session'}

    def end_session(self):
        self.has_ended = True

    def start_transaction(self):
        self.in_transaction = True

    def commit_transaction(self):
        self.in_transaction = False

    def abort_transaction(self):
        self.in_transaction = False


class DriverClient:
    def __init__(self, **arguments):
        self.arguments = arguments
        self.databases = {}
        self.admin = self['admin']
        self.closed = False

    def __getitem__(self, name):
        return self.databases.setdefault(name, Database(name, self))

    def start_session(self, **options):
        self.session_options = options
        return DriverSession()

    @staticmethod
    def list_database_names():
        return ['admin', 'qualification']

    def close(self):
        self.closed = True


class Permissions:
    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True


def client(connector=DriverClient, secret_acquirer=None):
    module = SimpleNamespace(MongoClient=connector)
    return MongoDBClient(secret_acquirer=secret_acquirer, module=module)


def context():
    return SimpleNamespace(
        endpoint_id='endpoint', mode='legacy_native',
        runtime_verification_state='verified',
        verified_runtime_family='mongodb',
        declared_runtime_family='mongodb',
        effective_permissions=frozenset({
            'data_read', 'data_write', 'administer', 'execute', 'network',
        }),
        session_namespace='session', cache_namespace='cache',
    )


class MongoDBProviderTests(unittest.TestCase):

    def test_collection_and_view_collation_forms_compile_native_options(self):
        adapter = client()
        for kind in ('collection', 'view'):
            visual = ProviderVisualAdministration(
                context(), Permissions(), 'mongodb', '8.2.6', adapter)
            request = {'resource_kind': kind, 'operation_id': 'create',
                       'target_resource': {'native': {'database': 'example'}},
                       'draft': {'name': 'example', 'options': {
                           'database': 'example', 'view_on': 'source',
                           'pipeline': []} if kind == 'view' else {
                               'database': 'example'},
                           'collation_mode': 'locale',
                           'collation_locale': 'en',
                           'collation_strength': '2'}}
            valid = visual.validate(request)
            self.assertTrue(valid['valid'], valid['errors'])
            plan = adapter.plan_admin_operation({
                **request, 'draft': valid['draft']})
            draft = json.loads(json.dumps(plan['provider_payload']))['draft']
            self.assertEqual({'name', 'options'}, set(draft))
            options = adapter._collection_options(draft)
            self.assertEqual({'locale': 'en', 'strength': 2},
                             options['collation'])
            if kind == 'view':
                self.assertEqual('source', options['view_on'])
                self.assertEqual([], options['pipeline'])
            form = adapter._admin_form(kind, 'create')
            mode = next(f for f in form['fields']
                        if f['field_id'] == 'collation_mode')
            self.assertEqual(kind.title() + ' collation', mode['label'])
            self.assertNotIn('Collection default', mode['options'][0]['label'])
        adapter.close()

    def test_collection_collation_conflicts_validate_before_execution(self):
        adapter = client()
        for options in ([], {'collation': {'locale': 'en'}}):
            request = {'resource_kind': 'collection', 'operation_id': 'create',
                       'draft': {'name': 'example', 'options': options,
                                 'collation_mode': 'simple'}}
            self.assertTrue(
                adapter.validate_admin_operation(request)['errors'])
            with self.assertRaises(MongoDBClientError):
                adapter.plan_admin_operation(request)
        original = {'options': {'collation': {'locale': 'fr'},
                                'validator': {'a': {'$gte': 0}}}}
        options = adapter._collection_options(original)
        options['collation']['locale'] = 'en'
        self.assertEqual('fr', original['options']['collation']['locale'])
        adapter.close()

    def test_geo_options_native_mapping_defaults_and_hidden_fields(self):
        base = {'name': 'geo', 'keys': [{'field': 'point', 'kind': '2d'}],
                'geo_mode': '2d'}
        for bits in (1, 26, 32):
            result = MongoDBClient._index_options({
                **base, 'geo_bits': bits, 'geo_min': -100.5, 'geo_max': 100.5})
            self.assertEqual((bits, -100.5, 100.5),
                             (result['bits'], result['min'], result['max']))
        self.assertNotIn('bits', MongoDBClient._index_options(base))
        self.assertNotIn('bits', MongoDBClient._index_options({
            **base, 'geo_mode': 'native', 'geo_bits': 'ignored'}))
        for version in ('1', '2', '3'):
            result = MongoDBClient._index_options({
                'name': 'geo', 'keys': [{'field': 'p', 'kind': '2dsphere'}],
                'geo_mode': '2dsphere', 'geo_version': version,
                'geo_bits': 'ignored'})
            self.assertEqual(int(version), result['2dsphereIndexVersion'])

    def test_geo_form_validation_and_plan_roundtrip(self):
        adapter = client()
        visual = ProviderVisualAdministration(
            context(), Permissions(), 'mongodb', '8.2.6', adapter)
        request = {'resource_kind': 'index', 'operation_id': 'create',
                   'target_resource': {'native': {'collection': 'items'}},
                   'draft': {'name': 'geo', 'keys': [
                       {'field': 'point', 'kind': '2d'}], 'geo_mode': '2d',
                       'geo_bits': 26, 'geo_min': -100.5, 'geo_max': 100.5,
                       'geo_version': 'hidden-invalid'}}
        valid = visual.validate(request)
        self.assertTrue(valid['valid'], valid['errors'])
        self.assertNotIn('geo_version', valid['draft'])
        plan = adapter.plan_admin_operation({
            **request, 'draft': valid['draft']})
        draft = json.loads(json.dumps(plan['provider_payload']))['draft']
        self.assertEqual({'name', 'options'}, set(draft))
        self.assertEqual(26, MongoDBClient._index_options(draft)['bits'])
        adapter.close()

    def test_geo_rejects_invalid_visual_values_and_conflicts(self):
        base = {'name': 'geo', 'keys': [{'field': 'p', 'kind': '2d'}],
                'geo_mode': '2d'}
        invalid = [{'geo_mode': 'bad'}, {'geo_mode': '2dsphere'},
                   {'geo_min': 200}, {'geo_max': -200},
                   {'geo_min': 0, 'geo_max': 0},
                   {'geo_bits': 2, 'options': {'bits': 2}}]
        invalid += [{'geo_bits': v} for v in (0, 33, True, 1.5, '2')]
        invalid += [{'geo_min': v} for v in (
            True, '1', float('nan'), float('inf'), float('-inf'),
            10 ** 400, -(10 ** 400))]
        for draft in invalid:
            with self.subTest(draft=draft):
                with self.assertRaises(MongoDBClientError):
                    MongoDBClient._index_options({**base, **draft})
        for draft in ({'geo_version': '4'}, {
                'geo_version': '3', 'options': {'2dsphereIndexVersion': 3}}):
            with self.assertRaises(MongoDBClientError):
                MongoDBClient._index_options({
                    **base, 'keys': [{'field': 'p', 'kind': '2dsphere'}],
                    'geo_mode': '2dsphere', **draft})

    def test_wildcard_projection_modes_defaults_and_plan(self):
        adapter = client()
        visual = ProviderVisualAdministration(
            context(), Permissions(), 'mongodb', '8.2.6', adapter)
        request = {'resource_kind': 'index', 'operation_id': 'create',
                   'target_resource': {'native': {'collection': 'items'}},
                   'draft': {'name': 'wild', 'keys': [
                       {'field': '$**', 'kind': 'ascending'}],
                       'configure_wildcard': True}}
        for action, opposite in (('include', 'exclude'),
                                 ('exclude', 'include')):
            request['draft']['wildcard_fields'] = [
                {'field': 'a.b', 'action': action},
                {'field': '_id', 'action': opposite}]
            valid = visual.validate(request)
            self.assertTrue(valid['valid'], valid['errors'])
            plan = adapter.plan_admin_operation({
                **request, 'draft': valid['draft']})
            draft = json.loads(json.dumps(plan['provider_payload']))['draft']
            self.assertEqual({'a.b': int(action == 'include'),
                              '_id': int(opposite == 'include')},
                             MongoDBClient._index_options(draft)[
                                 'wildcardProjection'])
        request['draft'].update(configure_wildcard=False,
                                wildcard_fields='ignored')
        valid = visual.validate(request)
        self.assertTrue(valid['valid'], valid['errors'])
        self.assertNotIn('wildcard_fields', valid['draft'])
        self.assertNotIn('wildcardProjection', MongoDBClient._index_options(
            valid['draft']))
        adapter.close()

    def test_wildcard_projection_rejects_invalid_controls(self):
        good = {'field': 'a', 'action': 'include'}
        base = {'name': 'wild', 'keys': [
                    {'field': '$**', 'kind': 'ascending'}],
                'configure_wildcard': True, 'wildcard_fields': [good]}
        invalid = [{'configure_wildcard': 1}, {'wildcard_fields': []},
                   {'wildcard_fields': {}}, {'wildcard_fields': [1]},
                   {'wildcard_fields': [good, good]},
                   {'wildcard_fields': [dict(good, extra=True)]},
                   {'wildcard_fields': [dict(good, action='bad')]},
                   {'wildcard_fields': [dict(good, field='')]},
                   {'wildcard_fields': [dict(good, field='a\x00')]},
                   {'wildcard_fields': [good, {
                       'field': 'b', 'action': 'exclude'}]},
                   {'options': {'wildcardProjection': {'a': 1}}}]
        invalid += [{'keys': [{'field': field, 'kind': kind}]}
                    for field, kind in (('a', 'ascending'),
                                        ('a.$**', 'ascending'),
                                        ('$**', 'text'))]
        for draft in invalid:
            with self.subTest(draft=draft):
                with self.assertRaises(MongoDBClientError):
                    MongoDBClient._index_options({**base, **draft})
        native = {'nested': {'a': 1}}
        self.assertEqual(native, MongoDBClient._index_options({
            **base, 'configure_wildcard': False,
            'options': {'wildcardProjection': native}})['wildcardProjection'])

    def test_index_collation_modes_and_all_visual_options(self):
        base = {'name': 'collated', 'keys': [
            {'field': 'name', 'kind': 'ascending'}]}
        result = MongoDBClient._index_options({
            **base, 'collation_mode': 'simple', 'collation_strength': 'bad'})
        self.assertEqual({'locale': 'simple'}, result['collation'])
        native = {'locale': 'fr', 'backwards': True}
        result = MongoDBClient._index_options({
            **base, 'options': {'collation': native}})
        self.assertEqual(native, result['collation'])
        choices = {'strength': '2', 'caseFirst': 'upper',
                   'alternate': 'shifted', 'maxVariable': 'space',
                   'caseLevel': 'enabled', 'numericOrdering': 'enabled',
                   'normalization': 'disabled', 'backwards': 'disabled',
                   'version': '57.1'}
        result = MongoDBClient._index_options({
            **base, 'collation_mode': 'locale', 'collation_locale': 'en',
            **{'collation_' + key: value for key, value in choices.items()}})
        self.assertEqual({'locale': 'en', 'strength': 2, 'caseFirst': 'upper',
                          'alternate': 'shifted', 'maxVariable': 'space',
                          'caseLevel': True, 'numericOrdering': True,
                          'normalization': False, 'backwards': False,
                          'version': '57.1'}, result['collation'])

    def test_index_collation_form_and_serialized_plan(self):
        adapter = client()
        visual = ProviderVisualAdministration(
            context(), Permissions(), 'mongodb', '8.2.6', adapter)
        request = {'resource_kind': 'index', 'operation_id': 'create',
                   'target_resource': {'native': {'collection': 'items'}},
                   'draft': {'name': 'collated', 'keys': [
                       {'field': 'name', 'kind': 'ascending'}],
                       'collation_mode': 'locale', 'collation_locale': 'en',
                       'collation_strength': '2'}}
        validated = visual.validate(request)
        self.assertTrue(validated['valid'], validated['errors'])
        plan = adapter.plan_admin_operation({
            **request, 'draft': validated['draft']})
        draft = json.loads(json.dumps(plan['provider_payload']))['draft']
        self.assertEqual({'locale': 'en', 'strength': 2},
                         MongoDBClient._index_options(draft)['collation'])
        request['draft'].update(collation_mode='simple',
                                collation_locale=None,
                                collation_strength='hidden-invalid')
        validated = visual.validate(request)
        self.assertTrue(validated['valid'], validated['errors'])
        self.assertNotIn('collation_strength', validated['draft'])
        adapter.close()

    def test_index_collation_rejects_invalid_visual_values_and_conflicts(self):
        base = {'name': 'collated', 'keys': [
            {'field': 'name', 'kind': 'ascending'}],
            'collation_mode': 'locale', 'collation_locale': 'en'}
        invalid = [{'collation_mode': 'unknown'}, {'collation_locale': ''},
                   {'collation_locale': 'en\x00'}, {'collation_version': 1},
                   {'options': {'collation': {'locale': 'en'}}}]
        for field in ('strength', 'caseFirst', 'alternate', 'maxVariable',
                      'caseLevel', 'numericOrdering', 'normalization',
                      'backwards'):
            invalid += [{'collation_' + field: value}
                        for value in (True, None, 'bad', {})]
        for draft in invalid:
            with self.subTest(draft=draft):
                with self.assertRaises(MongoDBClientError):
                    MongoDBClient._index_options({**base, **draft})

    def test_text_index_visual_options_and_plan_roundtrip(self):
        adapter = client()
        request = {'resource_kind': 'index', 'operation_id': 'create',
                   'target_resource': {'native': {'collection': 'items'}},
                   'draft': {'name': 'search', 'keys': [
                       {'field': 'title', 'kind': 'text'}],
                       'configure_text': True, 'text_weights': [
                           {'field': 'title', 'weight': 10},
                           {'field': 'body', 'weight': 1}],
                       'text_language': 'none',
                       'text_language_override': 'document_language',
                       'text_version': '3'}}
        visual = ProviderVisualAdministration(
            context(), Permissions(), 'mongodb', '8.2.6', adapter)
        validated = visual.validate(request)
        self.assertTrue(validated['valid'], validated['errors'])
        plan = adapter.plan_admin_operation({
            **request, 'draft': validated['draft']})
        draft = json.loads(json.dumps(plan['provider_payload']))['draft']
        self.assertEqual({'name', 'options'}, set(draft))
        options = MongoDBClient._index_options(draft)
        self.assertEqual({'title': 10, 'body': 1}, options['weights'])
        self.assertEqual('none', options['default_language'])
        self.assertEqual('document_language', options['language_override'])
        self.assertEqual(3, options['textIndexVersion'])
        request['draft'].update(configure_text=False, text_weights='ignored')
        validated = visual.validate(request)
        self.assertTrue(validated['valid'], validated['errors'])
        self.assertNotIn('text_weights', validated['draft'])
        adapter.close()

    def test_text_options_defaults_boundaries_and_advanced_preservation(self):
        base = {'name': 'search', 'keys': [{'field': '$**', 'kind': 'text'}],
                'configure_text': True}
        self.assertEqual({'keys': [('$**', 'text')], 'name': 'search'},
                         MongoDBClient._index_options(base))
        for weight in (1, 99999):
            options = MongoDBClient._index_options({**base, 'text_weights': [
                {'field': '$**', 'weight': weight}]})
            self.assertEqual({'$**': weight}, options['weights'])
        advanced = {'weights': {'a': 2.5}, 'default_language': 'en',
                    'textIndexVersion': 2}
        self.assertEqual(advanced, {key: value for key, value in
                         MongoDBClient._index_options({
                             **base, 'options': advanced}).items()
                         if key in advanced})

    def test_text_options_reject_invalid_records_and_conflicts(self):
        base = {'name': 'search', 'keys': [{'field': 'a', 'kind': 'text'}],
                'configure_text': True}
        invalid = [{'configure_text': 'yes'}, {'text_version': '1'},
                   {'text_language': 1}, {'text_language_override': '$bad'},
                   {'text_language_override': 'a.b'}, {'text_weights': {}},
                   {'keys': [{'field': 'a', 'kind': 'ascending'}]}]
        invalid += [{'text_weights': [{'field': 'a', 'weight': value}]}
                    for value in (0, -1, 100000, True, 1.5, '2', None)]
        invalid += [{'text_weights': [{'field': value, 'weight': 1}]}
                    for value in ('', 'a..b', '$bad', 'a.$bad', 'a\x00')]
        invalid += [{'text_weights': [1]}, {'text_weights': [
            {'field': 'a', 'weight': 1}, {'field': 'a', 'weight': 2}]}]
        for draft, native in (
                ({'text_weights': [{'field': 'a', 'weight': 1}]},
                 {'weights': {'a': 1}}),
                ({'text_language': 'none'}, {'default_language': 'none'}),
                ({'text_language_override': 'lang'},
                 {'language_override': 'lang'}),
                ({'text_version': '3'}, {'textIndexVersion': 3})):
            invalid.append({**draft, 'options': native})
        for draft in invalid:
            with self.subTest(draft=draft):
                with self.assertRaises(MongoDBClientError):
                    MongoDBClient._index_options({**base, **draft})

    def test_index_visual_flags_and_ttl_preserve_native_defaults(self):
        base = {'name': 'example', 'keys': [
            {'field': 'value', 'kind': 'ascending'}]}
        for field in ('unique', 'sparse', 'hidden'):
            for choice, expected in (('enabled', True), ('disabled', False)):
                with self.subTest(field=field, choice=choice):
                    result = MongoDBClient._index_options({
                        **base, field + '_mode': choice})
                    self.assertIs(expected, result[field])
            result = MongoDBClient._index_options({
                **base, field + '_mode': 'native'})
            self.assertNotIn(field, result)
            result = MongoDBClient._index_options({
                **base, field + '_mode': 'native', 'options': {field: True}})
            self.assertTrue(result[field])
        for ttl in (0, 60, 2147483647):
            self.assertEqual(ttl, MongoDBClient._index_options({
                **base, 'enable_ttl': True,
                'ttl_seconds': ttl})['expireAfterSeconds'])
        self.assertNotIn('expireAfterSeconds', MongoDBClient._index_options({
            **base, 'enable_ttl': False, 'ttl_seconds': 'ignored'}))

    def test_index_visual_options_reject_conflicts_and_bad_types(self):
        base = {'name': 'example', 'keys': [
            {'field': 'value', 'kind': 'ascending'}]}
        invalid = [{'enable_ttl': value} for value in (1, 'true', None)]
        invalid += [{'enable_ttl': True, 'ttl_seconds': value}
                    for value in (-1, 2147483648, True, 1.5, '10', None)]
        invalid += [{'enable_ttl': True, 'ttl_seconds': 0,
                     'options': {'expireAfterSeconds': 0}}]
        for field in ('unique', 'sparse', 'hidden'):
            invalid += [{field + '_mode': value}
                        for value in (True, None, 'unknown', {})]
            invalid += [{field + '_mode': 'enabled', 'options': {field: True}}]
        for draft in invalid:
            with self.subTest(draft=draft):
                with self.assertRaises(MongoDBClientError):
                    MongoDBClient._index_options({**base, **draft})

    def test_index_creation_visual_options_validate_and_roundtrip_plan(self):
        adapter = client()
        visual = ProviderVisualAdministration(
            context(), Permissions(), 'mongodb', '8.2.6', adapter)
        request = {'resource_kind': 'index', 'operation_id': 'create',
                   'target_resource': {'native': {'collection': 'items'}},
                   'draft': {'name': 'expiry', 'keys': [
                       {'field': 'expires', 'kind': 'ascending'}],
                       'unique_mode': 'disabled', 'sparse_mode': 'enabled',
                       'hidden_mode': 'enabled', 'enable_ttl': True,
                       'ttl_seconds': 0}}
        validated = visual.validate(request)
        self.assertTrue(validated['valid'], validated['errors'])
        plan = adapter.plan_admin_operation({
            **request, 'draft': validated['draft']})
        draft = json.loads(json.dumps(plan['provider_payload']))['draft']
        self.assertEqual({'name', 'options'}, set(draft))
        expected = {'name': 'expiry', 'keys': [['expires', 1]],
                    'unique': False, 'sparse': True, 'hidden': True,
                    'expireAfterSeconds': 0}
        self.assertEqual(expected, MongoDBClient._index_options(draft))
        self.assertIn('automatically delete', str(plan))
        request['draft']['enable_ttl'] = False
        request['draft']['ttl_seconds'] = 'ignored hidden value'
        validated = visual.validate(request)
        self.assertTrue(validated['valid'], validated['errors'])
        self.assertNotIn('ttl_seconds', validated['draft'])
        adapter.close()

    def test_index_ttl_editor_values_preserve_canonical_metadata(self):
        adapter = client()
        for seconds in (0, 60, 2147483647):
            with self.subTest(seconds=seconds):
                native = adapter._resource('index', ['db', 'items'], 'ttl',
                                           'generation', {'index': {
                                               'expireAfterSeconds': seconds,
                                           }})['native']
                self.assertEqual(seconds,
                                 native['editor_values']['ttl_seconds'])
                self.assertEqual({'$numberInt': str(seconds)},
                                 native['index']['expireAfterSeconds'])
                self.assertEqual(native['index'], native['definition'])
        native = adapter._resource('index', ['db', 'items'], 'ordinary',
                                   'generation', {'index': {'key': {'a': 1}}})
        self.assertNotIn('editor_values', native['native'])
        adapter.close()

    def test_validator_form_preserves_explicit_and_hidden_rule_semantics(self):
        adapter = client()
        visual = ProviderVisualAdministration(
            context(), Permissions(), 'mongodb', '8.2.6', adapter)
        request = {'resource_kind': 'validator', 'operation_id': 'alter',
                   'target_resource': {'native': {'collection': 'items'}},
                   'draft': {'replace_rule': True, 'validator': '{}'}}
        result = visual.validate(request)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual({}, result['draft']['validator'])
        result = visual.validate({**request, 'draft': {
            'replace_rule': False, 'validator': 'invalid hidden input',
            'validation_action': 'warn'}})
        self.assertTrue(result['valid'], result['errors'])
        self.assertNotIn('validator', result['draft'])
        adapter.close()

    def test_validator_changes_preserve_native_level_and_action(self):
        rule = {'score': {'$gte': 0}}
        wrapped = {'validator': rule, 'validationLevel': 'moderate',
                   'validationAction': 'errorAndLog'}
        for operation, container in (('create', 'options'),
                                     ('alter', 'changes')):
            self.assertEqual(wrapped, MongoDBClient._validator_changes(
                operation, {container: wrapped}))
            self.assertEqual({'validator': rule},
                             MongoDBClient._validator_changes(
                                 operation, {container: rule}))
        self.assertEqual(wrapped, MongoDBClient._validator_changes('alter', {
            'replace_rule': True, 'validator': rule,
            'validation_level': 'moderate',
            'validation_action': 'errorAndLog'}))
        self.assertEqual({'validationAction': 'warn'},
                         MongoDBClient._validator_changes('alter', {
                             'validation_action': 'warn',
                             'replace_rule': False,
                             'validator': {}}))

    def test_validator_changes_reject_ambiguous_or_invalid_edits(self):
        for draft in ({}, {'replace_rule': 'yes'},
                      {'replace_rule': True, 'validator': []},
                      {'validation_level': 'invented'},
                      {'validation_action': 'invented'},
                      {'validation_action': 'warn',
                       'changes': {'validationAction': 'error'}},
                      {'replace_rule': True, 'changes': {'validator': {}}},
                      {'changes': {'validator': {}, 'collMod': 'other'}}):
            with self.subTest(draft=draft):
                with self.assertRaises(MongoDBClientError):
                    MongoDBClient._validator_changes('alter', draft)

    def test_validator_execution_preserves_unchanged_rule(self):
        commands = []
        database = SimpleNamespace(command=commands.append)
        native = {'collection': 'selected'}
        MongoDBClient._apply_validator(database, 'alter', {
            'validation_level': 'strict'}, native)
        MongoDBClient._apply_validator(database, 'alter', {
            'replace_rule': True, 'validator': {}}, native)
        MongoDBClient._apply_validator(database, 'drop', {}, native)
        self.assertEqual([
            {'collMod': 'selected', 'validationLevel': 'strict'},
            {'collMod': 'selected', 'validator': {}},
            {'collMod': 'selected', 'validator': {}},
        ], commands)

    def test_visual_index_changes_default_to_no_mutation(self):
        with self.assertRaises(MongoDBClientError):
            MongoDBClient._index_changes({})
        for draft, expected in (
                ({'visibility': 'hidden'}, {'hidden': True}),
                ({'visibility': 'visible'}, {'hidden': False}),
                ({'change_ttl': True, 'ttl_seconds': 0},
                 {'expireAfterSeconds': 0}),
                ({'uniqueness': 'prepare'}, {'prepareUnique': True}),
                ({'uniqueness': 'cancel_prepare'}, {'prepareUnique': False}),
                ({'uniqueness': 'unique'}, {'unique': True}),
                ({'uniqueness': 'non_unique'}, {'forceNonUnique': True}),
                ({'changes': {'hidden': False}}, {'hidden': False})):
            self.assertEqual(expected, MongoDBClient._index_changes(draft))

    def test_visual_index_changes_reject_conflicts_and_unsafe_targets(self):
        invalid = [
            {'changes': {'name': 'different_index', 'hidden': True}},
            {'changes': {'keyPattern': {'a': 1}, 'hidden': True}},
            {'changes': {'unknown': True}},
            {'visibility': 'hidden', 'changes': {'hidden': False}},
            {'uniqueness': 'unique', 'changes': {'forceNonUnique': True}},
            {'uniqueness': 'prepare', 'visibility': 'hidden'},
            {'changes': {'unique': False}}, {'changes': {'hidden': 'false'}},
            {'change_ttl': 'true'}, {'visibility': 'invented'},
        ]
        invalid.extend({'change_ttl': True, 'ttl_seconds': value}
                       for value in (-1, 2147483648, 1.5, True, None))
        for draft in invalid:
            with self.subTest(draft=draft):
                with self.assertRaises(MongoDBClientError):
                    MongoDBClient._index_changes(draft)

    def test_index_alter_plan_omits_unselected_fields_and_keeps_identity(self):
        adapter = client()
        request = {'resource_kind': 'index', 'operation_id': 'alter',
                   'target_resource': {'native': {
                       'database': 'qualification', 'collection': 'items',
                       'index_name': 'selected'}},
                   'draft': {'visibility': 'visible', 'change_ttl': False,
                             'ttl_seconds': 123, 'uniqueness': 'unchanged'}}
        plan = adapter.plan_admin_operation(request)
        self.assertEqual({'changes': {'hidden': False}},
                         plan['provider_payload']['draft'])
        calls = []

        class Database:
            def __getitem__(self, _name):
                return None

            def command(self, command):
                calls.append(command)

        MongoDBClient._apply_index(
            Database(), 'alter', plan['provider_payload']['draft'],
            plan['provider_payload']['native'])
        self.assertEqual([{'collMod': 'items', 'index': {
            'name': 'selected', 'hidden': False}}], calls)
        for draft, warning in (
                ({'change_ttl': True, 'ttl_seconds': 0}, 'automatic deletion'),
                ({'uniqueness': 'prepare'}, 'new duplicate keys'),
                ({'uniqueness': 'non_unique'}, 'removes uniqueness')):
            plan = adapter.plan_admin_operation({**request, 'draft': draft})
            self.assertIn(warning, ' '.join(plan['warnings']))
        adapter.close()

    def test_visual_index_keys_preserve_order_types_and_native_options(self):
        for kind, native in (('ascending', 1), ('descending', -1),
                             ('text', 'text'), ('hashed', 'hashed'),
                             ('2d', '2d'), ('2dsphere', '2dsphere')):
            draft = {'name': 'example', 'keys': [
                {'field': 'a.b', 'kind': kind}],
                'options': {'hidden': True, 'partialFilterExpression': {
                    'active': True}}}
            result = MongoDBClient._index_options(draft)
            self.assertEqual([('a.b', native)], result['keys'])
            self.assertTrue(result['hidden'])
            self.assertEqual({'active': True},
                             result['partialFilterExpression'])
            self.assertNotIn('name', draft['options'])
        result = MongoDBClient._index_options({'name': 'compound', 'keys': [
            {'field': 'b', 'kind': 'descending'},
            {'field': 'a', 'kind': 'ascending'}]})
        self.assertEqual([('b', -1), ('a', 1)], result['keys'])

    def test_visual_index_keys_reject_conflicts_and_invalid_records(self):
        good = {'field': 'a', 'kind': 'ascending'}
        for draft in ({'keys': []}, {'keys': [good, good]},
                      {'keys': [dict(good, unsupported=True)]},
                      {'keys': [dict(good, kind='invented')]},
                      {'keys': [dict(good, field='')]}, {'keys': [42]},
                      {'keys': [good], 'options': {'keys': [('a', 1)]}},
                      {'keys': [good], 'options': {'name': 'override'}}):
            with self.subTest(draft=draft):
                with self.assertRaises(MongoDBClientError):
                    MongoDBClient._index_options({'name': 'example', **draft})
        legacy = {'name': 'legacy', 'options': {
            'keys': [('a', 1)], 'unique': True}}
        self.assertEqual([('a', 1)],
                         MongoDBClient._index_options(legacy)['keys'])

    def test_visual_index_execution_uses_native_key_tuples(self):
        calls = []
        collection = SimpleNamespace(create_index=lambda keys, **options:
                                     calls.append((keys, options)))
        MongoDBClient._apply_index({'items': collection}, 'create', {
            'name': 'ordered', 'keys': [
                {'field': '$**', 'kind': 'ascending'}],
            'options': {'wildcardProjection': {'private': 0}}},
            {'collection': 'items'})
        self.assertEqual([([('$**', 1)], {
            'name': 'ordered', 'wildcardProjection': {'private': 0}})], calls)

    def test_visual_index_form_and_plan_preserve_transport_contract(self):
        adapter = client()
        form = adapter._admin_form('index', 'create')
        fields = {f['field_id']: f for f in form['fields']}
        self.assertIn('array_editor', fields['keys'])
        request = {'resource_kind': 'index', 'operation_id': 'create',
                   'target_resource': {'native': {
                       'database': 'qualification', 'collection': 'items'}},
                   'draft': {'name': 'ordered', 'keys': [
                       {'field': 'a', 'kind': 'descending'}],
                       'options': {'unique': True}}}
        self.assertFalse(adapter.validate_admin_operation(request)['errors'])
        plan = adapter.plan_admin_operation(request)
        payload = json.loads(json.dumps(plan['provider_payload']))
        self.assertNotIn('keys', payload['draft'])
        options = MongoDBClient._index_options(payload['draft'])
        self.assertEqual([['a', -1]], options['keys'])
        self.assertTrue(options['unique'])
        self.assertEqual('ordered', options['name'])
        request['draft']['keys'] = []
        self.assertTrue(adapter.validate_admin_operation(request)['errors'])
        adapter.close()

    def test_compatibility_matrix_keeps_exact_claims_fail_closed(self):
        matrix = json.loads((
            WEB / 'pgadmin/cdeadmin/providers/mongodb/'
            'compatibility_matrix.json'
        ).read_text(encoding='utf-8'))
        self.assertEqual('8.2.6', matrix['reference_profile'][
            'server_version'
        ])
        self.assertEqual('4.17.0', matrix['reference_profile'][
            'driver_version'
        ])
        self.assertEqual(
            ['8.2.6'], matrix['patch_policy']['qualified_versions']
        )
        platforms = {
            item['platform']: item for item in matrix['client_platforms']
        }
        self.assertEqual({'linux', 'macos', 'windows'}, set(platforms))
        self.assertEqual('not_run', platforms['windows']['live_suite'])
        self.assertEqual('not_run', platforms['macos']['live_suite'])

    def test_profile_selects_document_language_and_production_renderer(self):
        self.assertEqual('application/json', PROFILE.language_mime_type)
        self.assertEqual('document', PROFILE.result_renderer_kind)
        self.assertEqual(
            'cdeadmin.result.document.tree', PROFILE.result_renderer_id
        )
        self.assertEqual('documents', PROFILE.result_records_field)
        self.assertEqual(26, len(PROFILE.resource_kinds))

    def test_exact_runtime_and_structured_route_are_driver_owned(self):
        created = []

        def connector(**arguments):
            value = DriverClient(**arguments)
            created.append(value)
            return value

        value = client(connector)
        identity = value.runtime_identity({'route': {
            'host': '127.0.0.1', 'port': 27017,
            'database': 'qualification', 'route_id': 'one',
            'user': 'operator', 'connection_timeout': 9,
        }})
        self.assertEqual('8.2.6', identity['version'])
        self.assertEqual(27, identity['native']['max_wire_version'])
        self.assertEqual('127.0.0.1', created[0].arguments['host'])
        self.assertEqual('operator', created[0].arguments['username'])
        self.assertEqual('qualification', created[0].arguments['authSource'])
        self.assertEqual(9000, created[0].arguments['connectTimeoutMS'])
        self.assertNotIn('route_id', created[0].arguments)
        self.assertTrue(created[0].closed)

        for route in (
            {'uri': 'mongodb://user:password@example.invalid'},
            {'host': 'localhost', 'password': 'inline'},
        ):
            with self.assertRaisesRegex(
                MongoDBClientError, 'unknown fields'
            ):
                value.runtime_identity({'route': route})

    def test_secret_is_leased_only_for_connector_and_failures_redact_it(self):
        observed = {}
        leases = []

        def acquire(reference, principal, purpose, expected_kind):
            observed['acquisition'] = (
                reference, principal, purpose, expected_kind
            )
            lease = SecretLease(b'mongodb-password-canary')
            leases.append(lease)
            return lease

        def connector(**arguments):
            observed['password'] = arguments.pop('password')
            observed['keys'] = frozenset(arguments)
            return DriverClient(**arguments)

        value = client(connector, acquire)
        handle = value.open_session({'route': {
            'host': 'localhost', 'username': 'operator',
            'auth_source': 'admin',
            'credential_reference_id': 'secret-one',
            'principal_reference': 'principal-one',
        }})
        self.assertEqual('mongodb-password-canary', observed['password'])
        self.assertEqual(
            ('secret-one', 'principal-one', 'connect',
             'database_password'),
            observed['acquisition'],
        )
        self.assertTrue(leases[0].closed)
        self.assertEqual({0}, set(leases[0].value))
        self.assertNotIn('credential_reference_id', observed['keys'])
        value.close()
        self.assertTrue(handle.closed)

    def test_topology_consistency_pool_tls_and_compression_are_forwarded(self):
        created = []

        def connector(**arguments):
            value = DriverClient(**arguments)
            created.append(value)
            return value

        value = client(connector)
        identity = value.runtime_identity({'route': {
            'host': 'mongo-a', 'port': 27017,
            'contact_points': 'mongo-b:27018,mongo-c:27019',
            'auth_mechanism': 'NONE', 'tls': True,
            'tls_ca_file': '/certs/ca.pem',
            'tls_allow_invalid_hostnames': False,
            'compressors': 'zstd,zlib', 'zlib_compression_level': 6,
            'read_preference': 'nearest',
            'read_concern_level': 'majority', 'write_concern': '2',
            'retry_reads': True, 'retry_writes': False,
            'min_pool_size': 2, 'max_pool_size': 40,
            'max_connecting': 4, 'max_idle_time_ms': 60000,
            'wait_queue_timeout_ms': 3000,
            'heartbeat_frequency_ms': 2000,
            'wait_queue_multiple': 6,
            'server_monitoring_mode': 'poll',
            'tls_disable_ocsp_endpoint_check': True,
            'enable_overload_retargeting': True,
            'max_adaptive_retries': 4,
            'server_api_version': '1',
            'server_api_strict': True,
            'auth_oidc_allowed_hosts': ['login.example.test'],
            'tz_aware': False,
            'uuid_representation': 'pythonLegacy',
            'unicode_decode_error_handler': 'replace',
            'fsync': True,
        }})

        self.assertEqual('8.2.6', identity['version'])
        arguments = created[0].arguments
        self.assertEqual(
            ['mongo-a', 'mongo-b:27018', 'mongo-c:27019'],
            arguments['host'],
        )
        self.assertNotIn('port', arguments)
        self.assertNotIn('username', arguments)
        self.assertEqual('zstd,zlib', arguments['compressors'])
        self.assertEqual('nearest', arguments['readPreference'])
        self.assertEqual('majority', arguments['readConcernLevel'])
        self.assertEqual(2, arguments['w'])
        self.assertEqual(40, arguments['maxPoolSize'])
        self.assertEqual(2000, arguments['heartbeatFrequencyMS'])
        self.assertEqual(6, arguments['waitQueueMultiple'])
        self.assertEqual('poll', arguments['serverMonitoringMode'])
        self.assertTrue(arguments['tlsDisableOCSPEndpointCheck'])
        self.assertTrue(arguments['enableOverloadRetargeting'])
        self.assertEqual(4, arguments['maxAdaptiveRetries'])
        self.assertEqual('1', arguments['server_api'].version)
        self.assertTrue(arguments['server_api'].strict)
        self.assertEqual(
            ['login.example.test'], arguments['authOIDCAllowedHosts']
        )
        self.assertFalse(arguments['tz_aware'])
        self.assertEqual('pythonLegacy', arguments['uuidRepresentation'])
        self.assertEqual(
            'replace', arguments['unicode_decode_error_handler']
        )
        self.assertTrue(arguments['fsync'])

    def test_session_and_transaction_defaults_are_driver_owned(self):
        created = []

        def connector(**arguments):
            value = DriverClient(**arguments)
            created.append(value)
            return value

        value = client(connector)
        handle = value.open_session({'route': {
            'host': 'mongo-a', 'port': 27017,
            'auth_mechanism': 'NONE',
            'session_causal_consistency': False,
            'session_snapshot': True,
            'transaction_read_concern': 'snapshot',
            'transaction_write_concern': 'majority',
            'write_concern_timeout_ms': 2000,
            'journal': True,
            'transaction_max_commit_time_ms': 5000,
        }})
        options = created[0].session_options
        self.assertFalse(options['causal_consistency'])
        self.assertTrue(options['snapshot'])
        transaction = options['default_transaction_options']
        self.assertEqual('snapshot', transaction.read_concern.level)
        self.assertEqual('majority', transaction.write_concern.document['w'])
        self.assertEqual(5000, transaction.max_commit_time_ms)
        handle.close()

    def test_snapshot_session_rejects_causal_consistency(self):
        value = client(lambda **arguments: DriverClient(**arguments))
        with self.assertRaisesRegex(
            MongoDBClientError, 'mutually exclusive'
        ):
            value.open_session({'route': {
                'host': 'mongo-a', 'port': 27017,
                'auth_mechanism': 'NONE',
                'session_causal_consistency': True,
                'session_snapshot': True,
            }})

    def test_invalid_compression_and_topology_conflicts_fail_closed(self):
        value = client()
        with self.assertRaisesRegex(
            MongoDBClientError, 'compressor selection'
        ):
            value.runtime_identity({'route': {
                'host': 'localhost', 'compressors': 'unsupported',
            }})
        with self.assertRaisesRegex(
            MongoDBClientError, 'load-balanced mode conflicts'
        ):
            value.runtime_identity({'route': {
                'host': 'localhost', 'load_balanced': True,
                'replica_set': 'rs0',
            }})
        with self.assertRaisesRegex(
            MongoDBClientError, 'minimum pool size exceeds'
        ):
            value.runtime_identity({'route': {
                'host': 'localhost', 'min_pool_size': 10,
                'max_pool_size': 2,
            }})
        with self.assertRaisesRegex(
            MongoDBClientError, 'server monitoring mode'
        ):
            value.runtime_identity({'route': {
                'host': 'localhost', 'server_monitoring_mode': 'unsafe',
            }})

    def test_aws_and_oidc_use_typed_leased_credentials(self):
        values = {
            'aws-secret': b'aws-secret-canary',
            'aws-session': b'aws-session-canary',
            'oidc-token': b'oidc-token-canary',
        }
        acquisitions = []
        created = []

        def acquire(reference, principal, purpose, expected_kind):
            acquisitions.append((
                reference, principal, purpose, expected_kind
            ))
            return SecretLease(values[reference])

        def connector(**arguments):
            value = DriverClient(**arguments)
            created.append(value)
            return value

        value = client(connector, acquire)
        value.runtime_identity({'route': {
            'host': 'localhost', 'username': 'access-key-id',
            'auth_mechanism': 'MONGODB-AWS',
            'credential_reference_id': 'aws-secret',
            'credential_kind': 'cloud_secret_access_key',
            'credential_references': {
                'cloud_secret_access_key': 'aws-secret',
                'cloud_session_token': 'aws-session',
            },
            'principal_reference': 'principal-one',
        }})
        self.assertEqual(
            'aws-secret-canary', created[0].arguments['password']
        )
        self.assertEqual(
            'aws-session-canary',
            created[0].arguments['authMechanismProperties'][
                'AWS_SESSION_TOKEN'
            ],
        )

        value.runtime_identity({'route': {
            'host': 'localhost', 'username': 'oidc-user',
            'auth_mechanism': 'MONGODB-OIDC',
            'oidc_environment': 'callback',
            'credential_reference_id': 'oidc-token',
            'credential_kind': 'oidc_access_token',
            'credential_references': {
                'oidc_access_token': 'oidc-token',
            },
            'principal_reference': 'principal-one',
        }})
        callback = created[1].arguments['authMechanismProperties'][
            'OIDC_MACHINE_CALLBACK'
        ]
        token = callback.fetch(None)
        self.assertEqual('oidc-token-canary', token.access_token)
        self.assertIn(
            ('oidc-token', 'principal-one', 'connect', 'oidc_access_token'),
            acquisitions,
        )

    def test_json_query_results_preserve_extended_json_and_are_bounded(self):
        value = client()
        handle = value.open_session({'route': {
            'host': 'localhost', 'database': 'qualification',
        }})
        token = value.execute(handle, {'source': json.dumps({
            'operation': 'find', 'database': 'qualification',
            'collection': 'widgets', 'filter': {}, 'limit': 10,
        })})
        result = value.describe_result(token)
        self.assertEqual('document', result['result_kind'])
        self.assertEqual(
            {'$numberInt': '1'},
            result['payload']['documents'][0]['_id'],
        )
        self.assertFalse(value.cancel(token))
        transaction = value.describe_transaction(handle)
        self.assertTrue(transaction['driver_observation_only'])
        self.assertFalse(
            transaction['finality_interpreted_by_common_code']
        )
        value.control_transaction(handle, 'begin')
        self.assertTrue(value.describe_transaction(handle)['in_transaction'])
        value.control_transaction(handle, 'rollback')
        self.assertFalse(value.describe_transaction(handle)['in_transaction'])
        with self.assertRaisesRegex(MongoDBClientError, 'read-only'):
            value.execute(handle, {'source': json.dumps({
                'operation': 'command', 'database': 'qualification',
                'command': {'dropDatabase': 1},
            })})
        value.close()

    def test_resource_discovery_includes_document_native_objects(self):
        value = client()
        resources = value.list_resources({'route': {
            'host': 'localhost', 'database': 'qualification',
        }})
        kinds = {item['resource_kind'] for item in resources}
        self.assertTrue({
            'deployment', 'replica-set', 'database', 'collection',
            'validator', 'index', 'change-stream', 'aggregation-pipeline',
        }.issubset(kinds))
        collection = next(
            item for item in resources
            if item['resource_kind'] == 'collection' and
            item['native']['database'] == 'qualification'
        )
        self.assertEqual(
            'qualification', collection['native']['database']
        )
        self.assertEqual(
            {'validator': {'name': {'$type': 'string'}}},
            collection['native']['definition'],
        )
        self.assertEqual(1, len(collection['native']['indexes']))
        self.assertEqual(
            {'_id': 1},
            collection['native']['indexes'][0]['definition']['key'],
        )
        validator = next(
            item for item in resources
            if item['resource_kind'] == 'validator'
        )
        self.assertEqual(
            {'name': {'$type': 'string'}},
            validator['native']['definition'],
        )
        deployment = next(
            item for item in resources
            if item['resource_kind'] == 'deployment'
        )
        self.assertIn('hello', deployment['native']['state'])

    def test_visual_catalog_and_plan_are_provider_owned_and_redacted(self):
        value = client()
        provider = MongoDBPilotProvider(context(), Permissions(), value)
        descriptor = provider.visual_admin_descriptor()
        graphical = descriptor['graphical_interface']
        self.assertEqual('passed', graphical['activation_state'])
        self.assertEqual(74, graphical['native_operation_count'])
        self.assertEqual(74, graphical['graphical_operation_count'])
        self.assertEqual([], graphical['missing_operations'])
        coverage = descriptor['concept_coverage']
        self.assertTrue(coverage['declaration_ready'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        document = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'document'
        )
        insert = next(
            item for item in document['operations']
            if item['operation_id'] == 'insert'
        )
        self.assertTrue(insert['target_required'])
        self.assertEqual(['collection'], insert['target_resource_kinds'])
        self.assertTrue(insert['native_supported'])
        self.assertTrue(next(
            operation for item in descriptor['objects']
            if item['resource_kind'] == 'replica-set'
            for operation in item['operations']
            if operation['operation_id'] == 'alter'
        )['native_supported'])

        target = {
            'resource_kind': 'collection',
            'extensions': {'mongodb': {'native': {
                'database': 'qualification', 'collection': 'widgets',
            }}},
        }
        plan = provider.plan_visual_admin({
            'resource_kind': 'document', 'operation_id': 'insert',
            'target_resource': target,
            'draft': {'values': {'name': 'planned'}, 'options': {}},
            '_provider_route': {
                'host': 'localhost', 'database': 'qualification',
            },
        })
        self.assertEqual('ready', plan['state'])
        self.assertNotIn('_provider_route', str(plan))
        self.assertEqual(
            'pymongo', plan['command_preview']['driver']
        )

    def test_aggregation_workspace_is_typed_bounded_and_read_only(self):
        provider = MongoDBPilotProvider(
            context(), Permissions(), client()
        )
        descriptor = provider.visual_admin_descriptor()
        workspace = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'aggregation-pipeline'
        )
        execute = next(
            item for item in workspace['operations']
            if item['operation_id'] == 'execute'
        )
        self.assertEqual('read', execute['mutation_class'])
        self.assertFalse(execute['confirmation_required'])
        self.assertEqual(
            'mongodb-aggregation-pipeline', execute['form']['form_id']
        )
        target = {
            'resource_kind': 'aggregation-pipeline',
            'extensions': {'mongodb': {'native': {
                'database': 'qualification', 'collection': 'widgets',
            }}},
        }
        plan = provider.plan_visual_admin({
            'resource_kind': 'aggregation-pipeline',
            'operation_id': 'execute', 'target_resource': target,
            'draft': {
                'pipeline': [{'$match': {'name': 'first'}}],
                'options': {}, 'max_documents': 1,
            },
            '_provider_route': {
                'host': 'localhost', 'database': 'qualification',
            },
        })
        result = provider.apply_visual_admin({
            'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'], 'confirmed': False,
        })['provider_result']['observation']
        self.assertEqual(1, result['document_count'])
        self.assertFalse(result['truncated'])
        blocked = provider.validate_visual_admin({
            'resource_kind': 'aggregation-pipeline',
            'operation_id': 'execute', 'target_resource': target,
            'draft': {
                'pipeline': [{'$merge': 'other'}], 'options': {},
            },
            '_provider_route': {
                'host': 'localhost', 'database': 'qualification',
            },
        })
        self.assertFalse(blocked['valid'])

    def test_advanced_route_streaming_and_change_stream_cancellation(self):
        created = []

        def connector(**arguments):
            value = DriverClient(**arguments)
            value['qualification'].collections['widgets'] = Collection([
                {'_id': 1, 'optional': 'one'}, {'_id': 2}, {'_id': 3},
            ])
            created.append(value)
            return value

        value = client(connector)
        handle = value.open_session({'route': {
            'host': 'localhost', 'database': 'qualification',
            'auth_source': 'admin', 'replica_set': 'cdeadmin-rs',
            'direct_connection': False, 'tls': True,
            'tls_ca_file': '/tmp/ca.pem',
            'tls_certificate_key_file': '/tmp/client.pem',
            'connect_timeout_ms': 7000,
            'server_selection_timeout_ms': 8000,
            'socket_timeout_ms': 9000,
        }})
        self.assertEqual('admin', created[0].arguments['authSource'])
        self.assertEqual('cdeadmin-rs', created[0].arguments['replicaSet'])
        self.assertTrue(created[0].arguments['tls'])
        token = value.execute(handle, {'source': json.dumps({
            'operation': 'find', 'database': 'qualification',
            'collection': 'widgets', 'batch_size': 2,
            'max_documents': 3,
        })})
        first = value.describe_result(token)
        self.assertEqual(2, len(first['payload']['documents']))
        self.assertFalse(first['complete'])
        second = value.describe_result(token)
        self.assertEqual(1, len(second['payload']['documents']))
        self.assertTrue(second['complete'])

        stream = value.execute(handle, {'source': json.dumps({
            'operation': 'watch', 'database': 'qualification',
            'collection': 'widgets', 'batch_size': 2,
        })})
        watched = value.describe_result(stream)
        self.assertTrue(watched['payload']['live'])
        self.assertIsNotNone(watched['payload']['resume_token'])
        self.assertTrue(value.cancel(stream))

    def test_document_pages_use_opaque_continuations_and_schema_sampling(self):
        def connector(**arguments):
            value = DriverClient(**arguments)
            value['qualification'].collections['widgets'] = Collection([
                {'_id': 1, 'optional': 'one'}, {'_id': 2}, {'_id': 3},
            ])
            return value

        value = client(connector)
        target = {
            'resource_kind': 'collection',
            'native': {
                'database': 'qualification', 'collection': 'widgets',
                'options': {},
            },
        }
        request = {
            'target_resource': target, 'limit': 2,
            '_provider_route': {
                'host': 'localhost', 'database': 'qualification',
            },
            'filter': {}, 'projection': {}, 'sort': [],
        }
        first = value.read_admin_rows(request)
        self.assertFalse(first['complete'])
        self.assertTrue(first['continuation'])
        optional = next(
            item for item in first['schema_sample']['fields']
            if item['path'] == 'optional'
        )
        self.assertEqual(1, optional['missing_count'])
        second = value.read_admin_rows({
            **request, 'continuation': first['continuation'],
        })
        self.assertTrue(second['complete'])


if __name__ == '__main__':
    unittest.main()
