/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import { styled } from '@mui/material/styles';
import gettext from 'sources/gettext';
import _ from 'lodash';
import PropTypes from 'prop-types';
import pgAdmin from 'sources/pgadmin';
import CDEadminLogo from './CDEadminLogo';
import { Link } from '@mui/material';
import url_for from 'sources/url_for';


const Root = styled('div')(({theme}) => ({
  background: theme.palette.grey[400],
  overflow: 'hidden',
  padding: '8px',
  display: 'flex',
  flexDirection: 'column',
  flexGrow: 1,
  height: '100%',
  '& .WelcomeDashboard-dashboardContainer': {
    paddingBottom: '8px',
    minHeight: '100%',

    '& .WelcomeDashboard-row': {
      marginRight: '-8px',
      marginLeft: '-8px'
    },
    '& .WelcomeDashboard-cardColumn': {
      flex: '0 0 100%',
      maxWidth: '100%',
      margin: '8px',

      '& .WelcomeDashboard-card': {
        position: 'relative',
        minWidth: 0,
        wordWrap: 'break-word',
        backgroundColor: theme.otherVars.tableBg,
        backgroundClip: 'border-box',
        border: '1px solid' + theme.otherVars.borderColor,
        borderRadius: theme.shape.borderRadius,
        marginTop: 8,

        '& .WelcomeDashboard-cardHeader': {
          padding: '0.25rem 0.5rem',
          fontWeight: 'bold',
          backgroundColor: theme.otherVars.tableBg,
          borderBottom: '1px solid',
          borderBottomColor: theme.otherVars.borderColor,
        },
        '& .WelcomeDashboard-cardBody': {
          flex: '1 1 auto',
          minHeight: '1px',
          padding: '0.5rem !important',

          '& .WelcomeDashboard-welcomeLogo': {
            width: '400px',
            '& .app-name': {
              fill: theme.otherVars.colorBrand
            },
            '& .app-name-underline': {
              stroke: theme.palette.text.primary
            },
            '& .app-tagline': {
              fill: theme.palette.text.primary
            }
          },

          '& .WelcomeDashboard-rowContent': {
            display: 'flex',
            flexWrap: 'wrap',
            marginRight: '-7.5px',
            marginLeft: '-7.5px',

            '& .WelcomeDashboard-dashboardLink': {
              color: theme.palette.text.primary + ' !important',
              flex: '0 0 50%',
              maxWidth: '50%',
              textAlign: 'center',
              cursor: 'pointer',

              '& .WelcomeDashboard-link': {
                color: theme.palette.text.primary + ' !important',

                '& .WelcomeDashboard-dashboardIcon': {
                  color: theme.otherVars.colorBrand
                }
              },
            },

            '& .WelcomeDashboard-gettingStartedLink': {
              flex: '0 0 25%',
              maxWidth: '50%',
              textAlign: 'center',
              cursor: 'pointer',

              '& .WelcomeDashboard-link': {
                color: theme.palette.text.primary + ' !important',

                '& .WelcomeDashboard-dashboardIcon': {
                  color: theme.otherVars.colorBrand
                }
              },
            },
          },
        },
      },
    },
  },
}));


function AddNewServer(pgBrowser) {
  if (pgBrowser?.tree) {
    let i = _.isUndefined(pgBrowser.tree.selected()) ?
        pgBrowser.tree.first(null, false) :
        pgBrowser.tree.selected(),
      serverModule = pgAdmin.Browser.Nodes.server,
      itemData = pgBrowser.tree.itemData(i);

    while (itemData && itemData._type != 'server_group') {
      i = pgBrowser.tree.next(i);
      itemData = pgBrowser.tree.itemData(i);
    }

    if (!itemData) {
      return;
    }

    if (serverModule) {
      serverModule.callbacks.show_obj_properties.apply(
        serverModule, [{
          action: 'create',
        }, i]
      );
    }
  }
}

