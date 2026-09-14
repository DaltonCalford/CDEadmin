##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Use an isolated real configuration database for registration failures."""

import sys
from types import ModuleType
from unittest.mock import Mock, patch

import pytest
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.endpoints.service import EndpointService


@pytest.fixture
def configuration():
    app = Flask('owned-registration-test')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    db = SQLAlchemy(app)
    model = ModuleType('pgadmin.model')
    model.db = db
    with app.app_context(), patch.dict(sys.modules, {'pgadmin.model': model}):
        db.session.execute(text(
            'CREATE TABLE target '
            '(id INTEGER PRIMARY KEY, name TEXT NOT NULL)'))
        db.session.execute(text("INSERT INTO target VALUES (1, 'original')"))
        db.session.commit()
        yield db
        db.session.remove()
        db.engine.dispose()


@pytest.mark.parametrize('failure', [None, 'body', 'constraint', 'commit'])
def test_configuration_transaction_rolls_back_and_keeps_session_usable(
        configuration, failure):
    db = configuration
    commit = db.session.commit
    if failure == 'commit':
        replacement = Mock(side_effect=RuntimeError('configuration commit'))
    else:
        replacement = commit

    def edit():
        with EndpointService._database_catalog_transaction():
            db.session.execute(text("UPDATE target SET name = 'changed'"))
            if failure == 'constraint':
                db.session.execute(text(
                    "INSERT INTO target VALUES (1, 'dup')"))
            if failure == 'body':
                raise RuntimeError('configuration write')

    with patch.object(db.session, 'commit', replacement):
        if failure:
            expected = IntegrityError if failure == 'constraint' else (
                RuntimeError)
            with pytest.raises(expected):
                edit()
        else:
            edit()
    assert db.session.execute(text('SELECT name FROM target')).all() == [
        ('original' if failure else 'changed',)]
    # A failed write must not leave a poisoned ORM session for later requests.
    db.session.execute(text("INSERT INTO target VALUES (2, 'next')"))
    db.session.commit()
    count = db.session.execute(text('SELECT COUNT(*) FROM target')).scalar()
    assert count == 2


def test_postcommit_catalog_read_failure_does_not_undo_persisted_registration(
        configuration):
    db = configuration
    with EndpointService._database_catalog_transaction():
        db.session.execute(text("UPDATE target SET name = 'committed'"))
    with pytest.raises(RuntimeError):
        raise RuntimeError('catalog observation unavailable')
    db.session.rollback()
    assert db.session.execute(text('SELECT name FROM target')).all() == [
        ('committed',)]
