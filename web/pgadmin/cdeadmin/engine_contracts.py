##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Strict exact-version dialect and metric activation contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from importlib import resources
from typing import Any, Mapping


class EngineContractError(ValueError):
    """An engine contract is absent, incomplete, or internally inconsistent."""


_DESCRIPTOR_CACHE = {}
_DESCRIPTOR_CACHE_LOCK = threading.RLock()


def _required(value, name):
    if not isinstance(value, str) or not value.strip():
        raise EngineContractError(f'{name} must not be empty')
    return value.strip()


def _string_list(value, name, *, allow_empty=False):
    if not isinstance(value, list) or (
            not allow_empty and not value):
        raise EngineContractError(f'{name} must be a non-empty array')
    result = [_required(item, f'{name} item') for item in value]
    if len(result) != len(set(result)):
        raise EngineContractError(f'{name} items must be unique')
    return result


def _positive_integer(value, name, *, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, int) or (
            value < (0 if allow_zero else 1)):
        raise EngineContractError(f'{name} must be a positive integer')
    return value


def _identity(document, profile, schema):
    if not isinstance(document, Mapping):
        raise EngineContractError('engine contract must be an object')
    value = copy.deepcopy(dict(document))
    if value.get('schema') != schema:
        raise EngineContractError(f'engine contract schema must be {schema}')
    expected = {
        'provider_id': profile.provider_id,
        'profile_id': profile.profile_id,
        'engine_id': profile.engine_id,
        'reference_version': profile.exact_version,
    }
    for name, expected_value in expected.items():
        if value.get(name) != expected_value:
            raise EngineContractError(
                f'{name} does not match the active provider profile'
            )
    _required(value.get('contract_id'), 'contract_id')
    _required(value.get('interface_id'), 'interface_id')
    return value


def _evidence(value, name):
    if not isinstance(value, Mapping):
        raise EngineContractError(f'{name} must be an object')
    authority = _required(value.get('authority'), f'{name}.authority')
    artifact = _required(value.get('artifact'), f'{name}.artifact')
    digest = _required(value.get('sha256'), f'{name}.sha256')
    if len(digest) != 64 or any(
            character not in '0123456789abcdef' for character in digest):
        raise EngineContractError(f'{name}.sha256 must be lowercase SHA-256')
    _required(value.get('format'), f'{name}.format')
    _required(value.get('license_id'), f'{name}.license_id')
    return {
        **copy.deepcopy(dict(value)),
        'authority': authority,
        'artifact': artifact,
        'sha256': digest,
    }


