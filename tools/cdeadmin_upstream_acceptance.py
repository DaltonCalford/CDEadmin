##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run fail-closed CDEadmin acceptance suites against supplied candidates."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol


CATALOG = Path('tools/cdeadmin_upstream_acceptance_suites.json')
REFERENCE_CORPUS = Path('tools/cdeadmin_reference_corpus.json')
SCENARIO_CATALOG = Path(
    'tools/tests/fixtures/cdeadmin_parity/scenario_catalog.json'
)
SHA256 = re.compile(r'^[a-f0-9]{64}$')
EXPECTED_SUITES = {
    'corrected_driver_package_api_provenance',
    'direct_and_manager_proxy_routes',
    'navigator_authorization_uuid_generation',
    'typed_results_cursors_backpressure',
    'mga_transaction_boundaries_and_finality',
    'operation_lifecycle_receipt_topology_scope',
    'exact_donor_listener_differential',
}
EXPECTED_AUTHORITIES = {
    'engine_execution': 'sblr_and_internal_procedures_only',
    'object_identity': 'uuid_backed',
    'transaction_authority': 'scratchbird_mga',
    'finality_source': 'durable_transaction_inventory',
    'driver_finality_authority': False,
    'donor_or_embedded_substitute': False,
    'unknown_finality_remains_unknown': True,
    'automatic_replay': False,
}
DEPENDENCIES = {'D2_DRIVER', 'D3_CDE', 'D5_EMULATION'}


class AcceptanceError(RuntimeError):
    """Acceptance definitions or candidate evidence are unsafe."""


class CandidateAdapter(Protocol):
    """External adapter supplied with an upstream candidate."""

    def candidate_manifest(self) -> Mapping[str, Any]:
        ...

    def execute_case(
        self, case: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f'cannot load {path}: {exc}') from exc
    if not isinstance(value, dict):
        raise AcceptanceError(f'{path} must contain a JSON object')
    return value


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def make_evidence(value: Mapping[str, Any]) -> dict:
    result = copy.deepcopy(dict(value))
    result.pop('evidence_digest', None)
    result['evidence_digest'] = canonical_digest(result)
    return result


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise AcceptanceError('evidence timestamp must be text')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise AcceptanceError(f'invalid evidence timestamp {value!r}') \
            from exc
    if parsed.tzinfo is None:
        raise AcceptanceError('evidence timestamp must have a timezone')
    return parsed.astimezone(timezone.utc)


def _case(case: Mapping, suite: Mapping, contract: Mapping) -> dict:
    value = copy.deepcopy(dict(case))
    value.update({
        'suite_id': suite['suite_id'],
        'owner_contract': suite['owner_contract'],
        'owner_search_key': contract['search_key'],
        'owner_dependency': contract['dependency'],
        'transaction_sensitive': bool(
            suite.get('transaction_sensitive', False)
        ),
        'promotes': copy.deepcopy(suite.get('promotes', [])),
    })
    return value


def expand_cases(catalog: Mapping, reference: Mapping,
                 scenarios: Mapping) -> list[dict]:
    """Expand six fixed suites and the exact 25-profile parity matrix."""
    contracts = catalog['contracts']
    cases = []
    for suite in catalog['suites']:
        contract = contracts[suite['owner_contract']]
        cases.extend(
            _case(item, suite, contract) for item in suite['cases']
        )
    suite = catalog['differential_suite']
    contract = contracts[suite['owner_contract']]
    for profile in reference['profiles']:
        for scenario in scenarios['scenarios']:
            if profile['engine_id'] not in scenario['applies_to']:
                continue
            case_id = suite['case_id_template'].format(
                engine_id=profile['engine_id'],
                scenario_id=scenario['scenario_id'],
            )
            capability = suite['promotes_template'].format(
                engine_id=profile['engine_id'],
                reference_profile=profile['reference_profile'],
            )
            cases.append({
                'case_id': case_id,
                'suite_id': suite['suite_id'],
                'owner_contract': suite['owner_contract'],
                'owner_search_key': contract['search_key'],
                'owner_dependency': contract['dependency'],
                'operation': suite['operation'],
                'assertions': copy.deepcopy(suite['assertions']),
                'promotes': [capability],
                'transaction_sensitive': bool(
                    scenario.get('transaction_observation')
                ),
                'engine_id': profile['engine_id'],
                'family': profile['family'],
                'reference_profile': profile['reference_profile'],
                'source_packet_digest': profile[
                    'packet_manifest_sha256'
                ],
                'scenario_id': scenario['scenario_id'],
                'scenario_digest': canonical_digest(scenario),
            })
    return cases


