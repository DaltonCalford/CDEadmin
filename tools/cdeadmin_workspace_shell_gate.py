##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Validate and render inert native-CDE workspace fixture stories."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any, Mapping


CATALOG = Path('tools/cdeadmin_workspace_shells.json')
STORY_MANIFEST = Path(
    'tools/tests/fixtures/cdeadmin_workspace_shells/story_manifest.json'
)
STATES = frozenset({
    'unavailable', 'deferred', 'permission_filtered', 'stale', 'unknown',
})
CONTROL_WORKSPACES = frozenset({
    'cde.topology', 'cde.runtime', 'cde.filespace', 'cde.parser',
    'cde.policy', 'cde.security', 'cde.agents', 'cde.observability',
    'cde.operations', 'cde.evidence',
})
MODEL_WORKSPACES = {
    'model.document': 'document',
    'model.graph': 'graph',
    'model.key_value': 'key_value',
    'model.time_series': 'time_series',
    'model.vector': 'vector',
    'model.search': 'search',
    'model.spatial': 'spatial',
    'model.columnar': 'columnar',
}
MGA_INVARIANTS = {
    'engine_execution': 'sblr_and_internal_procedures_only',
    'internal_identity': 'uuid_backed_no_synthesized_paths',
    'transaction_authority': 'scratchbird_mga_engine_owned',
    'transaction_finality_source': 'durable_transaction_inventory',
    'workspace_authority': 'read_only_projection_never_authority',
    'parser_authority': 'dialect_to_sblr_no_engine_sql_execution',
    'agent_runtime': 'database_owned_server_runtime_only',
    'unknown_behavior': 'fail_closed',
}
SHA256 = re.compile(r'^[a-f0-9]{64}$')


class WorkspaceShellError(RuntimeError):
    """A workspace fixture would cross its inert shell boundary."""


