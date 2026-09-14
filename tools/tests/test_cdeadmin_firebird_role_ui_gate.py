##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

from pathlib import Path

from tools.cdeadmin_firebird_role_ui_gate import arguments


def test_database_label_is_not_consumed_as_database_path_abbreviation():
    options, profiles = arguments([
        '--profiles', '/fixtures/profiles.json',
        '--database-path', '/var/lib/firebird/data/demo.fdb',
        '--database', 'demo.fdb', '--engine', 'Firebird',
        '--engine-id', 'firebird', '--interface-id', 'firebird-native',
        '--reference-version', '5.0.4', '--server', 'localhost',
        '--url', 'http://127.0.0.1:5052', '--output-root', '/evidence',
        '--summary-output', '/evidence/summary.json',
    ])
    assert options.database == 'demo.fdb'
    assert options.database_path == '/var/lib/firebird/data/demo.fdb'
    assert profiles == Path('/fixtures/profiles.json')