def definition_errors(catalog: Mapping, reference: Mapping,
                      scenarios: Mapping) -> list[str]:
    errors = []
    if catalog.get('schema') != \
            'cdeadmin.upstream-acceptance-catalog.v1':
        errors.append('acceptance catalog schema is invalid')
    if catalog.get('catalog_version') != '1.0.0' or \
            catalog.get('candidate_protocol_version') != '1.0.0':
        errors.append('acceptance catalog version is not exact')
    if catalog.get('authority_invariants') != EXPECTED_AUTHORITIES:
        errors.append('acceptance authority invariants are invalid')
    policy = catalog.get('execution_policy', {})
    required_policy = {
        'fixture_counts_as_pass': False,
        'fixture_can_promote': False,
        'embedded_backend': 'forbidden',
        'donor_backend_substitute': 'forbidden',
        'source_modification_required': False,
        'required_donor_transport': 'actual_donor_listener',
    }
    for field, expected in required_policy.items():
        if policy.get(field) != expected:
            errors.append(f'execution policy {field} must be {expected!r}')
    contracts = catalog.get('contracts', {})
    for name, contract in contracts.items():
        if contract.get('dependency') not in DEPENDENCIES:
            errors.append(f'contract {name} has unknown dependency owner')
        if not isinstance(contract.get('search_key'), str) or not \
                contract['search_key']:
            errors.append(f'contract {name} has no search key')
    suites = catalog.get('suites', [])
    suite_ids = {item.get('suite_id') for item in suites}
    suite_ids.add(catalog.get('differential_suite', {}).get('suite_id'))
    if suite_ids != EXPECTED_SUITES or len(suites) != 6:
        errors.append('acceptance suite inventory is incomplete')
    fixed_count = 0
    seen = set()
    for suite in suites:
        if suite.get('owner_contract') not in contracts:
            errors.append(f"suite {suite.get('suite_id')} has no owner")
        if not suite.get('promotes'):
            errors.append(f"suite {suite.get('suite_id')} promotes nothing")
        for case in suite.get('cases', []):
            fixed_count += 1
            case_id = case.get('case_id')
            if not isinstance(case_id, str) or case_id in seen:
                errors.append(f'duplicate or invalid case id {case_id!r}')
            seen.add(case_id)
            assertions = case.get('assertions')
            if not isinstance(assertions, list) or len(assertions) < 3 or \
                    len(assertions) != len(set(assertions)):
                errors.append(f'case {case_id!r} assertions are invalid')
    if fixed_count != 54:
        errors.append(f'fixed case inventory is {fixed_count}, expected 54')
    if reference.get('schema') != 'cdeadmin.reference-corpus.v1' or \
            len(reference.get('profiles', [])) != 25:
        errors.append('reference corpus is not the exact 25-profile catalog')
    if scenarios.get('schema') != 'cdeadmin.scenario-catalog.v1' or \
            len(scenarios.get('scenarios', [])) != 18:
        errors.append('differential scenario catalog is not exact')
    try:
        cases = expand_cases(catalog, reference, scenarios)
    except (KeyError, TypeError) as exc:
        errors.append(f'cannot expand acceptance cases: {exc}')
        return errors
    if len(cases) != 208:
        errors.append(f'expanded case inventory is {len(cases)}, expected 208')
    differential = [
        item for item in cases
        if item['suite_id'] == 'exact_donor_listener_differential'
    ]
    if len(differential) != 154:
        errors.append('differential matrix is not the exact 154 cells')
    if len({item['case_id'] for item in cases}) != len(cases):
        errors.append('expanded case identifiers are not unique')
    mga_ids = {
        item['case_id'] for item in cases
        if item['suite_id'] == 'mga_transaction_boundaries_and_finality'
    }
    required_mga = {
        'mga.initial_boundary', 'mga.begin.settings',
        'mga.savepoint.create', 'mga.savepoint.rollback',
        'mga.savepoint.release', 'mga.commit.replacement',
        'mga.rollback.replacement', 'mga.autocommit.success',
        'mga.autocommit.failure', 'mga.prepare.replacement',
        'mga.unknown_finality', 'mga.replay.refusal',
    }
    if mga_ids != required_mga:
        errors.append('MGA acceptance scenarios are incomplete')
    promotion = catalog.get('promotion_policy', {})
    if not all((
        promotion.get('all_capability_cases_must_pass') is True,
        promotion.get('fixture_evidence_promotes') is False,
        promotion.get('stale_evidence_promotes') is False,
        promotion.get('manual_override') is False,
    )):
        errors.append('capability promotion policy is not evidence-closed')
    return errors


