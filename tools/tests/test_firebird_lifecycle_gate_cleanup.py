##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import cdeadmin_firebird_database_lifecycle_ui_gate as gate


@pytest.mark.parametrize('drop_succeeds', [False, True])
def test_partial_lifecycle_evidence_and_cleanup_are_not_discarded(
        monkeypatch, drop_succeeds):
    monkeypatch.setattr(gate, '_create', Mock())
    drop = Mock(return_value=drop_succeeds)
    monkeypatch.setattr(gate, '_drop', drop)
    monkeypatch.setattr(gate, '_wait_target', Mock())
    monkeypatch.setattr(gate.shared, '_open_form', Mock(
        side_effect=[None, RuntimeError('edit form unavailable')]))
    monkeypatch.setattr(gate.shared, '_submit_target_form', Mock())
    monkeypatch.setattr(gate.shared, '_capture_completed', Mock())
    monkeypatch.setattr(gate.shared, '_close', Mock())
    evidence, cleanup = [], {}
    options = SimpleNamespace(database_root='/owned', database='demo.fdb')
    with pytest.raises(RuntimeError, match='edit form unavailable'):
        gate._complete_cases(None, None, options, None, 'unused-test-secret',
                             evidence, cleanup)
    assert [item['mode'] for item in evidence] == ['connect']
    assert cleanup['native_create_requested'] is False
    drop.assert_called_once()
    path = drop.call_args.args[-1]
    assert path == cleanup['registered_database']
    if drop_succeeds:
        assert cleanup['database_files_removed'] == [path]
        assert cleanup['unverified_paths'] == []
    else:
        assert cleanup['unverified_paths'] == [path]
        assert cleanup['database_files_removed'] == []
