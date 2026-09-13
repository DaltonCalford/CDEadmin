/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Derived from pgAdmin 4. Copyright (C) 2013 - 2026,
// The pgAdmin Development Team. PostgreSQL Licence.
//
//////////////////////////////////////////////////////////////

import ScratchRobinIcon from '../../../static/assets/cdeadmin/branding/scratchrobincde.svg?svgr';

export default function CDEadminLogo() {
  return (
    <div className="welcome-logo" aria-label="ScratchRobin CDE Administrator">
      <ScratchRobinIcon role="img" aria-label="ScratchRobin" />
      <div className="welcome-wordmark">
        <h1>ScratchRobin CDE Administrator</h1>
      </div>
    </div>
  );
}
