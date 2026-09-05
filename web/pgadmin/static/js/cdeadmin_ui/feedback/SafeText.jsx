/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';

export function SafeText({text, ...rest}) {
  return <span
    style={{whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      overflowWrap: 'anywhere'}}
    {...rest}
  >
    {text ?? ''}
  </span>;
}

SafeText.propTypes = {
  text: PropTypes.node,
};
