#!/usr/bin/env python3
"""Qualify conditional Dolt rebase catalogs in an owned disposable database."""
import argparse
import json
from pathlib import Path
import uuid

import mysql.connector
# Initialize the standalone provider imports.
import cdeadmin_catalog_qa_audit  # noqa: F401
from pgadmin.cdeadmin.providers.dolt.provider import _extras


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    name = 'cdeqa_rebase_' + uuid.uuid4().hex
    connection = mysql.connector.connect(
        host='127.0.0.1', port=53308, user='root',
        autocommit=True, ssl_disabled=True)
    cursor = connection.cursor()
    result = {'database': name, 'states': {}, 'sample_modified': False}
    created = False

    def execute(source):
        cursor.execute(source)
        if cursor.description:
            cursor.fetchall()
        while cursor.nextset():
            if cursor.description:
                cursor.fetchall()

    def observe():
        native = next(r['native'] for r in _extras(cursor, {}, 'qa')
                      if r['resource_kind'] == 'rebase')
        return {'plan_table_present': native['plan_table_present'],
                'plan_rows': len(native['plan'])}

    try:
        execute(f'CREATE DATABASE `{name}`')
        created = True
        execute(f'USE `{name}`')
        result['states']['initial'] = observe()
        execute('CREATE TABLE t (id INT PRIMARY KEY)')
        execute("CALL dolt_commit('-Am', 'QA base', "
                "'--author', 'CDEadmin QA <qa@example.invalid>')")
        execute("CALL dolt_checkout('-b', 'feature')")
        execute('INSERT INTO t VALUES (1)')
        execute("CALL dolt_commit('-am', 'QA feature', "
                "'--author', 'CDEadmin QA <qa@example.invalid>')")
        execute("CALL dolt_rebase('--interactive', 'main')")
        result['states']['interactive'] = observe()
        assert result['states']['interactive']['plan_table_present']
        assert result['states']['interactive']['plan_rows'] > 0
        execute("CALL dolt_rebase('--abort')")
        result['states']['aborted'] = observe()
        result['status'] = 'passed'
    except Exception as error:
        result.update(status='failed', error_type=type(error).__name__,
                      error=str(error))
    finally:
        if created:
            execute('USE information_schema')
            execute(f'DROP DATABASE `{name}`')
            result['owned_database_removed'] = True
        cursor.close()
        connection.close()
        args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
