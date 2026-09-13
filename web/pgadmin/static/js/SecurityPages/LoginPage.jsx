import { Box, Icon } from '@mui/material';
import { useState } from 'react';
import { InputSelectNonSearch, InputText, MESSAGE_TYPE, NotifierMessage } from '../components/FormComponents';
import BasePage, { SecurityButton } from './BasePage';
import LoginIllustration from './LoginIllustration';
import gettext from 'sources/gettext';
import PropTypes from 'prop-types';
import {QAVisualIdentityToggle} from 'sources/cdeadmin_ui/qa';

export default function LoginPage({userLanguage, langOptions, forgotPassUrl, csrfToken, loginUrl, authSources, authSourcesEnum, oauth2Config, loginBanner, ...props}) {
  const [form, setForm] = useState(({email: '', password: '', language: userLanguage}));

  // Hide login form if auth source is only oauth2 and/or kerberos. #5386
  const showLoginForm = !((authSources?.includes('oauth2') || authSources?.includes('kerberos')) && authSources?.length == 1 || (authSources?.includes('oauth2')
  && authSources?.includes('kerberos')) && authSources?.length == 2);

  const onTextChange = (n, val)=>{
    setForm((prev)=>({...prev, [n]: val}));
  };

  return (
    <>
      {loginBanner && <NotifierMessage showIcon={false} closable={false} type={MESSAGE_TYPE.ERROR} message={loginBanner} style={{
        position: 'absolute',
        width: '80%',
        top: '30px',
        left: 0,
        right: 0,
        marginRight: 'auto',
        marginLeft: 'auto'
      }} textCenter />}
      <BasePage title={gettext('Login')} pageImage={<LoginIllustration />} {...props}>
        <form data-cdeadmin-qa-key="security.login.form"
          style={{display:'flex', gap:'15px', flexDirection:'column'}}
          action={loginUrl} method="POST">
          <Box data-cdeadmin-qa-key="security.login.product-identity"
            textAlign="center">
            <Box component="strong" display="block">
              {gettext('ScratchRobin CDE Administration Console')}
            </Box>
            <Box component="span" display="block">
              {gettext('Alpha QA Release')}
            </Box>
          </Box>
          {showLoginForm &&
          <>
            <input name="csrf_token" defaultValue={csrfToken} hidden/>
            <InputText name="email" value={form.email} onChange={(v)=>onTextChange('email', v)} placeholder={gettext('Email Address / Username')} autoFocus
              controlProps={{autoComplete: null}} />
            <InputText name="password" value={form.password} onChange={(v)=>onTextChange('password', v)} type="password" placeholder={gettext('Password')}
              controlProps={{autoComplete: null}} />
            <Box textAlign="right" marginTop="-10px">
              <a style={{color: 'inherit'}} href={forgotPassUrl}>{gettext('Forgotten your password?')}</a>
            </Box>
            <InputSelectNonSearch name="language" options={langOptions} value={form.language} onChange={(v)=>onTextChange('language', v.target.value)} />
          </>
          }
          <QAVisualIdentityToggle />
          {showLoginForm &&
            <SecurityButton name="internal_button" value="Login" disabled={!(form.email && form.password)}>{gettext('Login')}</SecurityButton>
          }
          {authSources?.includes?.(authSourcesEnum.OAUTH2) &&
          oauth2Config.map((oauth)=>{
            // Allow the configured icon to specify its own Font Awesome style
            // (e.g. 'fas fa-key'). Default to the brands style ('fab') when
            // only an icon name is given, for backward compatibility.
            const iconStyles = ['fab', 'fas', 'far', 'fal', 'fat', 'fad',
              'fa-brands', 'fa-solid', 'fa-regular', 'fa-light', 'fa-thin', 'fa-duotone'];
            const hasStyle = oauth.OAUTH2_ICON?.split(/\s+/).some((c)=>iconStyles.includes(c));
            const iconClassName = hasStyle ? oauth.OAUTH2_ICON : 'fab '+oauth.OAUTH2_ICON;
            return (
              <SecurityButton key={oauth.OAUTH2_NAME} name="oauth2_button" value={oauth.OAUTH2_NAME} style={{backgroundColor: oauth.OAUTH2_BUTTON_COLOR}}>
                <Icon className={iconClassName} style={{ fontSize: '1.5em', marginRight: '8px' }} />{gettext('Login with %s', oauth.OAUTH2_DISPLAY_NAME)}
              </SecurityButton>
            );
          })
          }
        </form>
      </BasePage>
    </>
  );
}

LoginPage.propTypes = {
  userLanguage: PropTypes.string,
  langOptions: PropTypes.arrayOf(PropTypes.object),
  forgotPassUrl: PropTypes.string,
  csrfToken: PropTypes.string,
  loginUrl: PropTypes.string,
  authSources: PropTypes.arrayOf(PropTypes.string),
  authSourcesEnum: PropTypes.object,
  oauth2Config: PropTypes.arrayOf(PropTypes.object),
  loginBanner: PropTypes.string
};
