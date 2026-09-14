/////////////////////////////////////////////////////////////
// CDEadmin - provider-owned Firebird service observations.
/////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import gettext from 'sources/gettext';
import {Alert, Box} from '@mui/material';

export default function FirebirdServiceObservation({observation, title}) {
  const output = Array.isArray(observation.output) ?
    observation.output.filter((line) => typeof line === 'string') : [];
  const outputInvalid = !Array.isArray(observation.output) ||
    output.length !== observation.output.length;
  // Driver service readline includes native line endings. Joining with an
  // extra separator would double-space the actual gstat/gbak output.
  const lines = output.join('').split('\n');
  const release = observation.service_release?.service_handle_released;
  const fields = [
    [gettext('Operation'), title || observation.operation_id || gettext('Not reported')],
    [gettext('Database'), observation.database || gettext('Not reported')],
    [gettext('Native service call'), observation.server_completed === true ?
      gettext('Returned') : gettext('Completion not reported')],
    [gettext('Service attachment'), release === true ? gettext('Released') :
      release === false ? gettext('Release unconfirmed') : gettext('Not reported')],
  ];
  const selection = observation.backup_selection_requested;
  const retention = observation.history_retention_requested;
  if (selection !== undefined) {
    const guid = selection?.mode === 'guid' && typeof selection.guid === 'string';
    const level = selection?.mode === 'level' && Number.isInteger(selection.level) && selection.level >= 0;
    fields.push([gettext('Requested backup selection'), guid ?
      gettext('GUID: %s', selection.guid) : level ?
        gettext('Level: %s', selection.level) : gettext('Not reported')]);
  }
  if (retention !== undefined) {
    const valid = Number.isInteger(retention?.value) && retention.value > 0;
    const policy = valid && retention.unit === 'ROWS' ?
      gettext('Newest rows (timestamp cutoff): %s', retention.value) :
      valid && retention.unit === 'DAYS' ?
        gettext('Calendar days including today: %s', retention.value) : gettext('Not reported');
    fields.push([gettext('Requested backup-history retention'), policy]);
  }
  return <Box component="section" aria-label={gettext('Firebird service result')}
    sx={{mt: 1, p: 1, minWidth: 0, bgcolor: 'background.default'}}>
    <Box component="h3" sx={{mt: 0, fontSize: '1em'}}>
      {gettext('Firebird service result')}</Box>
    <Box component="dl" sx={{display: 'grid', gap: 1, m: 0,
      gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 16em), 1fr))'}}>
      {fields.map(([name, value]) => <Box key={name} sx={{minWidth: 0}}>
        <Box component="dt" sx={{fontWeight: 600}}>{name}</Box>
        <Box component="dd" sx={{m: 0, overflowWrap: 'anywhere'}}>{value}</Box>
      </Box>)}
    </Box>
    <Box component="p">
      {gettext('This is the returned native service observation, not independent verification of the resulting database state.')}
    </Box>
    {observation.output_truncated === true && <Alert severity="warning">
      {gettext('Native service output was truncated. The displayed text is incomplete.')}
    </Alert>}
    {outputInvalid && <Alert severity="warning">
      {gettext('Native output could not be displayed in full. Review the native service receipt.')}
    </Alert>}
    <Box component="h4" sx={{fontSize: '1em'}}>{gettext('Native output')}</Box>
    {output.length > 0 ? <Box component="pre"
      aria-label={gettext('Firebird native service output')}
      sx={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: '1em',
        maxHeight: 'min(20em, 25vh)', overflow: 'auto', m: 0}}>
      {lines.map((line, index) => <span key={index} data-firebird-output-line={index + 1}>
        {line}{index < lines.length - 1 ? '\n' : ''}</span>)}
    </Box> :
      <Box component="p">{gettext('No textual output was returned.')}</Box>}
    <Box component="details">
      <Box component="summary">{gettext('Native service receipt')}</Box>
      <Box component="pre" aria-label={gettext('Firebird native service receipt')}
        sx={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>
        {JSON.stringify(observation, null, 2)}</Box>
    </Box>
  </Box>;
}

FirebirdServiceObservation.propTypes = {
  observation: PropTypes.object.isRequired,
  title: PropTypes.string,
};
