##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Fail-closed parity ledger and differential-runner foundations."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol


SHA256 = re.compile(r'^[a-f0-9]{64}$')
DEFAULT_LEDGER = Path(
    'tools/tests/fixtures/cdeadmin_parity/parity_ledger.json'
)
DEFAULT_CATALOG = Path(
    'tools/tests/fixtures/cdeadmin_parity/scenario_catalog.json'
)


class ParityError(RuntimeError):
    """Raised when execution would weaken parity evidence."""


class ActualEngineExecutor(Protocol):
    """Narrow executor boundary; provider transaction state stays opaque."""

    def runtime_identity(self) -> Mapping[str, str]:
        """Return exact engine, profile, and runtime artifact identity."""

    def execute_scenario(self, scenario: Mapping[str, Any]) -> Mapping:
        """Return one typed provider-native observation."""


@dataclass(frozen=True)
class Comparison:
    """Typed semantic comparison result."""

    outcome: str
    detail: str


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ParityError(f'cannot load {path}: {exc}') from exc
    if not isinstance(value, dict):
        raise ParityError(f'{path} must contain a JSON object')
    return value


def canonical_digest(value: Any) -> str:
    """Return a stable digest for a JSON-compatible value."""
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (AttributeError, ValueError) as exc:
        raise ParityError(f'invalid UTC timestamp: {value!r}') from exc
    if parsed.tzinfo is None:
        raise ParityError(f'timestamp has no timezone: {value!r}')
    return parsed.astimezone(timezone.utc)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _canonical_sort(values: list) -> list:
    return sorted(values, key=lambda item: json.dumps(
        item, sort_keys=True, separators=(',', ':'),
    ))


def _delta_matches(reference: Any, candidate: Any, delta: Mapping) -> bool:
    delta_class = delta.get('class')
    bound = delta.get('bound')
    if delta_class in ('numeric_tolerance', 'score_tolerance'):
        return (
            _is_number(reference) and _is_number(candidate) and
            _is_number(bound) and math.isfinite(float(bound)) and
            float(bound) >= 0 and
            abs(float(reference) - float(candidate)) <= float(bound)
        )
    if delta_class == 'timestamp_precision':
        if not _is_number(bound) or float(bound) < 0:
            return False
        try:
            distance = abs((_parse_time(reference) -
                            _parse_time(candidate)).total_seconds())
        except ParityError:
            return False
        return distance <= float(bound)
    if delta_class == 'unordered_collection':
        return (
            isinstance(reference, list) and isinstance(candidate, list) and
            _canonical_sort(reference) == _canonical_sort(candidate)
        )
    if delta_class == 'collation_order':
        if bound != 'casefold':
            return False
        return (
            isinstance(reference, list) and isinstance(candidate, list) and
            sorted(reference, key=lambda item: str(item).casefold()) ==
            sorted(candidate, key=lambda item: str(item).casefold())
        )
    return False


def compare_observations(reference: Mapping, candidate: Mapping,
                         delta: Mapping | None = None) -> Comparison:
    """Compare typed observations without coercing model or scalar types."""
    if (not isinstance(reference, Mapping) or
            not isinstance(candidate, Mapping)):
        return Comparison('mismatch', 'observations must be objects')
    if set(reference) != {'kind', 'value'} or set(candidate) != {
            'kind', 'value'}:
        return Comparison('mismatch', 'observation shape is not typed')
    if reference['kind'] != candidate['kind']:
        return Comparison('mismatch', 'observation kinds differ')
    if type(reference['value']) is type(candidate['value']) and (
            reference['value'] == candidate['value']):
        return Comparison('equivalent', 'typed values are identical')
    if delta and _delta_matches(reference['value'], candidate['value'],
                                delta):
        return Comparison('acceptable_delta', delta.get('detail', ''))
    return Comparison('mismatch', 'typed values differ outside policy')


def validate_delta(delta: Mapping | None, policy: Mapping) -> list[str]:
    """Validate an explicit semantic-delta claim against policy."""
    if not isinstance(delta, Mapping):
        return ['semantic delta must be an object']
    classes = policy.get('delta_classes', {})
    delta_class = delta.get('class')
    if delta_class not in classes:
        return [f'unapproved semantic delta class: {delta_class!r}']
    rule = classes[delta_class]
    errors = []
    if rule.get('requires_bound') and delta.get('bound') in (None, ''):
        errors.append(f'{delta_class} requires an explicit bound')
    if not rule.get('counts_as_parity'):
        errors.append(f'{delta_class} cannot count as implemented parity')
    if not isinstance(delta.get('detail'), str) or not delta.get('detail'):
        errors.append('semantic delta requires a non-empty detail')
    return errors


