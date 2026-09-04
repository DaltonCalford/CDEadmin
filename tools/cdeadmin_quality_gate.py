#!/usr/bin/env python3
"""Evaluate CDEadmin fixture, compatibility, performance, and build gates."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


DEFAULT_POLICY = Path('tools/cdeadmin_quality_policy.json')
STARTUP_MARKER = 'CDEADMIN_STARTUP_RESULT='
SOURCE_SUFFIXES = frozenset({
    '.css', '.js', '.json', '.jsx', '.py', '.ts', '.tsx',
})


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f'cannot load JSON from {path}: {exc}') from exc


def source_files(root: Path):
    if not root.exists():
        return ()
    return tuple(
        path for path in root.rglob('*')
        if path.is_file() and path.suffix in SOURCE_SUFFIXES and
        '__pycache__' not in path.parts
    )


def file_bytes(paths):
    return sum(path.stat().st_size for path in paths)


def violation(rule, path, detail):
    return {'rule': rule, 'path': str(path), 'detail': detail}


def fixture_violations(source: Path, policy):
    findings = []
    markers = (
        'org.cdeadmin.fixture.non_operational',
        'NonOperationalFixtureProvider',
    )
    for relative in policy['production_provider_roots']:
        root = source / relative
        for path in source_files(root):
            rel = path.relative_to(source)
            is_fixture_path = any(
                part.casefold().startswith('fixture') for part in rel.parts
            )
            if is_fixture_path:
                findings.append(violation(
                    'fixture-production-exclusion', rel,
                    'fixture path is below a production provider root',
                ))
            text = path.read_text(encoding='utf-8', errors='replace')
            if any(marker in text for marker in markers):
                findings.append(violation(
                    'fixture-production-marker', rel,
                    'test fixture identity entered a production provider',
                ))
            if path.name == 'provider_manifest.json':
                try:
                    manifest = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if manifest.get('fixture') is True:
                    findings.append(violation(
                        'fixture-production-manifest', rel,
                        'fixture manifest entered production build inputs',
                    ))
    fixture_path = source / policy['fixture_provider']
    try:
        tree = ast.parse(fixture_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, SyntaxError) as exc:
        findings.append(violation(
            'fixture-provider-source', fixture_path,
            f'fixture provider cannot be inspected: {type(exc).__name__}',
        ))
        return findings
    forbidden = set(policy['forbidden_fixture_imports'])
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [item.name.split('.', 1)[0] for item in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split('.', 1)[0]]
        for name in names:
            if name in forbidden:
                findings.append(violation(
                    'fixture-network-import',
                    fixture_path.relative_to(source),
                    f'fixture imports forbidden network module {name!r}',
                ))
    return findings


def contract_violations(source: Path, policy):
    findings = []
    schema_path = source / policy['contract_schema']
    matrix_path = source / policy['compatibility_matrix']
    corpus_path = source / policy['golden_corpus']
    schema = load_json(schema_path)
    matrix = load_json(matrix_path)
    corpus = load_json(corpus_path)
    declared = schema.get('x-cdeadmin-supported-contract-versions', {})
    rows = matrix.get('contract_versions', [])
    current_rows = [
        item['contract_version'] for item in rows
        if item.get('role') == 'current'
    ]
    previous_rows = [
        item['contract_version'] for item in rows
        if item.get('role') == 'previous'
    ]
    if current_rows != [schema.get('contract_version')]:
        findings.append(violation(
            'sdk-current-version', matrix_path.relative_to(source),
            'matrix current version differs from canonical schema',
        ))
    if declared.get('current') != schema.get('contract_version'):
        findings.append(violation(
            'schema-current-version', schema_path.relative_to(source),
            'declared current version differs from contract_version',
        ))
    if previous_rows != declared.get('previous'):
        findings.append(violation(
            'sdk-previous-version', matrix_path.relative_to(source),
            'matrix previous versions differ from schema declaration',
        ))
    definitions = set(schema.get('$defs', {}))
    corpus_contracts = {
        item.get('contract') for item in corpus.get('entries', [])
        if isinstance(item, dict)
    }
    missing = sorted(definitions - corpus_contracts)
    extra = sorted(corpus_contracts - definitions)
    if missing or extra:
        findings.append(violation(
            'golden-corpus-coverage', corpus_path.relative_to(source),
            f'missing={missing!r}; extra={extra!r}',
        ))
    return findings


def source_measurements(source: Path, policy):
    production = []
    for relative in policy['production_scan_roots']:
        production.extend(source_files(source / relative))
    frontend = source_files(source / 'web/pgadmin/cdeadmin/static/js')
    return {
        'production_cdeadmin_files': len(set(production)),
        'production_cdeadmin_bytes': file_bytes(set(production)),
        'frontend_cdeadmin_files': len(frontend),
        'frontend_cdeadmin_bytes': file_bytes(frontend),
    }


def source_budget_violations(measurements, policy):
    findings = []
    budgets = policy['source_budgets']
    pairs = (
        ('production_cdeadmin_bytes',
         'production_cdeadmin_bytes_maximum'),
        ('frontend_cdeadmin_bytes', 'frontend_cdeadmin_bytes_maximum'),
    )
    for measurement, maximum in pairs:
        if measurements[measurement] > budgets[maximum]:
            findings.append(violation(
                'source-size-budget', measurement,
                f'{measurements[measurement]} exceeds {budgets[maximum]}',
            ))
    return findings


def bundle_measurement(source: Path, policy, require_bundle):
    paths = []
    for relative in policy['built_bundle']['roots']:
        root = source / relative
        if root.exists():
            paths.extend(path for path in root.rglob('*') if path.is_file())
    result = {
        'status': 'measured' if paths else 'not_built',
        'file_count': len(paths),
        'bytes': file_bytes(paths),
        'baseline_bytes': policy['built_bundle']['baseline_bytes'],
        'maximum_bytes': policy['built_bundle']['maximum_bytes'],
    }
    findings = []
    if require_bundle and not paths:
        findings.append(violation(
            'built-bundle-required', 'built_bundle',
            'generated production bundle is absent',
        ))
    if paths and result['bytes'] > result['maximum_bytes']:
        findings.append(violation(
            'built-bundle-size', 'built_bundle',
            f"{result['bytes']} exceeds {result['maximum_bytes']}",
        ))
    return result, findings


def startup_measurement(source: Path, policy):
    startup = policy['startup']
    with tempfile.TemporaryDirectory(prefix='cdeadmin-quality-startup-') \
            as directory:
        temporary = Path(directory)
        data = temporary / 'data'
        for name in ('sessions', 'storage', 'azure'):
            (data / name).mkdir(parents=True)
        configuration = '\n'.join((
            'SERVER_MODE = False',
            f'DATA_DIR = {str(data)!r}',
            f'SQLITE_PATH = {str(data / "pgadmin4.db")!r}',
            f'SESSION_DB_PATH = {str(data / "sessions")!r}',
            f'STORAGE_DIR = {str(data / "storage")!r}',
            f'AZURE_CREDENTIAL_CACHE_DIR = {str(data / "azure")!r}',
            'TESTING = True',
            '',
        ))
        (temporary / 'config_local.py').write_text(
            configuration, encoding='utf-8'
        )
        # Linux carries ru_maxrss from the parent across fork/exec. Read the
        # post-exec process high-water mark where available so a test runner's
        # earlier allocations cannot inflate the application startup result.
        script = (
            'import json, os, resource, sys, time; '
            'start=time.perf_counter(); '
            'import config; from pgadmin import create_app; app=create_app(); '
            'fallback=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss; '
            'fallback=fallback//1024 if sys.platform=="darwin" else fallback; '
            'status_path="/proc/self/status"; '
            'status=(open(status_path,encoding="utf-8").read() '
            'if os.path.exists(status_path) else ""); '
            'rss=next((int(line.split()[1]) for line in status.splitlines() '
            'if line.startswith("VmHWM:")),fallback); '
            f'names={tuple(startup["required_extensions"])!r}; '
            'result={"route_count":len(app.url_map._rules),'
            '"extensions":{name:(name in app.extensions) for name in names},'
            '"inner_wall_seconds":time.perf_counter()-start,'
            '"max_rss_kib":rss}; '
            f'print({STARTUP_MARKER!r}+json.dumps(result,sort_keys=True))'
        )
        environment = dict(os.environ)
        python_paths = [str(temporary), str(source / 'web')]
        if environment.get('PYTHONPATH'):
            python_paths.append(environment['PYTHONPATH'])
        environment['PYTHONPATH'] = os.pathsep.join(python_paths)
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, '-c', script],
            cwd=temporary,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        wall = time.perf_counter() - started
    result = {
        'wall_seconds': round(wall, 6),
        'exit_code': completed.returncode,
    }
    findings = []
    line = next(
        (
            item for item in reversed(completed.stdout.splitlines())
            if item.startswith(STARTUP_MARKER)
        ),
        None,
    )
    if completed.returncode or line is None:
        result['status'] = 'failed'
        result['output_tail'] = completed.stdout[-1000:]
        findings.append(violation(
            'startup-failed', 'application',
            f'startup exited {completed.returncode}',
        ))
        return result, findings
    result.update(json.loads(line[len(STARTUP_MARKER):]))
    result['status'] = 'measured'
    if result['route_count'] != startup['route_count']:
        findings.append(violation(
            'startup-route-count', 'application',
            f"{result['route_count']} differs from {startup['route_count']}",
        ))
    if result['wall_seconds'] > startup['wall_seconds_maximum']:
        findings.append(violation(
            'startup-wall-budget', 'application',
            f"{result['wall_seconds']} exceeds "
            f"{startup['wall_seconds_maximum']}",
        ))
    if result['max_rss_kib'] > startup['max_rss_kib_maximum']:
        findings.append(violation(
            'startup-rss-budget', 'application',
            f"{result['max_rss_kib']} exceeds "
            f"{startup['max_rss_kib_maximum']}",
        ))
    missing = sorted(
        name for name, present in result['extensions'].items()
        if not present
    )
    if missing:
        findings.append(violation(
            'startup-extension', 'application',
            f'missing application services: {missing!r}',
        ))
    return result, findings


def load_runtime(path: Path):
    specification = importlib.util.spec_from_file_location(
        'cdeadmin_quality_contract_runtime', path
    )
    if specification is None or specification.loader is None:
        raise SystemExit(f'cannot load contract runtime from {path}')
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def contract_benchmark(source: Path, policy):
    benchmark = policy['contract_benchmark']
    schema = load_json(source / policy['contract_schema'])
    corpus = load_json(source / policy['golden_corpus'])
    runtime = load_runtime(source / policy['contract_runtime'])
    entry = next(
        item for item in corpus['entries']
        if item['contract'] == 'Resource'
    )
    started = time.perf_counter()
    for _index in range(benchmark['iterations']):
        runtime.validate_contract('Resource', entry['payload'], schema)
    wall = time.perf_counter() - started
    result = {
        'iterations': benchmark['iterations'],
        'wall_seconds': round(wall, 6),
        'maximum_seconds': benchmark['wall_seconds_maximum'],
    }
    findings = []
    if wall > benchmark['wall_seconds_maximum']:
        findings.append(violation(
            'contract-validation-budget', 'Resource',
            f'{wall:.6f} exceeds {benchmark["wall_seconds_maximum"]}',
        ))
    return result, findings


def evaluate(source: Path, policy: dict, require_bundle=False):
    findings = []
    findings.extend(fixture_violations(source, policy))
    findings.extend(contract_violations(source, policy))
    source_values = source_measurements(source, policy)
    findings.extend(source_budget_violations(source_values, policy))
    bundle, bundle_findings = bundle_measurement(
        source, policy, require_bundle
    )
    findings.extend(bundle_findings)
    startup, startup_findings = startup_measurement(source, policy)
    findings.extend(startup_findings)
    benchmark, benchmark_findings = contract_benchmark(source, policy)
    findings.extend(benchmark_findings)
    return {
        'schema': 'cdeadmin.quality-gate-result.v1',
        'baseline_id': policy['baseline_id'],
        'source_measurements': source_values,
        'bundle_measurement': bundle,
        'startup_measurement': startup,
        'contract_benchmark': benchmark,
        'violation_count': len(findings),
        'violations': findings,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('.'))
    parser.add_argument('--policy', type=Path, default=DEFAULT_POLICY)
    parser.add_argument('--require-built-bundle', action='store_true')
    parser.add_argument('--output', type=Path)
    return parser


def main():
    args = build_parser().parse_args()
    source = args.source.resolve()
    policy = load_json(args.policy.resolve())
    result = evaluate(source, policy, args.require_built_bundle)
    rendered = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    else:
        print(rendered, end='')
    return 0 if result['violation_count'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
