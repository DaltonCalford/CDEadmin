/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {styled} from '@mui/material/styles';
import gettext from 'sources/gettext';
import PropTypes from 'prop-types';
import CDEadminLogo from './CDEadminLogo';
import url_for from 'sources/url_for';
import {Icon} from 'sources/cdeadmin_ui/icons';
import {useWorkbenchActivities} from 'sources/cdeadmin_ui/shell/WorkbenchShell';

const Root = styled('div')(({theme}) => ({
  background: theme.palette.grey[400],
  overflow: 'auto',
  padding: 'clamp(8px, 1.5vw, 20px)',
  height: '100%',
  '& .WelcomeDashboard-dashboardContainer': {
    width: 'min(1280px, 100%)',
    margin: '0 auto',
  },
  '& .WelcomeDashboard-card': {
    minWidth: 0,
    wordWrap: 'break-word',
    backgroundColor: theme.otherVars.tableBg,
    border: `1px solid ${theme.otherVars.borderColor}`,
    borderRadius: theme.shape.borderRadius,
    marginBottom: 'clamp(10px, 1.5vw, 20px)',
  },
  '& .WelcomeDashboard-cardHeader': {
    padding: '0.45rem 0.75rem',
    fontWeight: 'bold',
    borderBottom: `1px solid ${theme.otherVars.borderColor}`,
  },
  '& .WelcomeDashboard-cardBody': {
    padding: 'clamp(12px, 2vw, 24px)',
  },
  '& .WelcomeDashboard-welcomeLogo .welcome-logo': {
    display: 'flex',
    alignItems: 'center',
    gap: 'clamp(14px, 2vw, 28px)',
    '& svg': {
      width: 'clamp(72px, 10vw, 128px)',
      height: 'clamp(72px, 10vw, 128px)',
      flex: '0 0 auto',
    },
    '& h1': {
      color: theme.otherVars.colorBrand,
      fontSize: 'clamp(1.6rem, 3.5vw, 3.25rem)',
      lineHeight: 1.08,
      margin: 0,
    },
  },
  '& .WelcomeDashboard-description': {
    fontSize: 'clamp(1rem, 1.35vw, 1.2rem)',
    margin: '16px 0 0',
  },
  '& .WelcomeDashboard-launchGrid': {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(min(170px, 100%), 1fr))',
    gap: 'clamp(10px, 1.5vw, 18px)',
  },
  '& .WelcomeDashboard-launchTile': {
    appearance: 'none',
    minWidth: 0,
    minHeight: '132px',
    padding: '16px 10px',
    border: `1px solid ${theme.otherVars.borderColor}`,
    borderRadius: theme.shape.borderRadius,
    background: theme.palette.background.default,
    color: `${theme.palette.text.primary} !important`,
    font: 'inherit',
    fontWeight: 600,
    textDecoration: 'none !important',
    textAlign: 'center',
    cursor: 'pointer',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '10px',
    overflowWrap: 'anywhere',
    '&:hover:not(:disabled)': {
      borderColor: theme.palette.primary.main,
      background: theme.palette.action.hover,
    },
    '&:focus-visible': {
      outline: `2px solid ${theme.palette.primary.main}`,
      outlineOffset: '2px',
    },
    '&:disabled': {
      cursor: 'not-allowed',
      opacity: 0.5,
    },
    '& svg, & img, & i': {
      width: 'clamp(48px, 6vw, 72px)',
      height: 'clamp(48px, 6vw, 72px)',
      fontSize: 'clamp(48px, 6vw, 72px)',
      flex: '0 0 auto',
    },
  },
  '@media (max-width: 560px)': {
    '& .WelcomeDashboard-welcomeLogo .welcome-logo': {
      alignItems: 'flex-start',
      flexDirection: 'column',
    },
  },
}));

function ActivityLaunchers() {
  const {activities, activate} = useWorkbenchActivities();
  return <div className="WelcomeDashboard-launchGrid"
    data-cdeadmin-qa-key="dashboard.welcome.workspace-launchers">
    {activities.map((activity) => <button type="button"
      className="WelcomeDashboard-launchTile" key={activity.id}
      disabled={activity.disabled === true}
      title={activity.disabledReason || activity.label}
      onClick={() => activate(activity.id)}>
      <Icon iconKey={activity.iconKey || 'command.default'} decorative />
      <span>{activity.label}</span>
    </button>)}
  </div>;
}

function DocumentationLaunchers() {
  return <div className="WelcomeDashboard-launchGrid"
    data-cdeadmin-qa-key="dashboard.welcome.documentation-launchers">
    <a href={url_for('help.static', {filename: 'index.html'})}
      target="scratchrobin_help" role="link"
      data-cdeadmin-qa-key="dashboard.welcome.scratchrobin-documentation"
      className="WelcomeDashboard-launchTile">
      <Icon iconKey="tool.scratchrobin" decorative />
      <span>{gettext('ScratchRobin Documentation')}</span>
    </a>
    <a href="https://github.com/scratchbird-software-inc/ScratchBird"
      target="scratchbird_help" rel="noopener noreferrer"
      data-cdeadmin-qa-key="dashboard.welcome.scratchbird-documentation"
      className="WelcomeDashboard-launchTile">
      <Icon iconKey="engine.scratchbird" decorative />
      <span>{gettext('ScratchBird Documentation')}</span>
    </a>
  </div>;
}

export default function WelcomeDashboard({pgBrowser: _pgBrowser}) {
  return <Root>
    <div className="WelcomeDashboard-dashboardContainer">
      <section className="WelcomeDashboard-card" aria-labelledby="welcome-title">
        <div className="WelcomeDashboard-cardHeader">{gettext('Welcome')}</div>
        <div className="WelcomeDashboard-cardBody">
          <div className="WelcomeDashboard-welcomeLogo" id="welcome-title">
            <CDEadminLogo />
          </div>
          <p className="WelcomeDashboard-description">
            {gettext('A data management and business intelligence tool. A part of the ScratchBird CDE family of products.')}
          </p>
        </div>
      </section>
      <section className="WelcomeDashboard-card"
        aria-labelledby="workspace-launcher-title">
        <div className="WelcomeDashboard-cardHeader" id="workspace-launcher-title">
          {gettext('Workspaces')}
        </div>
        <div className="WelcomeDashboard-cardBody">
          <ActivityLaunchers />
        </div>
      </section>
      <section className="WelcomeDashboard-card"
        aria-labelledby="documentation-launcher-title">
        <div className="WelcomeDashboard-cardHeader"
          id="documentation-launcher-title">
          {gettext('Documentation')}
        </div>
        <div className="WelcomeDashboard-cardBody">
          <DocumentationLaunchers />
        </div>
      </section>
    </div>
  </Root>;
}

WelcomeDashboard.propTypes = {
  pgBrowser: PropTypes.object.isRequired,
};