def evaluate_definitions(source: Path) -> dict:
    catalog = load_json(source / CATALOG)
    reference = load_json(source / REFERENCE_CORPUS)
    scenarios = load_json(source / SCENARIO_CATALOG)
    errors = definition_errors(catalog, reference, scenarios)
    cases = [] if errors else expand_cases(catalog, reference, scenarios)
    return {
        'schema': 'cdeadmin.upstream-acceptance-definition-result.v1',
        'valid': not errors,
        'errors': errors,
        'suite_count': 7,
        'fixed_case_count': 54,
        'differential_profile_count': len(reference.get('profiles', [])),
        'differential_scenario_count': len(
            scenarios.get('scenarios', [])
        ),
        'differential_cell_count': len(cases) - 54 if cases else 0,
        'expanded_case_count': len(cases),
        'production_ready': False,
    }


def _manifest_errors(manifest: Mapping, catalog: Mapping,
                     selected_dependencies: set[str],
                     allow_contract_fixture: bool) -> tuple[list[str], bool]:
    errors = []
    if manifest.get('schema') != \
            'cdeadmin.upstream-candidate-manifest.v1':
        errors.append('candidate manifest schema is invalid')
    if manifest.get('candidate_protocol_version') != catalog[
            'candidate_protocol_version']:
        errors.append('candidate protocol version is unsupported')
    if manifest.get('authority_invariants') != EXPECTED_AUTHORITIES:
        errors.append('candidate authority invariants are invalid')
    candidate_class = manifest.get('candidate_class')
    fixture = candidate_class == 'contract_fixture'
    if fixture:
        if not allow_contract_fixture:
            errors.append('contract fixture requires explicit test admission')
        for field, expected in (
            ('production_candidate', False),
            ('execution_backend', 'none'),
            ('network_enabled', False),
        ):
            if manifest.get(field) != expected:
                errors.append(f'contract fixture {field} is invalid')
        return errors, False
    policy = catalog['execution_policy']
    if candidate_class != policy['qualifying_candidate_class']:
        errors.append('candidate class is not qualifying')
    if manifest.get('production_candidate') is not True:
        errors.append('candidate is not declared as a production candidate')
    if manifest.get('execution_backend') != policy['qualifying_backend']:
        errors.append('candidate is not bound to native ScratchBird')
    backend = manifest.get('backend_assertions', {})
    required_backend = {
        'native_scratchbird_backend': True,
        'public_driver_api_only': True,
        'embedded_backend_substitute': False,
        'donor_backend_substitute': False,
        'donor_reference_transport': 'actual_donor_listener',
    }
    for field, expected in required_backend.items():
        if backend.get(field) != expected:
            errors.append(f'candidate backend assertion {field} is invalid')
    dependencies = manifest.get('dependencies', {})
    for dependency in selected_dependencies:
        value = dependencies.get(dependency, {})
        if value.get('state') != 'candidate_delivered':
            errors.append(f'{dependency} candidate is not delivered')
        if not SHA256.fullmatch(str(value.get('artifact_digest', ''))):
            errors.append(f'{dependency} artifact digest is invalid')
        if value.get('signature_verified') is not True:
            errors.append(f'{dependency} signature is not verified')
        if not isinstance(value.get('version'), str) or not value['version']:
            errors.append(f'{dependency} version is absent')
    return errors, not errors