def make_evidence(record: Mapping) -> dict:
    """Add the canonical content digest to an evidence record."""
    value = dict(record)
    value.pop('evidence_digest', None)
    value['evidence_digest'] = canonical_digest(value)
    return value


def validate_evidence(evidence: Mapping, profile: Mapping,
                      scenario: Mapping, policy: Mapping,
                      now: datetime) -> list[str]:
    """Validate evidence identity, recency, digests, and MGA assertions."""
    errors = []
    required = {
        'evidence_id', 'engine_id', 'reference_profile', 'scenario_id',
        'side', 'observed_at', 'expires_at', 'digests', 'evidence_digest',
    }
    missing = required - set(evidence)
    if missing:
        return [f'evidence missing fields: {sorted(missing)}']
    if evidence['engine_id'] != profile['engine_id']:
        errors.append('evidence engine does not match profile')
    if evidence['reference_profile'] != profile['reference_profile']:
        errors.append('evidence reference profile is wrong version')
    if evidence['scenario_id'] != scenario['scenario_id']:
        errors.append('evidence scenario does not match ledger cell')
    try:
        observed = _parse_time(evidence['observed_at'])
        expires = _parse_time(evidence['expires_at'])
        maximum_age = timedelta(
            days=policy['evidence']['maximum_age_days']
        )
        if observed > now:
            errors.append('evidence observation is in the future')
        if now - observed > maximum_age or expires <= now:
            errors.append('evidence is stale or expired')
        if expires <= observed:
            errors.append('evidence expiry does not follow observation')
    except (KeyError, ParityError) as exc:
        errors.append(str(exc))
    digests = evidence.get('digests')
    if not isinstance(digests, Mapping):
        errors.append('evidence digests must be an object')
    else:
        for field in policy['evidence']['required_digest_fields']:
            if not SHA256.fullmatch(str(digests.get(field, ''))):
                errors.append(f'evidence digest {field} is missing or invalid')
    identity = evidence.get('runtime_identity')
    if not isinstance(identity, Mapping):
        errors.append('evidence has no exact runtime identity')
    else:
        if identity.get('engine_id') != profile['engine_id']:
            errors.append('runtime identity engine is wrong')
        if identity.get('reference_profile') != profile[
                'reference_profile']:
            errors.append('runtime identity profile is wrong version')
        if not SHA256.fullmatch(str(identity.get(
                'runtime_artifact_digest', ''))):
            errors.append('runtime artifact digest is missing or invalid')
    if (evidence.get('side') == 'scratchbird_emulation' and
            scenario.get('transaction_observation')):
        required_authority = policy['scratchbird_transaction_authority']
        actual = evidence.get('scratchbird_authority', {})
        for field in ('authority_class', 'execution_boundary',
                      'finality_source'):
            if actual.get(field) != required_authority[field]:
                errors.append(
                    f'ScratchBird transaction authority {field} is invalid'
                )
    digest_value = dict(evidence)
    claimed = digest_value.pop('evidence_digest', '')
    if not SHA256.fullmatch(str(claimed)):
        errors.append('evidence content digest is invalid')
    elif canonical_digest(digest_value) != claimed:
        errors.append('evidence content digest does not match record')
    return errors


def execute_reference_scenario(executor: ActualEngineExecutor,
                               executor_record: Mapping,
                               profile: Mapping,
                               scenario: Mapping) -> Mapping:
    """Execute only after exact runtime admission and identity agreement."""
    if executor_record.get('state') != 'ready_exact_runtime_admitted':
        raise ParityError('exact reference runtime is not admitted')
    identity = executor.runtime_identity()
    expected = (profile['engine_id'], profile['reference_profile'])
    actual = (identity.get('engine_id'), identity.get('reference_profile'))
    if actual != expected:
        raise ParityError(
            f'wrong reference runtime version: {actual!r} != {expected!r}'
        )
    if not SHA256.fullmatch(str(identity.get(
            'runtime_artifact_digest', ''))):
        raise ParityError('runtime identity is not content addressed')
    observation = executor.execute_scenario(scenario)
    if not isinstance(observation, Mapping):
        raise ParityError('executor did not return a typed observation')
    return observation


