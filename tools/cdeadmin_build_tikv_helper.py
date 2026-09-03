#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Build the pinned CDEadmin TiKV helper and record reproducible evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / 'web/pgadmin/cdeadmin/providers/tikv/go_helper'
)
WORK_AREA = Path(os.environ.get(
    'CDEADMIN_WORK_AREA',
    str(Path.home() / 'Sandbox/pgadmin4_work_area'),
))
DEFAULT_OUTPUT = (
    WORK_AREA / 'toolchains/tikv/8.5.6/cdeadmin-tikv-helper'
)
DEFAULT_EVIDENCE = (
    WORK_AREA / 'reports/cdeadmin_distributed_engine_support/'
    'TIKV_HELPER_BUILD_EVIDENCE.json'
)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--go', default='go')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--evidence', type=Path, default=DEFAULT_EVIDENCE)
    return parser.parse_args()


def run(command):
    return subprocess.run(
        command, cwd=SOURCE, check=True, capture_output=True, text=True,
        timeout=300,
    )


def main():
    args = arguments()
    started = time.time()
    version = run([args.go, 'version']).stdout.strip()
    tests = run([args.go, 'test', './...'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix='.cdeadmin-tikv-helper-', dir=args.output.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        run([
            args.go, 'build', '-trimpath', '-buildvcs=false',
            '-o', str(temporary), '.',
        ])
        temporary.chmod(0o755)
        os.replace(temporary, args.output)
    finally:
        if temporary.exists():
            temporary.unlink()
    binary = args.output.read_bytes()
    module = run([args.go, 'version', '-m', str(args.output)])
    evidence = {
        'schema': 'cdeadmin.tikv-helper-build-evidence.v1',
        'source': str(SOURCE.relative_to(ROOT)),
        'output': str(args.output.resolve()),
        'go_version': version,
        'module_metadata': module.stdout.splitlines(),
        'tests': tests.stdout.splitlines(),
        'sha256': hashlib.sha256(binary).hexdigest(),
        'size_bytes': len(binary),
        'client_revision': 'v2.0.8-0.20260319064229-5cba4fc2f3a9',
        'started_at': started,
        'completed_at': time.time(),
        'passed': True,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
