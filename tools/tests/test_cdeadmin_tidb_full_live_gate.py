##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Unit tests for the exact TiDB full-live gate."""

import tomllib

import tools.cdeadmin_tidb_full_live_gate as gate
from tools.cdeadmin_tidb_full_live_gate import (
    IMAGES,
    _current_tso,
    _write_pd_config,
    _write_tidb_config,
)


def test_full_live_gate_uses_every_exact_tidb_image():
    assert IMAGES == (
        'pingcap/pd:v8.5.6',
        'pingcap/tikv:v8.5.6',
        'pingcap/tidb:v8.5.6',
        'pingcap/tiflash:v8.5.6',
        'pingcap/ticdc:v8.5.6',
        'pingcap/br:v8.5.6',
    )


def test_generated_single_node_configs_are_valid_toml(tmp_path):
    (tmp_path / 'logs').mkdir()

    pd_config = tomllib.loads(_write_pd_config(tmp_path).read_text())
    tidb_config = tomllib.loads(_write_tidb_config(tmp_path).read_text())

    assert pd_config['replication']['max-replicas'] == 1
    assert tidb_config['split-table'] is False
    assert tidb_config['log']['slow-query-file'] == str(
        tmp_path / 'logs' / 'tidb-slow.log'
    )


def test_current_tso_is_captured_inside_an_explicit_transaction(monkeypatch):
    statements = []

    class Cursor:
        def execute(self, source):
            statements.append(source)

        @staticmethod
        def fetchone():
            return (468850799973761026,)

        @staticmethod
        def close():
            return None

    class Connection:
        @staticmethod
        def cursor():
            return Cursor()

        @staticmethod
        def close():
            return None

    monkeypatch.setattr(gate, '_connect', lambda _port: Connection())

    assert _current_tso(4000) == '468850799973761026'
    assert statements == [
        'BEGIN', 'SELECT TIDB_CURRENT_TSO()', 'ROLLBACK',
    ]