def execute_scratchbird_scenario(*_args, **_kwargs):
    """Keep emulation disabled until the explicit upstream handoff."""
    raise ParityError('ScratchBird emulation execution is disabled pending '
                      'handoff')


def _validate_inputs(source: Path, policy: Mapping, corpus: Mapping,
                     executors: Mapping, catalog: Mapping,
                     ledger: Mapping) -> tuple[list[str], dict, dict]:
    errors = []
    if policy.get('schema') != 'cdeadmin.parity-policy.v1':
        errors.append('parity policy schema is invalid')
    if policy.get('active_corpus_authority') != (
            'exact_release_packet_root_supplied_by_operator'):
        errors.append('active corpus authority is invalid')
    if policy.get('scratchbird_emulation_execution') != (
            'disabled_pending_handoff'):
        errors.append('ScratchBird execution must remain disabled')
    authority = policy.get('scratchbird_transaction_authority', {})
    required_authority = {
        'authority_class': 'scratchbird_mga',
        'execution_boundary': 'engine_sblr_uuid',
        'finality_source': 'durable_transaction_inventory',
        'common_runner_interprets_finality': False,
        'parser_or_driver_authority': False,
        'savepoints_allocate_independent_authority': False,
    }
    if any(authority.get(key) != value
           for key, value in required_authority.items()):
        errors.append('ScratchBird MGA authority policy is invalid')
    if corpus.get('schema') != 'cdeadmin.reference-corpus.v1':
        errors.append('reference corpus schema is invalid')
    profiles = corpus.get('profiles', [])
    profile_map = {row.get('engine_id'): row for row in profiles}
    expected_profiles = {
        (row.get('engine_id'), row.get('reference_profile'))
        for row in profiles
    }
    if len(profile_map) != 25 or len(expected_profiles) != 25:
        errors.append('reference corpus must contain 25 unique profiles')
    executor_rows = executors.get('executors', [])
    executor_map = {row.get('engine_id'): row for row in executor_rows}
    executor_profiles = {
        (row.get('engine_id'), row.get('reference_profile'))
        for row in executor_rows
    }
    if executor_profiles != expected_profiles:
        errors.append('executor exact profiles differ from reference corpus')
    contract = executors.get('executor_contract', {})
    if contract.get('transaction_authority') != 'provider_native_opaque':
        errors.append('executor transaction authority must remain opaque')
    if contract.get('common_finality_interpretation') is not False:
        errors.append('common runner cannot interpret provider finality')
    if catalog.get('schema') != 'cdeadmin.scenario-catalog.v1':
        errors.append('scenario catalog schema is invalid')
    scenarios = catalog.get('scenarios', [])
    scenario_map = {row.get('scenario_id'): row for row in scenarios}
    if len(scenario_map) != len(scenarios) or not scenarios:
        errors.append('scenario identifiers must be unique and non-empty')
    fixtures = {
        fixture for row in profiles for fixture in row.get('fixture_sets', [])
    }
    for scenario in scenarios:
        scenario_id = scenario.get('scenario_id', '<missing>')
        if scenario.get('fixture_set') not in fixtures:
            errors.append(f'{scenario_id}: fixture set is unknown')
        applies = scenario.get('applies_to', [])
        if not applies or len(applies) != len(set(applies)):
            errors.append(f'{scenario_id}: applies_to is empty or duplicated')
        for engine in applies:
            profile = profile_map.get(engine)
            if profile is None:
                errors.append(f'{scenario_id}: unknown engine {engine}')
            elif scenario.get('fixture_set') not in profile.get(
                    'fixture_sets', []):
                errors.append(
                    f'{scenario_id}: {engine} lacks required fixture set'
                )
        for operation in scenario.get('operations', []):
            if set(operation) != {'operation_key', 'arguments'}:
                errors.append(f'{scenario_id}: operation shape is invalid')
        transaction = scenario.get('transaction_observation')
        if transaction and transaction != {
                'reference_authority': 'provider_native_opaque',
                'scratchbird_authority': 'scratchbird_engine_mga',
                'common_runner_interprets_finality': False}:
            errors.append(f'{scenario_id}: transaction boundary is invalid')
    if ledger.get('schema') != 'cdeadmin.parity-ledger.v1':
        errors.append('parity ledger schema is invalid')
    if ledger.get('defaults') != {
            'reference_outcome': 'not_run',
            'scratchbird_emulation_outcome': 'not_run'}:
        errors.append('ledger defaults must truthfully remain not_run')
    ledger_profiles = {
        (row.get('engine_id'), row.get('reference_profile'))
        for row in ledger.get('profile_scopes', [])
    }
    if ledger_profiles != expected_profiles:
        errors.append('ledger exact profiles differ from reference corpus')
    try:
        ledger_catalog = (source / ledger['scenario_catalog']).resolve()
        if ledger_catalog != (source / DEFAULT_CATALOG).resolve():
            errors.append('ledger scenario catalog path is not canonical')
    except (KeyError, OSError):
        errors.append('ledger scenario catalog path is invalid')
    return errors, profile_map, scenario_map


