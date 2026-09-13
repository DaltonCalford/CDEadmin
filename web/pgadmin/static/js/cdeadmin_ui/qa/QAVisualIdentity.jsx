/////////////////////////////////////////////////////////////
// React installation and login control for visual QA identity.
/////////////////////////////////////////////////////////////

import {FormControlLabel, Switch} from '@mui/material';
import PropTypes from 'prop-types';
import {useEffect, useState} from 'react';
import gettext from 'sources/gettext';
import {QA_VISUAL_MODE_EVENT, QAVisualIdentityController, readQAVisualMode,
  requestQAVisualMode} from './VisualIdentity';

export function QAVisualIdentityBoundary() {
  useEffect(() => {
    const controller = new QAVisualIdentityController();
    controller.start();
    return () => controller.stop();
  }, []);
  return null;
}

export function QAVisualIdentityToggle({storage=window.localStorage}) {
  const [enabled, setEnabled] = useState(() => readQAVisualMode(storage));
  useEffect(() => {
    const update = (event) => setEnabled(Boolean(event.detail?.enabled));
    window.addEventListener(QA_VISUAL_MODE_EVENT, update);
    return () => window.removeEventListener(QA_VISUAL_MODE_EVENT, update);
  }, []);
  return <FormControlLabel data-cdeadmin-qa-key="security.login.qa-mode"
    control={<Switch checked={enabled} onChange={(event) => {
      const next = event.target.checked; setEnabled(next);
      requestQAVisualMode(next);
    }} slotProps={{input: {
      'aria-label': gettext('Enable QA visual element IDs'),
    }}} />}
    label={gettext('QA mode — show visual element IDs on hover')} />;
}

QAVisualIdentityToggle.propTypes = {storage: PropTypes.object};
