/////////////////////////////////////////////////////////////
// CDEadmin - Multi-engine Database Administration
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import gettext from 'sources/gettext';
import {Alert, Box} from '@mui/material';
import FirebirdServiceObservation from './FirebirdServiceObservation';
import FirebirdLimboObservation from './FirebirdLimboObservation';

export default function ProviderAdministrationResult({result, title, target}) {
  if (!result) return null;
  const observation = result.provider_result?.driver_observation;
  return <>
    {target?.resource_id && <Box component="p" sx={{mt: 2, overflowWrap: 'anywhere'}}
      aria-label={gettext('Provider response target')}>
      {title} — {target.display_name || target.resource_kind} [{target.resource_id}]
    </Box>}
    {observation?.service_release?.service_handle_released === false &&
      <Alert severity="warning" sx={{mt: 2}}
        aria-label={gettext('Firebird service cleanup required')}>
        {gettext('Firebird service handle release is unconfirmed. Do not replay the operation. Review its returned outcome separately from cleanup, then explicitly close the provider connection to retry handle release.')}
      </Alert>}
    {(result.workspace_follow_up || []).filter((item) =>
      item.state === 'failed' && typeof item.message === 'string'
    ).map((item, index) => <Alert key={`${item.action}-${index}`}
      severity="warning" sx={{mt: 2}}
      aria-label={gettext('Connection registration follow-up required')}>
      {item.message}
    </Alert>)}
    <Alert severity="info" sx={{mt: 2}}>
      {gettext('The provider response was recorded. Finality remains provider-owned; review the returned state and any required post-state validation.')}
    </Alert>
    {observation?.schema === 'cdeadmin.firebird-limbo-result.v1' ?
      <FirebirdLimboObservation observation={observation} /> :
      observation?.schema === 'cdeadmin.firebird-service-result.v1' ?
        <FirebirdServiceObservation title={title} observation={observation} /> :
        <Box component="pre" aria-label={gettext('Provider operation result')}
          sx={{mt: 1, p: 1, overflow: 'auto', maxHeight: 320,
            bgcolor: 'background.default'}}>
          {JSON.stringify(result.provider_result ?? result, null, 2)}
        </Box>}
  </>;
}

ProviderAdministrationResult.propTypes = {
  result: PropTypes.object,
  title: PropTypes.string,
  target: PropTypes.object,
};