def _validate_results(ledger: Mapping, profile_map: Mapping,
                      scenario_map: Mapping, policy: Mapping,
                      now: datetime) -> list[str]:
    errors = []
    evidence_rows = ledger.get('evidence', [])
    evidence_map = {row.get('evidence_id'): row for row in evidence_rows
                    if isinstance(row, Mapping)}
    if len(evidence_map) != len(evidence_rows):
        errors.append('evidence identifiers must be unique')
    seen = set()
    reference_outcomes = set(policy['outcomes']['reference'])
    scratchbird_outcomes = set(policy['outcomes']['scratchbird_emulation'])
    for result in ledger.get('results', []):
        key = (result.get('engine_id'), result.get('scenario_id'))
        if key in seen:
            errors.append(f'duplicate ledger result: {key!r}')
            continue
        seen.add(key)
        profile = profile_map.get(result.get('engine_id'))
        scenario = scenario_map.get(result.get('scenario_id'))
        if not profile or not scenario:
            errors.append(f'ledger result is outside scope: {key!r}')
            continue
        if result.get('engine_id') not in scenario['applies_to']:
            errors.append(f'ledger result is not applicable: {key!r}')
        if result.get('reference_profile') != profile['reference_profile']:
            errors.append(f'ledger result has wrong version: {key!r}')
        sides = (
            ('reference', reference_outcomes),
            ('scratchbird_emulation', scratchbird_outcomes),
        )
        for side_name, allowed in sides:
            side = result.get(side_name)
            if (not isinstance(side, Mapping) or
                    side.get('outcome') not in allowed):
                errors.append(f'{key!r}: invalid {side_name} outcome')
                continue
            outcome = side['outcome']
            evidence_id = side.get('evidence_id')
            if outcome != 'not_run' and not evidence_id:
                errors.append(f'{key!r}: {side_name} lacks evidence')
            if evidence_id:
                evidence = evidence_map.get(evidence_id)
                if not evidence:
                    errors.append(f'{key!r}: evidence {evidence_id} missing')
                else:
                    errors.extend(
                        f'{key!r}: {error}' for error in validate_evidence(
                            evidence, profile, scenario, policy, now
                        )
                    )
                    if evidence.get('side') != side_name:
                        errors.append(f'{key!r}: evidence side is wrong')
        reference = result.get('reference', {})
        expected = scenario['expected_reference']['disposition']
        if (reference.get('outcome') == 'expected_refusal' and
                expected != 'expected_refusal'):
            errors.append(f'{key!r}: refusal was not expected')
        scratchbird = result.get('scratchbird_emulation', {})
        if scratchbird.get('outcome') == 'acceptable_delta':
            errors.extend(
                f'{key!r}: {error}' for error in validate_delta(
                    result.get('semantic_delta'), policy
                )
            )
        elif result.get('semantic_delta') is not None:
            errors.append(f'{key!r}: delta exists without acceptable outcome')
    return errors


