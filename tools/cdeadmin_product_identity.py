#!/usr/bin/env python3
"""Audit upstream attribution and enforce CDEadmin identity separation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path


DEFAULT_POLICY = Path('tools/cdeadmin_product_identity_policy.json')
TEXT_SUFFIXES = frozenset({
    '', '.cfg', '.conf', '.css', '.desktop', '.html', '.in', '.ini', '.iss',
    '.js', '.json', '.jsx', '.md', '.py', '.rst', '.sh', '.toml', '.tpl',
    '.ts', '.tsx', '.txt', '.wsgi', '.xml', '.yaml', '.yml',
})
SURFACE_ROOTS = {
    '.github': 'ci',
    'docs': 'documentation',
    'pkg': 'packaging',
    'runtime': 'desktop-runtime',
    'tools': 'tooling',
    'web': 'web-application',
}


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'cannot load JSON from {path}: {exc}') from exc


def load_identity_runtime(path):
    specification = importlib.util.spec_from_file_location(
        'cdeadmin_product_identity_runtime', path
    )
    if specification is None or specification.loader is None:
        raise ValueError(f'cannot load identity runtime from {path}')
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def surface_for(path):
    first = path.parts[0] if path.parts else ''
    return SURFACE_ROOTS.get(first, 'repository')


def inventory_files(source, excluded_directories):
    excluded = set(excluded_directories)
    for path in source.rglob('*'):
        if not path.is_file() or any(part in excluded for part in path.parts):
            continue
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
        except OSError:
            continue
        yield path


def branding_inventory(source, policy):
    terms = tuple(policy['inventory_terms'])
    by_term = {term: Counter() for term in terms}
    files_by_surface = Counter()
    matched_files = set()
    scanned_files = 0
    for path in inventory_files(
            source, policy['inventory_excluded_directories']):
        scanned_files += 1
        try:
            content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            continue
        relative = path.relative_to(source)
        surface = surface_for(relative)
        matched = False
        for term in terms:
            count = content.count(term)
            if count:
                by_term[term][surface] += count
                matched = True
        if matched:
            matched_files.add(str(relative))
            files_by_surface[surface] += 1
    totals = {
        term: sum(counts.values()) for term, counts in by_term.items()
    }
    return {
        'scanned_files': scanned_files,
        'matched_files': len(matched_files),
        'matched_files_by_surface': dict(sorted(files_by_surface.items())),
        'occurrences': {
            term: {
                'total': totals[term],
                'by_surface': dict(sorted(by_term[term].items())),
            }
            for term in terms
        },
    }


def check_source_anchors(source, policy):
    errors = []
    results = []
    for anchor in policy['source_anchors']:
        path = source / anchor['path']
        present = False
        try:
            present = anchor['literal'] in path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            pass
        results.append({
            'path': anchor['path'],
            'literal': anchor['literal'],
            'present': present,
        })
        if not present:
            errors.append(
                f"branding source anchor missing: {anchor['path']} -> "
                f"{anchor['literal']!r}"
            )
    return results, errors


def check_product_banners(source, policy):
    """Reject obsolete upstream product banners while retaining attribution."""
    forbidden = tuple(policy.get('forbidden_product_banners', ()))
    excluded_paths = set(policy.get(
        'product_banner_scan_excluded_paths', ()
    ))
    matches = []
    if not forbidden:
        return matches, []
    for path in inventory_files(
            source, policy['inventory_excluded_directories']):
        relative = str(path.relative_to(source))
        if relative in excluded_paths:
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            continue
        present = [banner for banner in forbidden if banner in content]
        if present:
            matches.append({
                'path': relative,
                'banners': present,
            })
    errors = [
        f"obsolete upstream product banner remains: {item['path']}: "
        f"{item['banners']!r}"
        for item in matches
    ]
    return matches, errors


def check_product_surfaces(source, policy):
    """Require CDEadmin identity; reject branding on live surfaces."""
    errors = []
    results = []
    for assertion in policy.get('product_surface_assertions', ()):
        relative = assertion['path']
        path = source / relative
        try:
            content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            content = ''
        missing = [
            literal for literal in assertion.get('required', ())
            if literal not in content
        ]
        forbidden = [
            literal for literal in assertion.get('forbidden', ())
            if literal in content
        ]
        results.append({
            'path': relative,
            'valid': not missing and not forbidden,
            'missing': missing,
            'forbidden_present': forbidden,
        })
        if missing:
            errors.append(
                f'CDEadmin product surface incomplete: {relative}: '
                f'missing={missing!r}'
            )
        if forbidden:
            errors.append(
                f'upstream branding active on CDEadmin product surface: '
                f'{relative}: {forbidden!r}'
            )
    return results, errors


def check_notices(source, policy):
    errors = []
    upstream = []
    for relative in policy['protected_upstream_notices']:
        path = source / relative
        try:
            content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            errors.append(f'protected upstream notice unavailable: {relative}')
            upstream.append({'path': relative, 'preserved': False})
            continue
        missing = [
            fragment for fragment in policy['upstream_notice_fragments']
            if fragment not in content
        ]
        upstream.append({
            'path': relative,
            'preserved': not missing,
            'missing_fragments': missing,
        })
        if missing:
            errors.append(
                f'protected upstream notice incomplete: {relative}: '
                f'{missing!r}'
            )
    notice_path = source / policy['notice']
    try:
        notice = notice_path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        notice = ''
    missing = [
        fragment for fragment in policy['cdeadmin_notice_fragments']
        if fragment not in notice
    ]
    if missing:
        errors.append(f'CDEadmin attribution notice incomplete: {missing!r}')
    return {
        'upstream': upstream,
        'cdeadmin': {
            'path': policy['notice'],
            'preserved': not missing,
            'missing_fragments': missing,
        },
    }, errors


def check_smoke_plan(source, identity, policy):
    errors = []
    plan = load_json(source / policy['smoke_plan'])
    records = plan.get('delivery_modes', ())
    selected = set(identity['packaging']['selected_delivery_modes'])
    planned = {record.get('mode') for record in records}
    if selected != planned:
        errors.append(
            'package smoke modes differ from selected delivery modes: '
            f'missing={sorted(selected - planned)!r}, '
            f'extra={sorted(planned - selected)!r}'
        )
    common = set(plan.get('common_assertions', ()))
    required_common = {
        'cdeadmin-identity', 'isolated-namespaces',
        'upstream-attribution', 'pgadmin-coexistence',
        'independent-update-channel', 'independent-signing-identity',
    }
    if not required_common.issubset(common):
        errors.append('package smoke plan lacks required common assertions')
    results = []
    for record in records:
        mode = record.get('mode')
        anchors = record.get('build_entrypoints', ())
        missing = [relative for relative in anchors
                   if not (source / relative).is_file()]
        smoke = record.get('smoke')
        valid = bool(mode and record.get('artifact_identity') and smoke) \
            and not missing
        results.append({
            'mode': mode,
            'build_entrypoints': list(anchors),
            'missing_entrypoints': missing,
            'smoke_assertions': len(smoke or ()),
            'planned': valid,
        })
        if not valid:
            errors.append(f'incomplete package smoke plan for {mode!r}')
    return {
        'plan_version': plan.get('plan_version'),
        'status': plan.get('status'),
        'common_assertions': sorted(common),
        'delivery_modes': results,
    }, errors


def evaluate(source, policy_path=DEFAULT_POLICY, include_inventory=True):
    source = Path(source).resolve()
    policy_path = Path(policy_path)
    if not policy_path.is_absolute():
        policy_path = source / policy_path
    policy = load_json(policy_path)
    errors = []
    runtime = load_identity_runtime(source / policy['identity_runtime'])
    try:
        identity = runtime.load_identity(source / policy['identity'])
    except runtime.ProductIdentityError as exc:
        identity = None
        errors.append(str(exc))
    anchors, anchor_errors = check_source_anchors(source, policy)
    obsolete_banners, banner_errors = check_product_banners(source, policy)
    product_surfaces, product_surface_errors = check_product_surfaces(
        source, policy
    )
    notices, notice_errors = check_notices(source, policy)
    errors.extend(anchor_errors)
    errors.extend(banner_errors)
    errors.extend(product_surface_errors)
    errors.extend(notice_errors)
    smoke = None
    if identity is not None:
        try:
            smoke, smoke_errors = check_smoke_plan(
                source, identity, policy
            )
            errors.extend(smoke_errors)
        except ValueError as exc:
            errors.append(str(exc))
    result = {
        'valid': not errors,
        'policy_version': policy.get('policy_version'),
        'identity_version': identity.get('identity_version')
        if identity else None,
        'identity_status': identity['product']['identity_status']
        if identity else None,
        'release_ready': identity['product']['release_ready']
        if identity else None,
        'namespace_collisions': runtime.namespace_collisions(identity)
        if identity else None,
        'anchors': anchors,
        'obsolete_product_banners': obsolete_banners,
        'product_surfaces': product_surfaces,
        'notices': notices,
        'package_smoke_plan': smoke,
        'errors': errors,
    }
    if include_inventory:
        result['branding_inventory'] = branding_inventory(source, policy)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('.'))
    parser.add_argument('--policy', type=Path, default=DEFAULT_POLICY)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--skip-inventory', action='store_true')
    arguments = parser.parse_args(argv)
    try:
        result = evaluate(
            arguments.source,
            arguments.policy,
            include_inventory=not arguments.skip_inventory,
        )
    except ValueError as exc:
        print(json.dumps({'valid': False, 'errors': [str(exc)]}, indent=2))
        return 2
    rendered = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding='utf-8')
    sys.stdout.write(rendered)
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