def load_catalog(source: Path) -> dict:
    """Load the declarative shell catalog from one repository root."""
    try:
        value = json.loads(
            (source / CATALOG).read_text(encoding='utf-8')
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceShellError(f'cannot load workspace catalog: {exc}') \
            from exc
    if not isinstance(value, dict):
        raise WorkspaceShellError('workspace catalog must be an object')
    return value


def _load_story_manifest(source: Path) -> dict:
    try:
        value = json.loads(
            (source / STORY_MANIFEST).read_text(encoding='utf-8')
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceShellError(f'cannot load story manifest: {exc}') \
            from exc
    if not isinstance(value, dict):
        raise WorkspaceShellError('story manifest must be an object')
    return value


def _required_text(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f'{label} must be non-empty text')


def _validate_fixture_policy(catalog: Mapping, errors: list[str]) -> None:
    fixture = catalog.get('fixture_policy', {})
    expected = {
        'production': False,
        'activation': 'fixture_only',
        'network_enabled': False,
        'authentication_enabled': False,
        'execution_enabled': False,
        'live_provider_evidence': None,
    }
    for field, value in expected.items():
        if fixture.get(field) != value:
            errors.append(f'fixture policy {field} must be {value!r}')
    _required_text(
        fixture.get('persistent_label'), 'persistent fixture label', errors
    )
    requirements = fixture.get('production_evidence_requirements', [])
    required = {
        'qualified_scratchbird_provider',
        'exact_cde_runtime_identity',
        'compatible_python_driver_handoff',
        'current_capability_evidence',
        'permission_and_redaction_contract',
    }
    if set(requirements) != required:
        errors.append('production evidence requirements are incomplete')
    accessibility = catalog.get('accessibility_policy', {})
    required_accessibility = {
        'persistent_fixture_label', 'color_independent_states',
        'reduced_motion_safe', 'keyboard_reachable_status',
        'hidden_resources_not_announced',
    }
    if set(accessibility) != required_accessibility or not all(
            accessibility.values()):
        errors.append('fixture accessibility policy must fail closed')


def _validate_states(catalog: Mapping, errors: list[str]) -> None:
    states = catalog.get('state_presentations', {})
    if set(states) != STATES:
        errors.append('workspace state presentations are incomplete')
        return
    diagnostics = set()
    for state, presentation in states.items():
        prefix = f'state {state}'
        for field in ('diagnostic_code', 'title', 'message', 'focus_target',
                      'recovery_kind'):
            _required_text(presentation.get(field), f'{prefix} {field}',
                           errors)
        diagnostic = presentation.get('diagnostic_code')
        if diagnostic in diagnostics:
            errors.append(f'{prefix} diagnostic code is duplicated')
        diagnostics.add(diagnostic)
        role = presentation.get('role')
        live = presentation.get('aria_live')
        if role not in {'status', 'alert'}:
            errors.append(f'{prefix} role is not accessible')
        if role == 'status' and live != 'polite':
            errors.append(f'{prefix} status must use polite announcements')
        if role == 'alert' and live != 'assertive':
            errors.append(f'{prefix} alert must use assertive announcements')


def _validate_workspace(workspace: Mapping, catalog: Mapping,
                        errors: list[str], status_ids: set[str]) -> None:
    workspace_id = workspace.get('workspace_id', '<missing>')
    prefix = f'workspace {workspace_id}'
    _required_text(workspace.get('title'), f'{prefix} title', errors)
    if workspace.get('delivery_state') != 'shell_only':
        errors.append(f'{prefix} must remain shell_only')
    if workspace.get('current_state') != 'unavailable':
        errors.append(f'{prefix} must remain unavailable before handoff')
    if set(workspace.get('supported_states', [])) != STATES:
        errors.append(f'{prefix} does not support every refusal state')
    for field in ('resource_binding', 'authority_path', 'runtime_identity'):
        if workspace.get(field) is not None:
            errors.append(f'{prefix} synthesizes {field}')
    capability = workspace.get('required_capability')
    if not isinstance(capability, str) or not capability.startswith(
            'scratchbird.'):
        errors.append(f'{prefix} capability identity is invalid')
    authority = workspace.get('authority_refs', [])
    known_authority = set(catalog.get('authoritative_search_keys', []))
    if not authority or not set(authority) <= known_authority:
        errors.append(f'{prefix} authority references are invalid')
    views = workspace.get('views', [])
    if not views or len(views) != len(set(views)):
        errors.append(f'{prefix} views must be unique and non-empty')
    behavior = workspace.get('missing_capability_behavior')
    actions = workspace.get('actions', [])
    removed = workspace.get('removed_action_ids', [])
    unavailable_code = catalog['state_presentations'][
        'unavailable'
    ]['diagnostic_code']
    if behavior == 'disable':
        if not actions or removed:
            errors.append(f'{prefix} disabled action disposition is invalid')
        for action in actions:
            if action.get('enabled') is not False:
                errors.append(f'{prefix} has an enabled fixture action')
            if action.get('mutation_class') not in {'none', 'read'}:
                errors.append(f'{prefix} exposes a mutation action')
            if action.get('disabled_reason') != unavailable_code:
                errors.append(f'{prefix} action lacks exact refusal reason')
            _required_text(action.get('label'), f'{prefix} action label',
                           errors)
    elif behavior == 'remove':
        if actions or not removed or len(removed) != len(set(removed)):
            errors.append(f'{prefix} removed action disposition is invalid')
    else:
        errors.append(f'{prefix} missing-capability behavior is invalid')
    access = workspace.get('accessibility', {})
    if access.get('landmark_role') != 'region':
        errors.append(f'{prefix} needs a region landmark')
    _required_text(access.get('accessible_name'),
                   f'{prefix} accessible name', errors)
    if access.get('heading_level') != 2:
        errors.append(f'{prefix} heading hierarchy is invalid')
    status_id = access.get('status_region_id')
    _required_text(status_id, f'{prefix} status region ID', errors)
    if status_id in status_ids:
        errors.append(f'{prefix} status region ID is duplicated')
    status_ids.add(status_id)
    if access.get('keyboard_order') != [
            'heading', 'status', 'views', 'guidance']:
        errors.append(f'{prefix} keyboard order is invalid')


def evaluate(source: Path) -> dict:
    """Return structural validity without promoting fixture availability."""
    try:
        source = source.resolve()
        catalog = load_catalog(source)
        story_manifest = _load_story_manifest(source)
    except WorkspaceShellError as exc:
        return {
            'valid': False, 'errors': [str(exc)], 'workspace_count': 0,
            'production_ready': False,
        }
    errors = []
    if catalog.get('schema') != 'cdeadmin.workspace-shell-catalog.v1':
        errors.append('workspace catalog schema is invalid')
    _validate_fixture_policy(catalog, errors)
    if catalog.get('authority_invariants') != MGA_INVARIANTS:
        errors.append('workspace authority invariants are invalid')
    authority = catalog.get('authoritative_search_keys', [])
    if len(authority) != len(set(authority)) or not authority:
        errors.append('authoritative search keys must be unique')
    _validate_states(catalog, errors)
    workspaces = catalog.get('workspaces', [])
    workspace_ids = [item.get('workspace_id') for item in workspaces]
    expected = CONTROL_WORKSPACES | set(MODEL_WORKSPACES)
    if set(workspace_ids) != expected or len(workspace_ids) != len(expected):
        errors.append('workspace inventory is not the exact required set')
    expected_story = {
        'schema': 'cdeadmin.workspace-shell-story-manifest.v1',
        'catalog': str(CATALOG),
        'persistent_label': catalog.get('fixture_policy', {}).get(
            'persistent_label'
        ),
        'production': False,
        'execution_enabled': False,
        'workspace_ids': workspace_ids,
        'states': [
            'unavailable', 'deferred', 'permission_filtered', 'stale',
            'unknown',
        ],
        'story_count': len(workspaces) * len(STATES),
    }
    if story_manifest != expected_story:
        errors.append('fixture story manifest does not match the catalog')
    status_ids = set()
    for workspace in workspaces:
        workspace_id = workspace.get('workspace_id')
        if workspace_id in CONTROL_WORKSPACES:
            if workspace.get('category') != 'control_plane' or (
                    workspace.get('model_family') is not None):
                errors.append(
                    f'workspace {workspace_id} control-plane class is invalid'
                )
        elif workspace_id in MODEL_WORKSPACES:
            if workspace.get('category') != 'model' or (
                    workspace.get('model_family') !=
                    MODEL_WORKSPACES[workspace_id]):
                errors.append(
                    f'workspace {workspace_id} model class is invalid'
                )
        _validate_workspace(workspace, catalog, errors, status_ids)
    serialized = json.dumps(catalog, sort_keys=True).lower()
    forbidden = ('/home/', 'file://', 'network_route', 'provider_handle')
    for token in forbidden:
        if token in serialized:
            errors.append(
                f'catalog contains synthesized/private token {token}'
            )
    return {
        'schema': 'cdeadmin.workspace-shell-gate-result.v1',
        'valid': not errors,
        'errors': errors,
        'workspace_count': len(workspaces),
        'control_plane_count': sum(
            item.get('category') == 'control_plane' for item in workspaces
        ),
        'model_count': sum(
            item.get('category') == 'model' for item in workspaces
        ),
        'fixture_story_count': len(workspaces) * len(STATES),
        'production_ready': False,
        'enabled_action_count': sum(
            action.get('enabled') is True
            for item in workspaces for action in item.get('actions', [])
        ),
        'resource_binding_count': sum(
            item.get('resource_binding') is not None for item in workspaces
        ),
    }


def render_story(catalog: Mapping, workspace_id: str, state: str) -> dict:
    """Render one inert, accessible fixture story as serializable data."""
    if state not in STATES:
        raise WorkspaceShellError(f'unknown workspace state {state!r}')
    try:
        workspace = next(
            item for item in catalog['workspaces']
            if item['workspace_id'] == workspace_id
        )
    except (KeyError, StopIteration) as exc:
        raise WorkspaceShellError(
            f'unknown workspace {workspace_id!r}'
        ) from exc
    presentation = copy.deepcopy(catalog['state_presentations'][state])
    actions = copy.deepcopy(workspace['actions'])
    for action in actions:
        action['enabled'] = False
        action['disabled_reason'] = presentation['diagnostic_code']
    return {
        'story_kind': 'non_production_fixture',
        'persistent_label': catalog['fixture_policy']['persistent_label'],
        'production': False,
        'workspace_id': workspace_id,
        'title': workspace['title'],
        'state': state,
        'presentation': presentation,
        'views': list(workspace['views']),
        'actions': actions,
        'removed_action_ids': list(workspace['removed_action_ids']),
        'action_disposition_reason': presentation['diagnostic_code'],
        'resource_binding': None,
        'authority_path': None,
        'runtime_identity': None,
        'accessibility': copy.deepcopy(workspace['accessibility']),
    }


def production_evidence_errors(workspace: Mapping,
                               evidence: Mapping | None) -> list[str]:
    """Explain why a shell cannot be bound to a production provider."""
    if not isinstance(evidence, Mapping):
        return ['live provider evidence is required']
    errors = []
    expected = {
        'workspace_id': workspace.get('workspace_id'),
        'capability_id': workspace.get('required_capability'),
        'provider_id': 'org.scratchbird.cde',
        'provider_mode': 'scratchbird_native',
        'capability_state': 'implemented',
        'driver_handoff_state': 'qualified',
    }
    for field, value in expected.items():
        if evidence.get(field) != value:
            errors.append(f'provider evidence {field} is invalid')
    runtime = evidence.get('runtime_identity', {})
    if runtime.get('engine_id') != 'scratchbird_cde':
        errors.append('exact CDE runtime identity is missing')
    _required_text(runtime.get('profile_version'),
                   'CDE runtime profile version', errors)
    if not SHA256.fullmatch(str(runtime.get('artifact_digest', ''))):
        errors.append('CDE runtime artifact digest is invalid')
    if not SHA256.fullmatch(str(evidence.get('evidence_digest', ''))):
        errors.append('capability evidence digest is invalid')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('.'))
    parser.add_argument('--workspace')
    parser.add_argument('--state', choices=sorted(STATES))
    args = parser.parse_args()
    result = evaluate(args.source)
    if args.workspace:
        if not args.state:
            parser.error('--state is required with --workspace')
        try:
            catalog = load_catalog(args.source.resolve())
            result['story'] = render_story(
                catalog, args.workspace, args.state
            )
        except WorkspaceShellError as exc:
            result['valid'] = False
            result['errors'].append(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
