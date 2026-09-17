// Firebird attachment-local trap controls; never connection defaults.
import {useState} from 'react';
import PropTypes from 'prop-types';
import gettext from 'sources/gettext';
import {Alert, Box, Button, Checkbox, FormControlLabel} from '@mui/material';

export const FIREBIRD_TRAP_COMMANDS = {
  inspect: 'SELECT RDB$GET_CONTEXT(\'SYSTEM\', \'DECFLOAT_TRAPS\') AS DECFLOAT_TRAPS FROM RDB$DATABASE',
  disable: 'SET DECFLOAT TRAPS TO',
};

export default function FirebirdSessionTraps({sessionId, disabled, onExecute}) {
  const [confirmation, setConfirmation] = useState(null);
  const unavailable = disabled || !sessionId;
  return <Box component="fieldset" sx={{mt: 1, minWidth: 0}}
    aria-label={gettext('Firebird session DECFLOAT traps')}>
    <legend>{gettext('Session DECFLOAT traps')}</legend>
    <Alert severity="warning">
      {gettext('Disabling traps changes arithmetic error handling immediately for this query session. Commit and rollback do not restore the traps. Session reset restores attachment settings; reconnect uses connection preferences. This does not change saved preferences or other sessions.')}
    </Alert>
    {!sessionId && <Alert severity="info">
      {gettext('Open a query session first, using Run or Provider transaction state.')}
    </Alert>}
    <FormControlLabel control={<Checkbox disabled={unavailable}
      checked={!!sessionId && confirmation === sessionId}
      onChange={(event) => setConfirmation(event.target.checked ? sessionId : null)} />}
    label={gettext('Confirm disabling all DECFLOAT traps in this session')} />
    <Box sx={{display: 'flex', gap: 1, flexWrap: 'wrap'}}>
      <Button disabled={unavailable} onClick={() => onExecute(FIREBIRD_TRAP_COMMANDS.inspect)}>
        {gettext('Inspect DECFLOAT traps')}</Button>
      <Button color="warning" disabled={unavailable || confirmation !== sessionId}
        onClick={() => {
          setConfirmation(null);
          onExecute(FIREBIRD_TRAP_COMMANDS.disable);
        }}>{gettext('Disable all DECFLOAT traps')}</Button>
    </Box>
  </Box>;
}

FirebirdSessionTraps.propTypes = {
  sessionId: PropTypes.string,
  disabled: PropTypes.bool,
  onExecute: PropTypes.func.isRequired,
};