def _result_errors(result: Mapping, case: Mapping, manifest: Mapping,
                   qualifying: bool, catalog: Mapping,
                   now: datetime) -> list[str]:
    errors = []
    if result.get('schema') != 'cdeadmin.acceptance-case-evidence.v1':
        errors.append('case evidence schema is invalid')
    if result.get('case_id') != case['case_id']:
        errors.append('case evidence identity changed')
    allowed = {'passed', 'failed', 'refused', 'blocked', 'fixture_observed'}
    status = result.get('status')
    if status not in allowed:
        errors.append('case evidence status is invalid')
    if qualifying and status == 'fixture_observed':
        errors.append('live candidate returned fixture evidence')
    if not qualifying and status != 'fixture_observed':
        errors.append('non-qualifying candidate cannot return a test outcome')
    try:
        observed = _parse_time(result.get('observed_at'))
        expires = _parse_time(result.get('expires_at'))
        maximum = timedelta(
            days=catalog['execution_policy']['evidence_maximum_age_days']
        )
        if observed > now + timedelta(minutes=5) or \
                now - observed > maximum:
            errors.append('case evidence is not current')
        if expires <= now or expires <= observed:
            errors.append('case evidence expiry is invalid')
    except AcceptanceError as exc:
        errors.append(str(exc))
    claimed = result.get('evidence_digest')
    digest_value = copy.deepcopy(dict(result))
    digest_value.pop('evidence_digest', None)
    if not SHA256.fullmatch(str(claimed)) or \
            canonical_digest(digest_value) != claimed:
        errors.append('case evidence digest is invalid')
    assertions = result.get('assertions')
    if not isinstance(assertions, Mapping) or \
            set(assertions) != set(case['assertions']):
        errors.append('case assertion inventory is not exact')
    elif status == 'passed' and not all(
            value is True for value in assertions.values()):
        errors.append('passed case has a failed assertion')
    runtime = result.get('runtime_identity', {})
    if qualifying:
        if runtime.get('execution_backend') != 'scratchbird_native_live':
            errors.append('case did not execute on native ScratchBird')
        driver = manifest['dependencies'].get('D2_DRIVER')
        if driver and runtime.get('driver_artifact_digest') != driver.get(
                'artifact_digest'):
            errors.append('case driver runtime identity is wrong')
    if case.get('transaction_sensitive') and status == 'passed':
        if result.get('scratchbird_authority') != EXPECTED_AUTHORITIES:
            errors.append('transaction evidence weakens MGA authority')
    if case['suite_id'] == 'exact_donor_listener_differential' and \
            status == 'passed':
        reference = result.get('reference_runtime', {})
        scratchbird = result.get('scratchbird_runtime', {})
        reference_digest = reference.get('runtime_artifact_digest', '')
        if (
                reference.get('engine_id') != case['engine_id'] or
                reference.get('reference_profile') !=
                case['reference_profile'] or
                reference.get('transport') != 'actual_donor_listener' or
                not SHA256.fullmatch(str(reference_digest))):
            errors.append('exact donor listener identity is invalid')
        scratchbird_digest = scratchbird.get(
            'runtime_artifact_digest', ''
        )
        if (
                scratchbird.get('engine_id') != 'scratchbird' or
                scratchbird.get('emulated_engine_id') !=
                case['engine_id'] or
                scratchbird.get('transport') !=
                'scratchbird_emulation_listener' or
                not SHA256.fullmatch(str(scratchbird_digest))):
            errors.append('ScratchBird emulation listener identity is invalid')
        if result.get('embedded_backend_substitute') is not False or \
                result.get('donor_backend_substitute') is not False:
            errors.append('differential evidence used a substitute backend')
        if result.get('scenario_id') != case['scenario_id']:
            errors.append('differential scenario identity changed')
        if result.get('source_packet_digest') != case[
                'source_packet_digest']:
            errors.append('differential source packet identity changed')
    return errors