def build_dashboard(ledger: Mapping, profile_map: Mapping,
                    scenario_map: Mapping, policy: Mapping) -> dict:
    """Account for all applicable cells without inflating parity."""
    result_map = {
        (row['engine_id'], row['scenario_id']): row
        for row in ledger.get('results', [])
        if row.get('engine_id') in profile_map and
        row.get('scenario_id') in scenario_map
    }
    reference_counts = {name: 0 for name in policy['outcomes']['reference']}
    scratchbird_counts = {
        name: 0 for name in policy['outcomes']['scratchbird_emulation']
    }
    expected_cells = 0
    implemented = 0
    implemented_outcomes = set(policy['implemented_parity_outcomes'])
    for scenario in scenario_map.values():
        for engine in scenario['applies_to']:
            expected_cells += 1
            result = result_map.get((engine, scenario['scenario_id']))
            reference = 'not_run'
            scratchbird = 'not_run'
            if result:
                reference = result['reference']['outcome']
                scratchbird = result['scratchbird_emulation']['outcome']
            reference_counts[reference] += 1
            scratchbird_counts[scratchbird] += 1
            if scratchbird in implemented_outcomes:
                implemented += 1
    reference_complete = (
        reference_counts['observed'] + reference_counts['expected_refusal']
    )
    return {
        'expected_cells': expected_cells,
        'reference': reference_counts,
        'reference_complete_cells': reference_complete,
        'scratchbird_emulation': scratchbird_counts,
        'implemented_parity_cells': implemented,
        'implemented_parity_percent': (
            round(100 * implemented / expected_cells, 2)
            if expected_cells else 0.0
        ),
        'refusals_count_as_implemented_parity': False,
    }


def evaluate(source: Path, ledger_path: Path | None = None,
             now: datetime | None = None) -> dict:
    """Run structural accounting and report release readiness separately."""
    source = source.resolve()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    paths = {
        'policy': source / 'tools/cdeadmin_parity_policy.json',
        'corpus': source / 'tools/cdeadmin_reference_corpus.json',
        'executors': source / 'tools/cdeadmin_actual_engine_executors.json',
        'catalog': source / DEFAULT_CATALOG,
        'ledger': (ledger_path or source / DEFAULT_LEDGER),
    }
    try:
        loaded = {name: _load(path) for name, path in paths.items()}
    except ParityError as exc:
        return {
            'valid': False, 'release_ready': False,
            'errors': [str(exc)], 'release_blockers': ['inputs invalid'],
            'dashboard': {},
        }
    errors, profile_map, scenario_map = _validate_inputs(
        source, loaded['policy'], loaded['corpus'], loaded['executors'],
        loaded['catalog'], loaded['ledger'],
    )
    errors.extend(_validate_results(
        loaded['ledger'], profile_map, scenario_map, loaded['policy'], now,
    ))
    dashboard = build_dashboard(
        loaded['ledger'], profile_map, scenario_map, loaded['policy'],
    )
    blockers = []
    if dashboard['reference_complete_cells'] != dashboard['expected_cells']:
        blockers.append(
            f"reference observations incomplete: "
            f"{dashboard['reference_complete_cells']}/"
            f"{dashboard['expected_cells']}"
        )
    scratchbird_assessed = (
        dashboard['expected_cells'] -
        dashboard['scratchbird_emulation']['not_run']
    )
    if scratchbird_assessed != dashboard['expected_cells']:
        blockers.append(
            f'ScratchBird emulation outcomes not assessed: '
            f'{scratchbird_assessed}/{dashboard["expected_cells"]}'
        )
    blocked_executors = sum(
        row.get('state') != 'ready_exact_runtime_admitted'
        for row in loaded['executors'].get('executors', [])
    )
    if blocked_executors:
        blockers.append(
            f'exact reference runtimes not admitted: {blocked_executors}/25'
        )
    if errors:
        blockers.append('structural or evidence validation failed')
    return {
        'schema': 'cdeadmin.parity-evaluation.v1',
        'valid': not errors,
        'release_ready': not errors and not blockers,
        'errors': errors,
        'release_blockers': blockers,
        'profile_count': len(profile_map),
        'scenario_count': len(scenario_map),
        'dashboard': dashboard,
        'digests': {
            name: canonical_digest(value) for name, value in loaded.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('.'))
    parser.add_argument('--ledger', type=Path)
    parser.add_argument('--at', help='UTC evaluation timestamp')
    parser.add_argument('--require-reference-complete', action='store_true')
    parser.add_argument('--require-release', action='store_true')
    args = parser.parse_args()
    try:
        now = _parse_time(args.at) if args.at else None
    except ParityError as exc:
        print(json.dumps({'valid': False, 'errors': [str(exc)]}, indent=2))
        return 2
    ledger = args.ledger
    if ledger and not ledger.is_absolute():
        ledger = (args.source / ledger).resolve()
    result = evaluate(args.source, ledger, now)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result['valid']:
        return 1
    if (args.require_reference_complete and
            result['dashboard']['reference_complete_cells'] !=
            result['dashboard']['expected_cells']):
        return 1
    if args.require_release and not result['release_ready']:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
