/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Derived from pgAdmin 4. Copyright (C) 2013 - 2026,
// The pgAdmin Development Team. PostgreSQL Licence.
//
//////////////////////////////////////////////////////////////

export default function CDEadminLogo() {
  return (
    <div className="welcome-logo" aria-label="CDEadmin">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 130" role="img">
        <title>CDEadmin</title>
        <g fill="none" stroke="currentColor" strokeWidth="5">
          <ellipse cx="65" cy="28" rx="38" ry="15" />
          <path d="M27 28v54c0 8 17 15 38 15s38-7 38-15V28" />
          <path d="M27 55c0 8 17 15 38 15s38-7 38-15" />
          <circle cx="126" cy="28" r="8" fill="currentColor" />
          <circle cx="126" cy="82" r="8" fill="currentColor" />
          <path d="M103 40l17-8M103 72l17 8" />
        </g>
        <text x="155" y="72" className="app-name" fontSize="58" fontWeight="700">CDEadmin</text>
        <text x="158" y="104" className="app-tagline" fontSize="20">multi-engine • multi-model • open source</text>
      </svg>
    </div>
  );
}
