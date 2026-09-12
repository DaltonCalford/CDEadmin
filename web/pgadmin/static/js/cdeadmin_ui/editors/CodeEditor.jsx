/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import ReactCodeMirror from '../../components/ReactCodeMirror';
import {ProgressOverlay} from '../feedback/ProgressOverlay';
import {ValidationMessage} from '../feedback/Indicators';

/* Public editor boundary; CodeMirror remains a private implementation. */
export default function CodeEditor({loading=false, error='', label='Code editor',
  ...props}) {
  return <Box data-code-editor role="region" aria-label={label}
    aria-busy={loading || undefined}
    sx={{position: 'relative', minHeight: 120, height: '100%'}}>
    {error && <ValidationMessage status="error">{error}</ValidationMessage>}
    <ReactCodeMirror {...props} />
    <ProgressOverlay message={loading ? 'Loading editor' : ''} />
  </Box>;
}

CodeEditor.propTypes = {
  loading: PropTypes.bool,
  error: PropTypes.node,
  label: PropTypes.string,
};
