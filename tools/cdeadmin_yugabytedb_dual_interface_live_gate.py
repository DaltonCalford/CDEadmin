#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify YSQL and YCQL against the same YugabyteDB runtime.

This gate is intentionally destructive and self-cleaning. It delegates each
interface to its protocol-owned live gate and never retries a request through
the other interface.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from cdeadmin_distributed_sql_live_verify import verify as verify_ysql
from cdeadmin_yugabytedb_ycql_live_gate import verify as verify_ycql


EXPECTED_SERVER = '2025.2.2.2'


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--ysql-port', type=int, default=5433)
    value.add_argument('--ycql-port', type=int, default=9042)
    value.add_argument('--version-api-host')
    value.add_argument('--version-api-port', type=int, default=7000)
    value.add_argument('--ysql-database', default='yugabyte')
    value.add_argument('--ysql-user', default='yugabyte')
    value.add_argument('--ysql-password-env')
    value.add_argument('--ysql-sslmode')
    value.add_argument('--ycql-local-dc', default='datacenter1')
    value.add_argument('--ycql-username')
    value.add_argument('--ycql-password-env', default='CDEADMIN_YCQL_PASSWORD')
    value.add_argument('--allow-mutation', action='store_true')
    value.add_argument('--output', type=Path, required=True)
    return value


def _interface_evidence_paths(output):
    suffix = output.suffix or '.json'
    stem = output.name[:-len(suffix)] if output.suffix else output.name
    return (
        output.with_name(f'{stem}.ysql{suffix}'),
        output.with_name(f'{stem}.ycql{suffix}'),
    )


def verify(args):
    if not args.allow_mutation:
        raise RuntimeError(
            'dual-interface qualification requires --allow-mutation'
        )
    for field in ('ysql_port', 'ycql_port', 'version_api_port'):
        port = getattr(args, field)
        if isinstance(port, bool) or not isinstance(port, int) or not (
                1 <= port <= 65535):
            raise ValueError(f'{field.replace("_", " ")} is invalid')
    if args.ysql_password_env and args.ysql_password_env not in os.environ:
        raise RuntimeError('YSQL credential environment variable is unset')
    if args.ycql_username and args.ycql_password_env not in os.environ:
        raise RuntimeError('YCQL credential environment variable is unset')

    ysql_output, ycql_output = _interface_evidence_paths(args.output)
    ysql_args = SimpleNamespace(
        engine='yugabytedb', host=args.host, port=args.ysql_port,
        database=args.ysql_database, user=args.ysql_user,
        password_environment=args.ysql_password_env,
        sslmode=args.ysql_sslmode, vtgate_http_host=None,
        vtgate_http_port=15001, vtgate_http_tls_mode='disable',
        allow_mutation=True, output=ysql_output,
    )
    ycql_args = SimpleNamespace(
        host=args.host, port=args.ycql_port,
        version_api_host=args.version_api_host or args.host,
        version_api_port=args.version_api_port,
        local_dc=args.ycql_local_dc, username=args.ycql_username,
        password_env=args.ycql_password_env, output=ycql_output,
    )
    started = time.time()
    failures = []
    results = {}
    for interface_id, callback, arguments in (
        ('ysql', verify_ysql, ysql_args),
        ('ycql', verify_ycql, ycql_args),
    ):
        try:
            result = callback(arguments)
            passed = (
                result.get('status') == 'passed'
                if interface_id == 'ysql' else result.get('passed') is True
            )
            if not passed:
                failures.append(f'{interface_id}: component gate failed')
            results[interface_id] = {
                'status': 'passed' if passed else 'failed',
                'profile_id': (
                    'yugabytedb-native' if interface_id == 'ysql'
                    else 'yugabytedb-ycql'
                ),
                'protocol_id': (
                    'postgresql_wire' if interface_id == 'ysql' else 'cql'
                ),
                'port': (
                    args.ysql_port if interface_id == 'ysql'
                    else args.ycql_port
                ),
                'evidence': str(
                    ysql_output if interface_id == 'ysql' else ycql_output
                ),
            }
            if interface_id == 'ycql':
                ycql_output.parent.mkdir(parents=True, exist_ok=True)
                ycql_output.write_text(
                    json.dumps(result, indent=2, sort_keys=True) + '\n',
                    encoding='utf-8',
                )
        except Exception as exc:
            failures.append(
                f'{interface_id}: {type(exc).__name__}: {exc}'
            )
            results[interface_id] = {
                'status': 'failed',
                'error_type': type(exc).__name__,
            }

    report = {
        'schema': 'cdeadmin.yugabytedb-dual-interface-live-gate.v1',
        'engine_id': 'yugabytedb',
        'expected_runtime': EXPECTED_SERVER,
        'interfaces_required': ['ysql', 'ycql'],
        'request_routing': 'exact-profile-no-cross-interface-fallback',
        'same_runtime_host': args.host,
        'mutation_performed': True,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'interfaces': results,
        'failures': failures,
        'passed': not failures,
        'duration_seconds': round(time.time() - started, 3),
    }
    return report


def main():
    args = parser().parse_args()
    try:
        report = verify(args)
    except Exception as exc:
        report = {
            'schema': 'cdeadmin.yugabytedb-dual-interface-live-gate.v1',
            'engine_id': 'yugabytedb', 'passed': False,
            'failures': [f'setup: {type(exc).__name__}: {exc}'],
        }
    encoded = json.dumps(report, indent=2, sort_keys=True) + '\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding='utf-8')
    print(encoded, end='')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