def _failure(case: Mapping, diagnostic: str) -> dict:
    return {
        'case_id': case['case_id'],
        'suite_id': case['suite_id'],
        'status': 'failed',
        'owner_contract': case['owner_contract'],
        'owner_search_key': case['owner_search_key'],
        'owner_dependency': case['owner_dependency'],
        'diagnostics': [diagnostic],
        'evidence_digest': None,
    }


def _promotions(cases: list[Mapping], results: list[Mapping],
                qualifying: bool) -> list[dict]:
    requirements: dict[str, set[str]] = {}
    for case in cases:
        for capability in case['promotes']:
            requirements.setdefault(capability, set()).add(case['case_id'])
    by_id = {result['case_id']: result for result in results}
    promotions = []
    for capability, case_ids in sorted(requirements.items()):
        passed = {
            case_id for case_id in case_ids
            if by_id.get(case_id, {}).get('status') == 'passed' and
            not by_id[case_id].get('diagnostics')
        }
        promoted = qualifying and passed == case_ids
        evidence = sorted(
            by_id[case_id]['evidence_digest'] for case_id in passed
            if by_id[case_id].get('evidence_digest')
        )
        promotions.append({
            'capability_id': capability,
            'state': 'promoted' if promoted else 'not_promoted',
            'required_cases': len(case_ids),
            'passing_cases': len(passed),
            'evidence_set_digest': (
                canonical_digest(evidence) if promoted else None
            ),
            'manual_override': False,
        })
    return promotions