def validate_dialect_contract(document, profile, expected_task_ids=None):
    """Validate a complete dialect package without filling defaults."""
    value = _identity(document, profile, 'cdeadmin.engine-dialect.v2')
    languages = _string_list(
        value.get('language_profiles'), 'language_profiles'
    )
    if profile.language_profile not in languages:
        raise EngineContractError(
            'active language profile is absent from the dialect contract'
        )
    value['grammar_evidence'] = _evidence(
        value.get('grammar_evidence'), 'grammar_evidence'
    )
    proof_records = value.get('proof_records')
    if not isinstance(proof_records, list) or not proof_records:
        raise EngineContractError('proof_records must be a non-empty array')
    proof_ids = set()
    proof_kinds = {}
    admitted_proof_kinds = {
        'grammar', 'catalog', 'documentation', 'parser_acceptance',
        'live_execution', 'driver',
    }
    for position, proof in enumerate(proof_records):
        checked = _evidence(proof, f'proof_records item {position}')
        proof_id = _required(
            checked.get('evidence_id'),
            f'proof_records item {position}.evidence_id',
        )
        if proof_id in proof_ids:
            raise EngineContractError('proof record IDs must be unique')
        proof_kind = _required(
            checked.get('evidence_kind'), f'{proof_id}.evidence_kind'
        )
        if proof_kind not in admitted_proof_kinds:
            raise EngineContractError(f'{proof_id}.evidence_kind is invalid')
        proof_ids.add(proof_id)
        proof_kinds[proof_id] = proof_kind
    inventories = value.get('inventories')
    if not isinstance(inventories, Mapping):
        raise EngineContractError('inventories must be an object')
    inventory_counts = {}
    for name in (
        'lexical_rules', 'statements', 'commands', 'data_types',
        'functions', 'operators', 'session_settings', 'diagnostics',
    ):
        records = inventories.get(name)
        if not isinstance(records, list):
            raise EngineContractError(f'inventories.{name} must be an array')
        seen_inventory_ids = set()
        for position, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise EngineContractError(
                    f'inventories.{name} item {position} must be an object'
                )
            item_id = _required(
                record.get('item_id'), f'inventories.{name}.item_id'
            )
            if item_id in seen_inventory_ids:
                raise EngineContractError(
                    f'inventories.{name} item IDs must be unique'
                )
            seen_inventory_ids.add(item_id)
            _required(
                record.get('native_name'), f'{name}.{item_id}.native_name'
            )
            _required(record.get('source'), f'{name}.{item_id}.source')
            item_proofs = _string_list(
                record.get('proof_ids'), f'{name}.{item_id}.proof_ids'
            )
            if not set(item_proofs).issubset(proof_ids):
                raise EngineContractError(
                    f'{name}.{item_id} refers to unknown proof evidence'
                )
        inventory_counts[name] = len(records)
    decisions = value.get('syntax_decisions')
    if not isinstance(decisions, Mapping):
        raise EngineContractError('syntax_decisions must be an object')
    for name in (
        'identifier_quoting', 'string_literals', 'parameter_binding',
        'transaction_control', 'pagination', 'explain', 'cancellation',
    ):
        if name not in decisions:
            raise EngineContractError(
                f'syntax_decisions.{name} must be explicitly declared'
            )
        decision = decisions[name]
        if not isinstance(decision, Mapping):
            raise EngineContractError(
                f'syntax_decisions.{name} must be an object'
            )
        decision_proofs = _string_list(
            decision.get('proof_ids'),
            f'syntax_decisions.{name}.proof_ids',
        )
        if not set(decision_proofs).issubset(proof_ids):
            raise EngineContractError(
                f'syntax_decisions.{name} refers to unknown proof evidence'
            )
    templates = value.get('task_templates')
    if not isinstance(templates, list) or not templates:
        raise EngineContractError('task_templates must be a non-empty array')
    seen = set()
    for position, template in enumerate(templates):
        if not isinstance(template, Mapping):
            raise EngineContractError(
                f'task_templates item {position} must be an object'
            )
        task_id = _required(
            template.get('task_id'), 'task_templates.task_id'
        )
        if task_id in seen:
            raise EngineContractError('task template IDs must be unique')
        seen.add(task_id)
        _required(template.get('source'), f'{task_id}.source')
        template_proofs = _string_list(
            template.get('proof_ids'), f'{task_id}.proof_ids'
        )
        if not set(template_proofs).issubset(proof_ids):
            raise EngineContractError(
                f'{task_id} refers to unknown proof evidence'
            )
        template_proof_kinds = {
            proof_kinds[proof_id] for proof_id in template_proofs
        }
        if not {'parser_acceptance', 'live_execution'}.issubset(
                template_proof_kinds):
            raise EngineContractError(
                f'{task_id} requires parser and live execution evidence'
            )
        _string_list(
            template.get('required_bindings', []),
            f'{task_id}.required_bindings', allow_empty=True,
        )
    coverage = value.get('coverage')
    if not isinstance(coverage, Mapping):
        raise EngineContractError('coverage must be an object')
    if coverage.get('scope') != 'cdeadmin_generated_tasks':
        raise EngineContractError(
            'coverage.scope must be cdeadmin_generated_tasks'
        )
    if coverage.get('language_acceptance_authority') != 'engine_parser':
        raise EngineContractError(
            'coverage.language_acceptance_authority must be engine_parser'
        )
    authoritative_inventory_counts = coverage.get(
        'authoritative_inventory_counts'
    )
    if not isinstance(authoritative_inventory_counts, Mapping) or dict(
            authoritative_inventory_counts) != inventory_counts:
        raise EngineContractError(
            'dialect inventory coverage is not complete and exact'
        )
    authoritative_ids = _string_list(
        coverage.get('authoritative_task_ids'),
        'coverage.authoritative_task_ids',
    )
    authoritative = _positive_integer(
        coverage.get('authoritative_task_count'),
        'coverage.authoritative_task_count',
    )
    implemented = _positive_integer(
        coverage.get('implemented_task_count'),
        'coverage.implemented_task_count',
    )
    if (
            authoritative != implemented or
            implemented != len(templates) or
            set(authoritative_ids) != seen or
            len(authoritative_ids) != len(seen)
    ):
        raise EngineContractError(
            'dialect task coverage is not complete and exact'
        )
    if expected_task_ids is not None and set(authoritative_ids) != set(
            expected_task_ids):
        raise EngineContractError(
            'dialect contract does not cover the executable provider tasks'
        )
    live_evidence_ids = _string_list(
        value.get('live_evidence_ids'), 'live_evidence_ids'
    )
    if not set(live_evidence_ids).issubset(proof_ids) or any(
            proof_kinds[item] != 'live_execution'
            for item in live_evidence_ids):
        raise EngineContractError(
            'live_evidence_ids must refer only to live execution proofs'
        )
    return value


