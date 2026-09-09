/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import LoginImageUrl from '../../img/login.svg?url';

export default function LoginIllustration() {
  return <img
    src={LoginImageUrl}
    alt=""
    aria-hidden="true"
    style={{
      display: 'block',
      height: '100%',
      width: '100%',
      objectFit: 'contain',
    }}
  />;
}