def run_candidate(adapter: CandidateAdapter, source: Path,
                  suite_ids: set[str] | None = None,
                  allow_contract_fixture: bool = False,
                  now: datetime | None = None) -> dict:
    """Run selected definitions through an externally supplied adapter."""
    now = now or datetime.now(timezone.utc)
    catalog = load_json(source / CATALOG)
    reference = load_json(source / REFERENCE_CORPUS)
    scenarios = load_json(source / SCENARIO_CATALOG)
    errors = definition_errors(catalog, reference, scenarios)
    if errors:
        raise AcceptanceError('; '.join(errors))
    all_cases = expand_cases(catalog, reference, scenarios)
    if suite_ids:
        unknown = suite_ids - EXPECTED_SUITES
        if unknown:
            raise AcceptanceError(f'unknown suites: {sorted(unknown)}')
        cases = [item for item in all_cases if item['suite_id'] in suite_ids]
    else:
        cases = all_cases
    try:
        manifest = adapter.candidate_manifest()
    except Exception as exc:
        raise AcceptanceError(
            f'candidate manifest failed: {type(exc).__name__}'
        ) from exc
    if not isinstance(manifest, Mapping):
        raise AcceptanceError('candidate manifest must be a mapping')
    dependencies = {item['owner_dependency'] for item in cases}
    manifest_errors, qualifying = _manifest_errors(
        manifest, catalog, dependencies, allow_contract_fixture
    )
    if manifest_errors:
        return {
            'schema': 'cdeadmin.upstream-acceptance-run.v1',
            'candidate_id': manifest.get('candidate_id'),
            'qualifying': False,
            'valid': False,
            'errors': manifest_errors,
            'results': [],
            'promotions': [],
        }
    results = []
    for case in cases:
        try:
            raw = adapter.execute_case(copy.deepcopy(case))
            if not isinstance(raw, Mapping):
                raise AcceptanceError('candidate result is not a mapping')
            result_errors = _result_errors(
                raw, case, manifest, qualifying, catalog, now
            )
            if result_errors:
                results.append(_failure(case, '; '.join(result_errors)))
            else:
                result = {
                    'case_id': case['case_id'],
                    'suite_id': case['suite_id'],
                    'status': raw['status'],
                    'owner_contract': case['owner_contract'],
                    'owner_search_key': case['owner_search_key'],
                    'owner_dependency': case['owner_dependency'],
                    'diagnostics': copy.deepcopy(raw.get('diagnostics', [])),
                    'evidence_digest': raw['evidence_digest'],
                }
                results.append(result)
        except Exception as exc:
            results.append(_failure(
                case, f'candidate execution failed: {type(exc).__name__}'
            ))
    promotions = _promotions(cases, results, qualifying)
    counts = {
        status: sum(item['status'] == status for item in results)
        for status in (
            'passed', 'failed', 'refused', 'blocked', 'fixture_observed'
        )
    }
    if qualifying:
        run_valid = all(
            item['status'] == 'passed' and not item['diagnostics']
            for item in results
        )
    else:
        run_valid = all(
            item['status'] == 'fixture_observed' for item in results
        )
    return {
        'schema': 'cdeadmin.upstream-acceptance-run.v1',
        'candidate_id': manifest.get('candidate_id'),
        'qualifying': qualifying,
        'valid': run_valid,
        'errors': [],
        'selected_suites': sorted({item['suite_id'] for item in cases}),
        'case_count': len(cases),
        'counts': counts,
        'results': results,
        'promotions': promotions,
        'promoted_capabilities': sum(
            item['state'] == 'promoted' for item in promotions
        ),
    }


def load_candidate(path: Path, config: Mapping[str, Any]):
    """Load a user-supplied adapter file without changing CDEadmin source."""
    path = path.resolve()
    specification = importlib.util.spec_from_file_location(
        'cdeadmin_external_acceptance_candidate', path
    )
    if specification is None or specification.loader is None:
        raise AcceptanceError(f'cannot load candidate adapter {path}')
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    factory = getattr(module, 'create_acceptance_adapter', None)
    if not callable(factory):
        raise AcceptanceError('candidate adapter has no public factory')
    adapter = factory(copy.deepcopy(dict(config)))
    for method in ('candidate_manifest', 'execute_case'):
        if not callable(getattr(adapter, method, None)):
            raise AcceptanceError(f'candidate adapter lacks {method}')
    return adapter


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Run CDEadmin upstream acceptance suites.'
    )
    parser.add_argument('--source', type=Path, default=Path.cwd())
    parser.add_argument('--candidate-adapter', type=Path)
    parser.add_argument('--candidate-config', type=Path)
    parser.add_argument('--suite', action='append', default=[])
    parser.add_argument('--allow-contract-fixture', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    source = args.source.resolve()
    if args.candidate_adapter is None:
        result = evaluate_definitions(source)
    else:
        if args.candidate_config is None:
            print('ERROR: --candidate-config is required')
            return 2
        try:
            config = load_json(args.candidate_config.resolve())
            adapter = load_candidate(args.candidate_adapter, config)
            result = run_candidate(
                adapter, source, set(args.suite) or None,
                args.allow_contract_fixture,
            )
        except AcceptanceError as exc:
            print(f'ERROR: {exc}')
            return 2
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, sort_keys=True))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