def validate_metrics_contract(document, profile):
    """Validate a complete exact-version native metrics package."""
    value = _identity(document, profile, 'cdeadmin.engine-metrics.v2')
    value['catalog_evidence'] = _evidence(
        value.get('catalog_evidence'), 'catalog_evidence'
    )
    classification_evidence = value.get('classification_evidence')
    if not isinstance(classification_evidence, list) or not (
            classification_evidence):
        raise EngineContractError(
            'classification_evidence must be a non-empty array'
        )
    evidence_ids = set()
    for position, item in enumerate(classification_evidence):
        checked = _evidence(
            item, f'classification_evidence item {position}'
        )
        evidence_id = _required(
            checked.get('evidence_id'),
            f'classification_evidence item {position}.evidence_id',
        )
        if evidence_id in evidence_ids:
            raise EngineContractError(
                'classification evidence IDs must be unique'
            )
        evidence_ids.add(evidence_id)

    observations = value.get('native_observations')
    if not isinstance(observations, list) or not observations:
        raise EngineContractError(
            'native_observations must be a non-empty array'
        )
    observation_required = {
        'observation_id', 'native_name', 'scope', 'source', 'value_type',
        'observation_class', 'description', 'privilege',
        'version_condition', 'collection_cost', 'poll_interval_seconds',
        'cardinality', 'redaction', 'evidence_ids',
    }
    observation_ids = set()
    for position, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise EngineContractError(
                f'native_observations item {position} must be an object'
            )
        missing = sorted(observation_required.difference(observation))
        if missing:
            raise EngineContractError(
                'native observation record is missing: ' + ', '.join(missing)
            )
        observation_id = _required(
            observation['observation_id'], 'observation_id'
        )
        if observation_id in observation_ids:
            raise EngineContractError(
                'native observation IDs must be unique'
            )
        observation_ids.add(observation_id)
        for name in observation_required - {
                'observation_id', 'poll_interval_seconds', 'evidence_ids'}:
            _required(observation[name], f'{observation_id}.{name}')
        _positive_integer(
            observation['poll_interval_seconds'],
            f'{observation_id}.poll_interval_seconds',
        )
        record_evidence = _string_list(
            observation['evidence_ids'], f'{observation_id}.evidence_ids'
        )
        if not set(record_evidence).issubset(evidence_ids):
            raise EngineContractError(
                f'{observation_id} refers to unknown classification evidence'
            )

    metrics = value.get('metrics')
    if not isinstance(metrics, list) or not metrics:
        raise EngineContractError('metrics must be a non-empty array')
    required = {
        'metric_id', 'observation_id', 'unit', 'kind', 'reset_behavior',
        'aggregation', 'evidence_ids',
    }
    metric_ids = set()
    metric_observation_ids = set()
    for position, metric in enumerate(metrics):
        if not isinstance(metric, Mapping):
            raise EngineContractError(
                f'metrics item {position} must be an object'
            )
        missing = sorted(required.difference(metric))
        if missing:
            raise EngineContractError(
                'metric record is missing: ' + ', '.join(missing)
            )
        metric_id = _required(metric['metric_id'], 'metric_id')
        if metric_id in metric_ids:
            raise EngineContractError('metric IDs must be unique')
        metric_ids.add(metric_id)
        observation_id = _required(
            metric['observation_id'], f'{metric_id}.observation_id'
        )
        if observation_id not in observation_ids:
            raise EngineContractError(
                f'{metric_id} refers to an unknown native observation'
            )
        if observation_id in metric_observation_ids:
            raise EngineContractError(
                'a native observation may define only one metric'
            )
        metric_observation_ids.add(observation_id)
        for name in required - {
                'metric_id', 'observation_id', 'evidence_ids'}:
            _required(metric[name], f'{metric_id}.{name}')
        metric_evidence = _string_list(
            metric['evidence_ids'], f'{metric_id}.evidence_ids'
        )
        if not set(metric_evidence).issubset(evidence_ids):
            raise EngineContractError(
                f'{metric_id} refers to unknown classification evidence'
            )
    authoritative_observations = _positive_integer(
        value.get('authoritative_observation_count'),
        'authoritative_observation_count',
    )
    authoritative_observation_ids = _string_list(
        value.get('authoritative_observation_ids'),
        'authoritative_observation_ids',
    )
    if (
            authoritative_observations != len(observations) or
            authoritative_observations != len(
                authoritative_observation_ids) or
            set(authoritative_observation_ids) != observation_ids
    ):
        raise EngineContractError(
            'native observation coverage is not complete'
        )
    authoritative = _positive_integer(
        value.get('authoritative_metric_count'),
        'authoritative_metric_count',
    )
    authoritative_ids = _string_list(
        value.get('authoritative_metric_ids'),
        'authoritative_metric_ids',
    )
    if (
            authoritative != len(metrics) or
            authoritative != len(authoritative_ids) or
            set(authoritative_ids) != metric_ids
    ):
        raise EngineContractError(
            'operational metric coverage is not complete'
        )
    _string_list(value.get('live_evidence_ids'), 'live_evidence_ids')
    return value


