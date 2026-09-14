##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

from unittest.mock import Mock, patch

from tools.reference_engine_demos import seed_demo


def test_supplied_seed_connection_never_connects_to_default_demo():
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.fetchone.return_value = None
    with patch('firebird.driver.connect') as connect, \
            patch('firebird.driver.create_database') as create:
        result = seed_demo.seed_firebird(
            connection=connection, database_path='/owned/fixture.fdb')
    connect.assert_not_called()
    create.assert_not_called()
    connection.close.assert_not_called()
    cursor.close.assert_called_once()
    assert result['database'] == '/owned/fixture.fdb'
    assert connection.commit.call_count > 0


def test_default_seed_still_owns_and_closes_its_attachment():
    connection = Mock()
    connection.cursor.return_value.fetchone.return_value = None
    with patch('firebird.driver.connect', return_value=connection) as connect:
        result = seed_demo.seed_firebird()
    connect.assert_called_once()
    assert connect.call_args.args[0].endswith('/cdeadmin_demo.fdb')
    connection.close.assert_called_once()
    assert result['database'].endswith('/cdeadmin_demo.fdb')
