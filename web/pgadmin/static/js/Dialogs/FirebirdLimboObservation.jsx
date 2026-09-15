/////////////////////////////////////////////////////////////
// ScratchRobin - native Firebird prepared transaction observations.
/////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import gettext from 'sources/gettext';
import {Alert, Box} from '@mui/material';

export default function FirebirdLimboObservation({observation}) {
  const inspect = observation.operation_id === 'inspect_limbo';
  const records = Array.isArray(observation.transactions) ? observation.transactions : [];
  const validRecords = records.filter((record) => record &&
    typeof record.transaction_id === 'string' && Array.isArray(record.participants));
  return <Box component="section" aria-label={gettext('Firebird prepared transactions')}
    sx={{mt: 1, minWidth: 0, overflowWrap: 'anywhere'}}>
    <Box component="h3" sx={{fontSize: '1em'}}>
      {gettext('Database: %s', observation.database || gettext('Not reported'))}
    </Box>
    <Alert severity="warning">
      {gettext('This observation concerns only the selected database. Stored participant paths are historical metadata; peer states and the global distributed outcome have not been verified. Do not reuse credentials for those paths automatically.')}
    </Alert>
    {observation.attachment_released !== true && <Alert severity="warning">
      {gettext('Attachment release is unconfirmed. Do not replay the recovery decision. Explicitly close the provider connection to retry cleanup.')}
    </Alert>}
    {inspect ? <>
      {(observation.inventory_complete !== true || validRecords.length !== records.length) &&
        <Alert severity="error">{gettext('The prepared transaction inventory is incomplete.')}</Alert>}
      {observation.inventory_complete === true && records.length === 0 &&
        <Box component="p">{gettext('No local prepared transactions were reported at observation time.')}</Box>}
      {validRecords.map((record) => <Box component="article" key={record.transaction_id}
        sx={{mt: 2, p: 1, border: 1, borderColor: 'divider'}}>
        <Box component="h4" sx={{fontSize: '1em', mt: 0}}>
          {gettext('Local transaction %s', record.transaction_id)}</Box>
        <Box component="p">{record.kind === 'distributed' ?
          gettext('Stored distributed transaction description is available.') :
          gettext('No stored distributed transaction description was reported.')}</Box>
        {record.host && <Box component="p">{gettext('Recorded coordinator host: %s', record.host)}</Box>}
        {record.native_length_limit_reached && <Alert severity="warning">
          {gettext('A native description field reached its 255-byte storage limit and may be truncated. Do not infer a complete endpoint from it.')}
        </Alert>}
        <Box component="ul" aria-label={gettext('Recorded participants')} sx={{pl: 3}}>
          {record.participants.map((participant, index) => <Box component="li"
            key={`${participant.transaction_id}-${index}`} sx={{mb: 1}}>
            {gettext('Database: %s', participant.database || gettext('Not reported'))}<br />
            {gettext('Transaction: %s', participant.transaction_id || gettext('Not reported'))}
          </Box>)}
        </Box>
      </Box>)}
    </> : <>
      <Box component="p">{observation.native_decision_returned === true ?
        gettext('The native decision call returned for this database participant.') :
        gettext('Native decision completion is unconfirmed. Inspect native state before any further decision.')}</Box>
      <Box component="p">{gettext('Requested decision: %s',
        observation.native_decision_requested === 'commit' ? gettext('Commit') :
          observation.native_decision_requested === 'rollback' ? gettext('Rollback') : gettext('Not reported'))}</Box>
    </>}
  </Box>;
}

FirebirdLimboObservation.propTypes = {observation: PropTypes.object.isRequired};