def _load_provider_document(provider_module, filename):
    package = provider_module.rsplit('.', 1)[0]
    artifact = resources.files(package).joinpath(filename)
    source = artifact.read_bytes()
    return json.loads(source), hashlib.sha256(source).hexdigest()


def contract_descriptor(profile, provider_module, expected_task_ids=None):
    """Return a fail-closed activation record for both exact contracts."""
    cache_key = (
        provider_module,
        profile.provider_id,
        profile.profile_id,
        profile.engine_id,
        profile.exact_version,
        profile.language_profile,
        profile.dialect_contract_file,
        profile.metrics_contract_file,
        tuple(sorted(expected_task_ids or ())),
    )
    with _DESCRIPTOR_CACHE_LOCK:
        cached = _DESCRIPTOR_CACHE.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)
    results: dict[str, Any] = {}
    specifications = (
        ('dialect', profile.dialect_contract_file,
         validate_dialect_contract),
        ('metrics', profile.metrics_contract_file,
         validate_metrics_contract),
    )
    for kind, filename, validator in specifications:
        if filename is None:
            results[kind] = {
                'state': 'blocked',
                'reason': f'provider_exact_{kind}_contract_absent',
            }
            continue
        try:
            document, digest = _load_provider_document(
                provider_module, filename
            )
            checked = (
                validator(document, profile, expected_task_ids)
                if kind == 'dialect' else validator(document, profile)
            )
            results[kind] = {
                'state': 'passed',
                'contract_id': checked['contract_id'],
                'artifact': filename,
                'sha256': digest,
            }
        except (EngineContractError, OSError, ValueError) as exc:
            results[kind] = {
                'state': 'blocked',
                'reason': str(exc),
            }
    descriptor = {
        'schema': 'cdeadmin.engine-contract-activation.v1',
        'engine_id': profile.engine_id,
        'profile_id': profile.profile_id,
        'reference_version': profile.exact_version,
        'state': (
            'passed' if all(item['state'] == 'passed'
                            for item in results.values()) else 'blocked'
        ),
        **results,
    }
    with _DESCRIPTOR_CACHE_LOCK:
        _DESCRIPTOR_CACHE[cache_key] = copy.deepcopy(descriptor)
    return descriptor
