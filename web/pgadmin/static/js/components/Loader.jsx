/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {ProgressOverlay} from '../cdeadmin_ui/feedback/ProgressOverlay';

export default function Loader({message, style, autoEllipsis=false, ...props}) {

  return <ProgressOverlay message={message} style={style}
    autoEllipsis={autoEllipsis} {...props} />;
}

Loader.propTypes = {
  message: PropTypes.string,
  style: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
  autoEllipsis: PropTypes.bool,
};
