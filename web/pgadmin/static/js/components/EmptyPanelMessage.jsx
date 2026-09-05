/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {EmptyState} from '../cdeadmin_ui/feedback/EmptyState';

export default function EmptyPanelMessage({text, style}) {

  return (
    <EmptyState message={text} style={style} />
  );
}
EmptyPanelMessage.propTypes = {
  text: PropTypes.string,
  style: PropTypes.object,
};
