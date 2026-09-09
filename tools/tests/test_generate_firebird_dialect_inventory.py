##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exact Firebird dialect inventory generator tests."""

import os
from pathlib import Path

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_inventory import (
    generate,
)


SOURCE_ROOT = Path(os.environ.get(
    'CDEADMIN_FIREBIRD_SOURCE_ROOT', '/nonexistent/firebird-5.0.4-source'
))


def test_firebird_inventory_covers_every_pinned_token_and_production():
    if not SOURCE_ROOT.is_dir():
        pytest.skip('preserved Firebird 5.0.4 source is unavailable')
    document = generate(SOURCE_ROOT)
    completeness = document['completeness']
    assert completeness['lexer_token_count'] == len(
        document['lexer_tokens']
    ) == 518
    assert completeness['grammar_production_count'] == len(
        document['grammar_productions']
    ) == 680
    assert completeness['grammar_alternative_count'] == sum(
        len(item['alternatives'])
        for item in document['grammar_productions']
    ) == 2223
    assert completeness['cdeadmin_generated_tasks_qualified'] is False


def test_firebird_inventory_contains_native_database_grammar_not_drop_sql():
    if not SOURCE_ROOT.is_dir():
        pytest.skip('preserved Firebird 5.0.4 source is unavailable')
    document = generate(SOURCE_ROOT)
    productions = {
        item['name']: item for item in document['grammar_productions']
    }
    alter_database = productions['db_alter_clause']['grammar_source']
    assert 'SET DEFAULT CHARACTER SET' in alter_database
    assert 'SET LINGER TO' in alter_database
    assert 'DROP LINGER' in alter_database
    drop_clause = productions['drop_clause']['grammar_source']
    assert 'DROP DATABASE' not in drop_clause
