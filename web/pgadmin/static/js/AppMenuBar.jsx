/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////
import { Box } from '@mui/material';
import { styled } from '@mui/material/styles';
import { useEffect } from 'react';
import { PrimaryButton } from './components/Buttons';
import { PgMenu, PgMenuDivider, PgMenuItem, PgSubMenu } from './components/Menu';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import AccountCircleRoundedIcon from '@mui/icons-material/AccountCircleRounded';
import { usePgAdmin } from '../../static/js/PgAdminProvider';
import { useForceUpdate } from './custom_hooks';


const StyledBox = styled(Box)(({theme}) => ({
  height: '30px',
  backgroundColor: theme.palette.primary.main,
  color: theme.palette.primary.contrastText,
  padding: '0 0.5rem',
  display: 'flex',
  alignItems: 'center',
  '& .AppMenuBar-logo': {
    width: '112px',
    height: '100%',
    backgroundPositionY: 'center',
    background: 'none',
    display: 'flex',
    alignItems: 'center',
    fontSize: '1.05rem',
    fontWeight: 700,
    letterSpacing: '0.03em',
  },
  '& .AppMenuBar-menus': {
    display: 'flex',
    alignItems: 'center',
    gap: '2px',
    marginLeft: '16px',

    '& .MuiButton-containedPrimary': {
      padding: '1px 8px',
    }
  },
  '& .AppMenuBar-userMenu': {
    marginLeft: 'auto',
    '& .MuiButton-containedPrimary': {
      fontSize: '0.825rem',
    },
    '& .AppMenuBar-gravatar': {
      marginRight: '4px',
    }
  },
}));



export default function AppMenuBar() {

  const forceUpdate = useForceUpdate();
  const pgAdmin = usePgAdmin();

  useEffect(()=>{
    pgAdmin.Browser.Events.on('pgadmin:enable-disable-menu-items', _.debounce(()=>{
      forceUpdate();
    }, 100));
    pgAdmin.Browser.Events.on('pgadmin:refresh-app-menu', _.debounce(()=>{
      forceUpdate();
    }, 100));
  }, []);

  const getPgMenuItem = (menuItem, i)=>{
    if(menuItem.type == 'separator') {
      return <PgMenuDivider key={i}/>;
    }
    const hasCheck = typeof menuItem.checked == 'boolean';

    return <PgMenuItem
      key={i}
      disabled={menuItem.isDisabled}
      onClick={()=>{
        menuItem.callback();
        if(hasCheck) {
          forceUpdate();
        }
      }}
      hasCheck={hasCheck}
      checked={menuItem.checked}
      closeOnCheck={true}
      shortcut={menuItem.shortcut}
    >{menuItem.label}</PgMenuItem>;
  };

  const userMenuInfo = pgAdmin.Browser.utils.userMenuInfo;

  const getPgMenu = (menu)=>{
    return menu.getMenuItems()?.map((menuItem, i)=>{
      const submenus = menuItem.getMenuItems();
      if(submenus) {
        return <PgSubMenu key={menuItem.label} label={menuItem.label}>
          {getPgMenu(menuItem)}
        </PgSubMenu>;
      }
      return getPgMenuItem(menuItem, i);
    });
  };

  return (
    <StyledBox data-test="app-menu-bar">
      <div className='AppMenuBar-logo' aria-label='CDEadmin'>CDEadmin</div>
      <div className='AppMenuBar-menus'>
        {pgAdmin.Browser.MainMenus?.map((menu)=>{
          return (
            <PgMenu
              menuButton={<PrimaryButton key={menu.label} data-label={menu.label}>{menu.label}<KeyboardArrowDownIcon fontSize="small" /></PrimaryButton>}
              label={menu.label}
              key={menu.name}
            >
              {getPgMenu(menu)}
            </PgMenu>
          );
        })}
      </div>
      {userMenuInfo &&
        <div className='AppMenuBar-userMenu'>
          <PgMenu
            menuButton={
              <PrimaryButton data-test="loggedin-username">
                <div className='AppMenuBar-gravatar'>
                  {userMenuInfo.gravatar &&
                  <img src={userMenuInfo.gravatar} width = "18" height = "18"
                    alt ={`Gravatar for ${ userMenuInfo.username }`} />}
                  {!userMenuInfo.gravatar && <AccountCircleRoundedIcon />}
                </div>
                { userMenuInfo.username } ({userMenuInfo.auth_source})
                <KeyboardArrowDownIcon fontSize="small" />
              </PrimaryButton>
            }
            label={userMenuInfo.username}
            align="end"
          >
            {userMenuInfo.menus.map((menuItem, i)=>{
              return getPgMenuItem(menuItem, i);
            })}
          </PgMenu>
        </div>}
    </StyledBox>
  );
}
