"""Stored buffers are not inferred from attachment/monitoring allocation."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import _resources
from pgadmin.cdeadmin.providers.firebird.attachment_cache import (
    stored_page_buffers,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('value', [None, True, False, -1, '64', 64.0,
                                   2147483647, [], {}])
def test_non_native_stored_values_are_unavailable_not_coerced(value):
    info = SimpleNamespace(get_info=Mock(return_value=value))
    with pytest.raises(RelationalClientError):
        stored_page_buffers(info)
    info.get_info.assert_called_once_with(61)


@pytest.mark.parametrize('stored', [0, 64, None, 'denied'])
def test_catalog_distinguishes_stored_buffers_and_unavailable_observations(
        stored):
    cursor = Mock()
    cursor.fetchall.return_value = []
    get_info = Mock(return_value=stored)
    if stored == 'denied':
        get_info.side_effect = RuntimeError('private-native-secret-canary')
    info = SimpleNamespace(page_cache_size=128, get_info=get_info)
    values = _resources(SimpleNamespace(cursor=lambda: cursor, info=info), {})
    database = next(item['native'] for item in values if
                    item['resource_kind'] == 'database')
    assert database['page_cache_size'] == 128
    get_info.assert_called_once_with(61)
    if stored in (0, 64):
        assert database['stored_page_buffers'] == stored
        assert database['information_observations']['stored_page_buffers'] == {
            'available': True}
    else:
        assert database['stored_page_buffers'] is None
        assert database['information_observations'][
            'stored_page_buffers']['available'] is False
        assert 'private-native-secret' not in str(database)
    cursor.close.assert_called_once()