export default function WelcomeDashboard({ pgBrowser }) {
  return (
    <Root>
      <div className='WelcomeDashboard-dashboardContainer'>
        <div className='WelcomeDashboard-row'>
          <div className='WelcomeDashboard-cardColumn'>
            <div className='WelcomeDashboard-card'>
              <div className='WelcomeDashboard-cardHeader'>{gettext('Welcome')}</div>
              <div className='WelcomeDashboard-cardBody'>
                <div className='WelcomeDashboard-welcomeLogo'>
                  <CDEadminLogo />
                </div>
                <h4>
                  {gettext('Multi-engine')} | {gettext('Multi-model')}{' '}
                  | {gettext('Open Source')}{' '}
                </h4>
                <p>
                  {gettext(
                    'CDEadmin is an independent hard fork of pgAdmin 4 for administering ScratchBird and independently managed database engines. Its provider-driven workspaces support relational, document, graph, key-value, analytic and distributed data systems without treating an emulated endpoint differently from its native counterpart.'
                  )}
                </p>
              </div>
            </div>
          </div>
        </div>
        <div className='WelcomeDashboard-row'>
          <div className='WelcomeDashboard-cardColumn'>
            <div className='WelcomeDashboard-card'>
              <div className='WelcomeDashboard-cardHeader'>{gettext('Quick Links')}</div>
              <div className='WelcomeDashboard-cardBody'>
                <div className='WelcomeDashboard-rowContent'>
                  <div className='WelcomeDashboard-dashboardLink'>
                    <Link onClick={() => { AddNewServer(pgBrowser); }} className='WelcomeDashboard-link'>
                      <div className='WelcomeDashboard-dashboardIcon'>
                        <span
                          className="fa fa-4x fa-server"
                          aria-hidden="true"
                        ></span>
                      </div>
                      {gettext('Add New Server')}
                    </Link>
                  </div>
                  <div className='WelcomeDashboard-dashboardLink'>
                    <Link onClick={() => pgAdmin.Preferences.show()} className='WelcomeDashboard-link'>
                      <div className='WelcomeDashboard-dashboardIcon'>
                        <span
                          id="mnu_preferences"
                          className="fa fa-4x fa-cogs"
                          aria-hidden="true"
                        ></span>
                      </div>
                      {gettext('Configure CDEadmin')}
                    </Link>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className='WelcomeDashboard-row'>
          <div className='WelcomeDashboard-cardColumn'>
            <div className='WelcomeDashboard-card'>
              <div className='WelcomeDashboard-cardHeader'>{gettext('Getting Started')}</div>
              <div className='WelcomeDashboard-cardBody'>
                <div className='WelcomeDashboard-rowContent'>
                  <div className='WelcomeDashboard-gettingStartedLink'>
                    <a
                      href={url_for('help.static', {filename: 'index.html'})}
                      target="cdeadmin_help"
                      className='WelcomeDashboard-link'
                    >
                      <div className='WelcomeDashboard-dashboardIcon'>
                        <span
                          className="fa fa-4x dashboard-cdeadmin-doc"
                          aria-hidden="true"
                        ></span>
                      </div>
                      {gettext('CDEadmin Documentation')}
                    </a>
                  </div>
                  <div className='WelcomeDashboard-gettingStartedLink'>
                    <a href="https://www.pgadmin.org" target="upstream_pgadmin" className='WelcomeDashboard-link'>
                      <div className='WelcomeDashboard-dashboardIcon'>
                        <span
                          className="fa fa-4x fa-globe"
                          aria-hidden="true"
                        ></span>
                      </div>
                      {gettext('Upstream pgAdmin Project')}
                    </a>
                  </div>
                  <div className='WelcomeDashboard-gettingStartedLink'>
                    <a
                      href={url_for('help.static', {filename: 'cdeadmin_hard_fork.html'})}
                      target="cdeadmin_status"
                      className='WelcomeDashboard-link'
                    >
                      <div className='WelcomeDashboard-dashboardIcon'>
                        <span
                          className="fa fa-4x fa-book"
                          aria-hidden="true"
                        ></span>
                      </div>
                      {gettext('Hard-fork Status')}
                    </a>
                  </div>
                  <div className='WelcomeDashboard-gettingStartedLink'>
                    <a
                      href={url_for('help.static', {filename: 'licence.html'})}
                      target="cdeadmin_licence"
                      className='WelcomeDashboard-link'
                    >
                      <div className='WelcomeDashboard-dashboardIcon'>
                        <span
                          className="fa fa-4x fa-users"
                          aria-hidden="true"
                        ></span>
                      </div>
                      {gettext('Licence and Attribution')}
                    </a>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Root>
  );
}


WelcomeDashboard.propTypes = {
  pgBrowser: PropTypes.object.isRequired
};
