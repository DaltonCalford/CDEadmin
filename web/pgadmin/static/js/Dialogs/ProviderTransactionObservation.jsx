/////////////////////////////////////////////////////////////
// CDEadmin - native transaction observations, not inferred finality.
/////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import gettext from 'sources/gettext';
import {Box} from '@mui/material';

function fieldValue(field, format=(value) => String(value)) {
  if(field?.available !== true || field.value === undefined || field.value === null) {
    return gettext('Unavailable');
  }
  return format(field.value);
}

export default function ProviderTransactionObservation({transaction, label}) {
  const native = transaction?.provider_payload;
  if(transaction?.transaction_model !== 'firebird-native-transaction' ||
      !native?.native_observation) {
    return <Box component="pre" aria-label={label}
      sx={{mt: 1, p: 1, maxHeight: 180, overflow: 'auto',
        bgcolor: 'background.default'}}>{JSON.stringify(transaction, null, 2)}</Box>;
  }
  const state = {
    idle: gettext('Idle — no active transaction'),
    active: gettext('Active transaction'), closed: gettext('Closed'),
    unknown: gettext('Transaction state unavailable'),
  }[native.state] || gettext('Transaction state unavailable');
  const fields = native.fields || {};
  const entries = [
    [gettext('State'), state],
  ];
  if(native.attachment_fields) {
    entries.push(
      [gettext('Client SQL dialect'), fieldValue(native.attachment_fields.client_sql_dialect)],
      [gettext('Stored database SQL dialect'), fieldValue(native.attachment_fields.database_sql_dialect)],
    );
  }
  if(native.state === 'active') {
    entries.push(
      [gettext('Transaction ID'), fieldValue(fields.transaction_id)],
      [gettext('Isolation'), fieldValue(fields.isolation)],
      [gettext('Access mode'), fieldValue(fields.read_only, (value) =>
        value === true ? gettext('Read only') :
          value === false ? gettext('Read/write') : gettext('Unavailable'))],
      [gettext('Lock wait'), fieldValue(fields.lock_timeout_seconds, (value) =>
        value === -1 ? gettext('Wait indefinitely') :
          value === 0 ? gettext('No wait') : gettext('%s seconds', value))],
      [gettext('Snapshot number'), fieldValue(fields.snapshot_number)],
      [gettext('Oldest interesting transaction at start'),
        fieldValue(fields.oldest_interesting_at_start)],
      [gettext('Oldest active transaction at start'),
        fieldValue(fields.oldest_active_at_start)],
      [gettext('Oldest snapshot transaction at start'),
        fieldValue(fields.oldest_snapshot_at_start)],
    );
  }
  return <Box component="section" aria-label={label}
    sx={{mt: 1, p: 1, bgcolor: 'background.default', color: 'text.primary'}}>
    <Box component="h3" sx={{mt: 0, fontSize: '1em'}}>
      {gettext('Firebird transaction')}</Box>
    <Box component="dl" sx={{display: 'grid', gap: 1, m: 0,
      gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 16em), 1fr))'}}>
      {entries.map(([name, value]) => <Box key={name} sx={{minWidth: 0}}>
        <Box component="dt" sx={{fontWeight: 600}}>{name}</Box>
        <Box component="dd" sx={{m: 0, overflowWrap: 'anywhere'}}>{value}</Box>
      </Box>)}
    </Box>
    <Box component="p">
      {gettext('Observed from the retained Firebird session. Inspection does not start, commit or roll back a transaction.')}
    </Box>
    {native.attachment_fields && <Box component="p">
      {gettext('Client SQL dialect controls statement interpretation; stored database SQL dialect is a separate database property. Inspection changes neither.')}
    </Box>}
    <Box component="details">
      <Box component="summary">{gettext('Native observation details')}</Box>
      <Box component="pre" sx={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>
        {JSON.stringify(transaction, null, 2)}</Box>
    </Box>
  </Box>;
}

ProviderTransactionObservation.propTypes = {
  transaction: PropTypes.object.isRequired,
  label: PropTypes.string,
};
