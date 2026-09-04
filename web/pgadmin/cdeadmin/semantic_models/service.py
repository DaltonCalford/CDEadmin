##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Semantic-model lifecycle, lineage and provider compilation service."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone

from .models import (
    MODEL_STATUSES,
    SemanticCompilationUnavailable,
    SemanticModelConflict,
    SemanticModelError,
    public_model,
    validate_model,
    validate_query,
)
from .profiles import analytical_profile


class DatabaseSemanticModelRepository:
    """Store endpoint-scoped definitions and immutable revision snapshots."""

    def __init__(self):
        from pgadmin.model import (
            db, SemanticModelDefinition, SemanticModelRevision,
        )
        self.db = db
        self.definition = SemanticModelDefinition
        self.revision = SemanticModelRevision

    def list(self, user_id, endpoint_id):
        return self.definition.query.filter_by(
            user_id=user_id, endpoint_id=endpoint_id
        ).order_by(self.definition.name).all()

    def get(self, user_id, endpoint_id, model_id):
        return self.definition.query.filter_by(
            id=model_id, user_id=user_id, endpoint_id=endpoint_id
        ).first()

    def new_definition(self, **values):
        return self.definition(**values)

    def save(self, row, snapshot):
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm.exc import StaleDataError
        try:
            self.db.session.add(row)
            self.db.session.flush()
            self.db.session.add(self.revision(
                id=str(uuid.uuid4()), model_id=row.id,
                revision=row.revision, status=row.status,
                definition=snapshot, created_at=datetime.now(timezone.utc),
            ))
            self.db.session.commit()
        except StaleDataError as exc:
            self.db.session.rollback()
            raise SemanticModelConflict(
                'semantic model revision has changed'
            ) from exc
        except IntegrityError as exc:
            self.db.session.rollback()
            raise SemanticModelError(
                'semantic model name or revision already exists'
            ) from exc

    def revisions(self, model_id):
        return self.revision.query.filter_by(model_id=model_id).order_by(
            self.revision.revision.desc()
        ).all()

    def delete(self, row):
        self.db.session.delete(row)
        self.db.session.commit()


