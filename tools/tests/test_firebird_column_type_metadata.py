##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import columns
from pgadmin.cdeadmin.providers.firebird.column_type_metadata import (
    type_editor_values,
)


def test_alter_form_prefills_without_dropping_unchanged_type_attributes():
    fields = {item['field_id']: item for item in
              columns.form('alter', ADMINISTRATION._field)['fields']}
    assert fields['data_type'].get('default') is None
    assert fields['data_type']['require_explicit_choice'] is True
    for key in ('data_type', 'domain', 'length', 'precision', 'scale',
                'blob_subtype', 'segment_size', 'character_set', 'time_zone'):
        assert fields[key]['initial_value_path'] == ['type_editor', key]
        assert fields[key]['submit_unchanged'] is True
    assert fields['expression']['initial_value_path'] == ['computed_source']


@pytest.mark.parametrize('code,name', [
    (7, 'SMALLINT'), (8, 'INTEGER'), (16, 'BIGINT'), (26, 'INT128'),
    (10, 'FLOAT'), (12, 'DATE'), (23, 'BOOLEAN'), (27, 'DOUBLE PRECISION'),
])
def test_scalar_type_metadata(code, name):
    assert type_editor_values({'field_type': str(code)}) == {'data_type': name}


@pytest.mark.parametrize('subtype,name', [(1, 'NUMERIC'), (2, 'DECIMAL')])
@pytest.mark.parametrize('precision,scale', [(1, 0), (18, -3), (38, -38)])
def test_exact_fixed_precision(subtype, name, precision, scale):
    assert type_editor_values({'field_type': 26, 'field_sub_type': subtype,
                               'field_scale': scale,
                               'field_precision': precision}) == {
        'data_type': name, 'precision': precision, 'scale': -scale}


@pytest.mark.parametrize('field', [
    {}, {'field_type': True}, {'field_type': 999}, {'field_type': 11},
    {'field_type': 8, 'field_sub_type': 1},
    {'field_type': 8, 'field_sub_type': 1, 'field_precision': 0},
    {'field_type': 26, 'field_sub_type': 1, 'field_precision': 39},
    {'field_type': 8, 'field_sub_type': 1, 'field_precision': 9,
     'field_scale': -10},
    {'field_type': 8, 'field_sub_type': 1, 'field_precision': 9,
     'field_scale': 1},
    {'field_type': 27, 'field_scale': -2},
    {'field_type': 37, 'field_length': 80},
    {'field_type': 37, 'character_length': 'unknown'},
    {'field_type': 261, 'segment_length': 65536},
])
def test_incomplete_or_invalid_type_is_not_guessed(field):
    assert type_editor_values(field) == {}


@pytest.mark.parametrize('domain', ['rdb$domain', 'RDb$domain', 'Space Name'])
def test_user_domain_keeps_exact_identity(domain):
    assert type_editor_values({'domain': domain, 'field_type': 8}) == {
        'data_type': 'DOMAIN', 'domain': domain}


def test_text_uses_character_count_not_byte_count():
    assert type_editor_values({'field_type': 37, 'character_length': 20,
                               'field_length': 80,
                               'character_set': 'UTF8'}) == {
        'data_type': 'VARCHAR', 'length': 20, 'character_set': 'UTF8'}


@pytest.mark.parametrize('code,name', [(14, 'BINARY'), (37, 'VARBINARY')])
def test_binary_subtype_is_not_lost_as_plain_octets_text(code, name):
    assert type_editor_values({'field_type': code, 'field_sub_type': 1,
                               'character_length': 20,
                               'character_set': 'OCTETS'}) == {
        'data_type': name, 'length': 20}


@pytest.mark.parametrize('code,name,zone', [
    (13, 'TIME', 'WITHOUT TIME ZONE'), (28, 'TIME', 'WITH TIME ZONE'),
    (35, 'TIMESTAMP', 'WITHOUT TIME ZONE'),
    (29, 'TIMESTAMP', 'WITH TIME ZONE'),
])
def test_temporal_zone_is_explicit(code, name, zone):
    assert type_editor_values({'field_type': code}) == {
        'data_type': name, 'time_zone': zone}


@pytest.mark.parametrize('code,precision', [(24, 16), (25, 34)])
def test_decimal_float_precision(code, precision):
    assert type_editor_values({'field_type': code}) == {
        'data_type': 'DECFLOAT', 'precision': precision}


def test_text_blob_metadata_includes_segment_and_charset():
    assert type_editor_values({'field_type': 261, 'field_sub_type': 1,
                               'segment_length': 0,
                               'character_set': 'UTF8'}) == {
        'data_type': 'BLOB', 'blob_subtype': 1, 'segment_size': 0,
        'character_set': 'UTF8'}