class SemanticModelService:
    """Own semantic metadata while leaving native semantics to providers."""

    def __init__(self, repository=None):
        self.repository = repository or DatabaseSemanticModelRepository()

    @staticmethod
    def capabilities(provider):
        descriptor = getattr(provider, 'semantic_model_descriptor', None)
        provider_value = descriptor() if callable(descriptor) else None
        model_family = (
            provider_value.get('model_family') if provider_value else None
        )
        return {
            'contract_version': '1.0.0',
            'designer': True,
            'model_designer': True,
            'relationship_diagram': True,
            'fact_dimension_classification': True,
            'grain_declaration': True,
            'revision_history': True,
            'validation': True,
            'lineage': True,
            'query_builder': True,
            'pivot_cellset': True,
            'slice_dice': True,
            'drill': True,
            'drill_down': True,
            'drill_through': True,
            'cross_filtering': True,
            'time_intelligence': True,
            'parameters': True,
            'chart_builder': True,
            'dashboard_builder': True,
            'report_builder': True,
            'scheduled_report_definitions': True,
            'scheduled_report_execution': bool(
                provider_value and provider_value.get(
                    'report_scheduler_available', False
                )
            ),
            'row_level_security': True,
            'tenant_filtering': True,
            'metric_certification': True,
            'query_diagnostics': True,
            'result_reproducibility': True,
            'export': ['csv', 'json', 'jsonl', 'xlsx', 'svg'],
            'provider_compiler': provider_value,
            'analytical_profile': analytical_profile(model_family),
            'execution_available': bool(
                provider_value and provider_value.get('execution_available')
            ),
            'materialization': (
                provider_value.get('materialization', {})
                if provider_value else {'execution_available': False}
            ),
        }

    def list(self, user_id, endpoint_id):
        return [self._summary(row) for row in self.repository.list(
            user_id, endpoint_id
        )]

    def get(self, user_id, endpoint_id, model_id):
        return self._present(self._required(user_id, endpoint_id, model_id))

    def create(self, user_id, endpoint_id, value):
        model = validate_model(value)
        now = datetime.now(timezone.utc)
        row = self.repository.new_definition(
            id=str(uuid.uuid4()), user_id=user_id, endpoint_id=endpoint_id,
            name=model['name'], status='draft', revision=1,
            definition=json.dumps(public_model(model)), created_at=now,
            updated_at=now,
        )
        self.repository.save(row, row.definition)
        return self._present(row)

    def update(self, user_id, endpoint_id, model_id, expected_revision, value):
        row = self._required(user_id, endpoint_id, model_id)
        self._check_revision(row, expected_revision)
        if row.status == 'published':
            raise SemanticModelError(
                'published models must be cloned before editing'
            )
        model = validate_model(value)
        row.name = model['name']
        row.definition = json.dumps(public_model(model))
        row.revision += 1
        row.updated_at = datetime.now(timezone.utc)
        self.repository.save(row, row.definition)
        return self._present(row)

    def set_status(self, user_id, endpoint_id, model_id, expected_revision,
                   status):
        row = self._required(user_id, endpoint_id, model_id)
        self._check_revision(row, expected_revision)
        if status not in MODEL_STATUSES:
            raise SemanticModelError('semantic model status is unsupported')
        validate_model(json.loads(row.definition))
        row.status = status
        row.revision += 1
        row.updated_at = datetime.now(timezone.utc)
        self.repository.save(row, row.definition)
        return self._present(row)

    def clone(self, user_id, endpoint_id, model_id, name):
        row = self._required(user_id, endpoint_id, model_id)
        value = json.loads(row.definition)
        value['name'] = name
        return self.create(user_id, endpoint_id, value)

    def delete(self, user_id, endpoint_id, model_id, expected_revision):
        row = self._required(user_id, endpoint_id, model_id)
        self._check_revision(row, expected_revision)
        self.repository.delete(row)
        return {'model_id': model_id, 'deleted': True}

    def history(self, user_id, endpoint_id, model_id):
        row = self._required(user_id, endpoint_id, model_id)
        return [{
            'revision': item.revision, 'status': item.status,
            'created_at': item.created_at.isoformat(),
        } for item in self.repository.revisions(row.id)]

    def compare(self, user_id, endpoint_id, model_id, left, right):
        row = self._required(user_id, endpoint_id, model_id)
        revisions = {
            item.revision: item
            for item in self.repository.revisions(row.id)
        }
        if left not in revisions or right not in revisions:
            raise SemanticModelError(
                'requested semantic model revision is unavailable'
            )
        before = json.loads(revisions[left].definition)
        after = json.loads(revisions[right].definition)
        keys = sorted(set(before) | set(after))
        return {'model_id': model_id, 'left_revision': left,
                'right_revision': right, 'changes': [{
                    'field': key, 'before': before.get(key),
                    'after': after.get(key),
                } for key in keys if before.get(key) != after.get(key)]}

    def validate(self, value):
        try:
            model = validate_model(value)
        except SemanticModelError as exc:
            return {'valid': False, 'errors': [{'message': str(exc)}]}
        return {'valid': True, 'errors': [], 'lineage': self.lineage(model)}

    @staticmethod
    def lineage(model_value):
        model = validate_model(model_value)
        nodes = []
        edges = []
        for source in model['sources']:
            nodes.append({'id': f"source:{source['id']}", 'kind': 'source',
                          'label': '.'.join(source['relation']),
                          'classification': source['classification'],
                          'source_kind': source['source_kind'],
                          'grain': copy.deepcopy(source['grain'])})
        for join in model['joins']:
            edges.append({
                'from': f"source:{join['left_source']}",
                'to': f"source:{join['right_source']}",
                'kind': 'join',
                'relationship_id': join['id'],
                'cardinality': join['cardinality'],
            })
        for relationship in model['relationships']:
            edges.append({
                'from': f"source:{relationship['from_source']}",
                'to': f"source:{relationship['to_source']}",
                'kind': relationship['relationship_kind'],
                'relationship_id': relationship['id'],
                'provider_config': copy.deepcopy(
                    relationship['provider_config']
                ),
            })
        for dimension in model['dimensions']:
            node = f"dimension:{dimension['id']}"
            nodes.append({'id': node, 'kind': 'dimension',
                          'label': dimension['name']})
            edges.append({'from': f"source:{dimension['field']['source_id']}",
                          'to': node, 'kind': 'derives'})
            for hierarchy in dimension.get('hierarchies', []):
                for level in hierarchy['levels']:
                    level_node = f"level:{level['id']}"
                    nodes.append({'id': level_node, 'kind': 'level',
                                  'label': level['name']})
                    edges.append({'from': node, 'to': level_node,
                                  'kind': 'contains'})
        for measure in model['measures']:
            node = f"measure:{measure['id']}"
            nodes.append({'id': node, 'kind': 'measure',
                          'label': measure['name'],
                          'certification': copy.deepcopy(
                              measure['certification']
                          )})
            if measure.get('field'):
                source_id = measure['field']['source_id']
                edges.append({
                    'from': f'source:{source_id}',
                    'to': node, 'kind': 'aggregates',
                })
        for item in model['materializations']:
            node = f"materialization:{item['id']}"
            nodes.append({'id': node, 'kind': 'materialization',
                          'label': item['name']})
            edges.extend({'from': f'measure:{measure}', 'to': node,
                          'kind': 'materializes'}
                         for measure in model['_symbols']['measure_ids'])
        for chart in model['visualizations']:
            node = f"visualization:{chart['id']}"
            nodes.append({
                'id': node, 'kind': 'visualization', 'label': chart['name'],
            })
            edges.extend({
                'from': f'measure:{measure_id}', 'to': node,
                'kind': 'visualizes',
            } for measure_id in chart['query'].get('measures', [])
                if measure_id in model['_symbols']['measure_ids'])
        for dashboard in model['dashboards']:
            node = f"dashboard:{dashboard['id']}"
            nodes.append({
                'id': node, 'kind': 'dashboard',
                'label': dashboard['name'],
            })
            edges.extend({
                'from': f"visualization:{tile['visualization_id']}",
                'to': node, 'kind': 'placed-on',
            } for tile in dashboard['tiles'])
        for report in model['reports']:
            node = f"report:{report['id']}"
            nodes.append({
                'id': node, 'kind': 'report', 'label': report['name'],
            })
            if report.get('dashboard_id'):
                edges.append({
                    'from': f"dashboard:{report['dashboard_id']}",
                    'to': node, 'kind': 'published-as',
                })
        return {'nodes': nodes, 'edges': edges}

    def compile(self, provider, model_value, query_value,
                security_context=None):
        model = validate_model(model_value)
        descriptor = getattr(provider, 'semantic_model_descriptor', None)
        provider_value = descriptor() if callable(descriptor) else None
        if provider_value:
            profile = analytical_profile(provider_value.get('model_family'))
            if profile['recognized_model_family'] and model[
                    'semantic_family'] != profile['semantic_family']:
                raise SemanticCompilationUnavailable(
                    'semantic model family does not match the endpoint '
                    'provider'
                )
        query = validate_query(model, query_value)
        query = self._apply_security(model, query, security_context)
        callback = getattr(provider, 'compile_semantic_query', None)
        if not callable(callback):
            raise SemanticCompilationUnavailable(
                'endpoint provider has no semantic-query compiler'
            )
        compiled = callback(public_model(model), query)
        if not isinstance(compiled, dict):
            raise SemanticModelError(
                'provider semantic compiler must return an object'
            )
        value = copy.deepcopy(compiled)
        value['reproducibility'] = self.reproducibility_manifest(
            provider, model, query, value
        )
        return value

    @staticmethod
    def _apply_security(model, query, security_context):
        """Bind trusted principal claims to provider-compiled filters."""
        security = model['security']
        if not security.get('tenant_filter') and not security.get('roles'):
            return query
        context = security_context if isinstance(
            security_context, dict
        ) else {}
        claims = context.get('claims', {})
        if not isinstance(claims, dict):
            claims = {}
        secured = copy.deepcopy(query)
        tenant = security.get('tenant_filter')
        if tenant is not None:
            claim = tenant['principal_claim']
            if claim not in claims:
                if tenant['required']:
                    raise SemanticModelError(
                        'required tenant identity is unavailable'
                    )
            else:
                secured['cross_filters'].append({
                    'field': copy.deepcopy(tenant['field']),
                    'operator': 'eq', 'value': claims[claim],
                    'security_filter': 'tenant',
                })
        matched_role = False
        for role in security.get('roles', []):
            principal_roles = claims.get(role['principal_claim'], [])
            if isinstance(principal_roles, str):
                principal_roles = [principal_roles]
            if not isinstance(principal_roles, list):
                principal_roles = []
            if role['name'] in principal_roles:
                matched_role = True
                secured['cross_filters'].extend(
                    copy.deepcopy(role['filters'])
                )
        if security.get('roles') and not matched_role:
            raise SemanticModelError(
                'no semantic security role applies to this principal'
            )
        return secured

    @staticmethod
    def reproducibility_manifest(provider, model, query, compiled):
        """Fingerprint inputs without claiming a provider snapshot."""
        descriptor = getattr(provider, 'semantic_model_descriptor', None)
        provider_value = descriptor() if callable(descriptor) else {}

        def digest(value):
            encoded = json.dumps(
                value, sort_keys=True, separators=(',', ':'),
                ensure_ascii=False, default=repr,
            ).encode('utf-8')
            return hashlib.sha256(encoded).hexdigest()

        public = public_model(model)
        compiled_public = {
            key: value for key, value in compiled.items()
            if key != 'reproducibility'
        }
        return {
            'schema': 'cdeadmin.semantic-reproducibility.v1',
            'model_digest': digest(public),
            'query_digest': digest(query),
            'compiled_digest': digest(compiled_public),
            'provider_id': provider_value.get('provider_id'),
            'engine_id': provider_value.get('engine_id'),
            'provider_model_family': provider_value.get('model_family'),
            'language_profile': compiled.get('language_profile'),
            'provider_snapshot': None,
            'exact_data_replay_requires_provider_snapshot': True,
            'common_layer_infers_snapshot_or_finality': False,
        }

    def diagnostics(self, provider, model_value, query_value,
                    security_context=None):
        """Return compilation diagnostics plus optional provider analysis."""
        model = validate_model(model_value)
        query = validate_query(model, query_value)
        secured_query = self._apply_security(
            model, query, security_context
        )
        compiled = self.compile(
            provider, model, query, security_context=security_context
        )
        callback = getattr(provider, 'diagnose_semantic_query', None)
        provider_diagnostics = callback(
            public_model(model), secured_query, copy.deepcopy(compiled)
        ) if callable(callback) else None
        return {
            'schema': 'cdeadmin.semantic-query-diagnostics.v1',
            'compilation': {
                'language_profile': compiled.get('language_profile'),
                'warnings': copy.deepcopy(compiled.get('warnings', [])),
                'projection': copy.deepcopy(compiled.get('projection', {})),
                'source_count': len(model['sources']),
                'relationship_count': (
                    len(model['joins']) + len(model['relationships'])
                ),
                'dimension_count': len(model['dimensions']),
                'measure_count': len(query['measures']),
            },
            'provider_diagnostics': provider_diagnostics,
            'provider_diagnostics_available': callable(callback),
            'reproducibility': compiled['reproducibility'],
        }

    @staticmethod
    def cellset(model_value, query_value, records):
        model = validate_model(model_value)
        query = validate_query(model, query_value)
        if not isinstance(records, list):
            raise SemanticModelError(
                'semantic result records must be an array'
            )
        levels = []
        for axis in ('pages', 'rows', 'columns'):
            levels.extend(query['axes'][axis])
        cells = []
        for record in records:
            if not isinstance(record, dict):
                raise SemanticModelError(
                    'semantic result rows must be objects'
                )
            cells.append({
                'coordinates': {item: record.get(item) for item in levels},
                'measures': {
                    item: record.get(item) for item in query['measures']
                },
            })
        return {
            'family': 'cellset', 'axes': query['axes'],
            'levels': levels, 'measures': query['measures'],
            'cells': cells, 'records': copy.deepcopy(records),
            'formats': {item['id']: item.get('format', '')
                        for item in model['measures']},
            'drill': {'supported': True, 'active_levels': levels},
            'slice': copy.deepcopy(query['filters']),
        }

    def _required(self, user_id, endpoint_id, model_id):
        row = self.repository.get(user_id, endpoint_id, model_id)
        if row is None:
            raise SemanticModelError('semantic model is unavailable')
        return row

    @staticmethod
    def _check_revision(row, expected):
        if isinstance(expected, bool) or not isinstance(expected, int):
            raise SemanticModelError('expected_revision must be an integer')
        if row.revision != expected:
            raise SemanticModelConflict('semantic model revision has changed')

    @staticmethod
    def _summary(row):
        return {'model_id': row.id, 'name': row.name, 'status': row.status,
                'revision': row.revision,
                'updated_at': row.updated_at.isoformat()}

    @classmethod
    def _present(cls, row):
        definition = public_model(validate_model(json.loads(row.definition)))
        return cls._summary(row) | {'definition': definition}
