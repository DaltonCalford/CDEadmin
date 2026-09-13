/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////



import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import Theme from '../../../pgadmin/static/js/Theme';
import LoginPage from '../../../pgadmin/static/js/SecurityPages/LoginPage';

describe('LoginPage', ()=>{

  beforeEach(() => window.localStorage.clear());
  afterEach(() => window.localStorage.clear());


  let ctrlMount = (props)=>{
    return render(<Theme>
      <LoginPage {...props}/>
    </Theme>);
  };

  it('internal', ()=>{
    const ctrl = ctrlMount({
      userLanguage: 'en',
      langOptions: [{
        label: 'English',
        value: 'en',
      }],
      forgotPassUrl: '/forgot/url',
      csrfToken: 'some-token',
      loginUrl: '/login/url',
      authSources: ['internal'],
      authSourcesEnum: {
        OAUTH2: 'oauth2',
        KERBEROS: 'kerberos'
      },
      oauth2Config: [],
      loginBanner: 'login banner'
    });
    expect(ctrl.container.querySelector('form').getAttribute('action')).toBe('/login/url');
    expect(ctrl.container.querySelector('input[name="email"]')).not.toBeNull();
    expect(ctrl.container.querySelector('input[name="password"]')).not.toBeNull();
    expect(ctrl.container.querySelector('img[aria-hidden="true"]'))
      .toHaveAttribute('src', '/static/test-content-hashed-asset.svg');
    expect(screen.getByText('ScratchRobin CDE Administration Console'))
      .toBeInTheDocument();
    expect(screen.getByText('Alpha QA Release')).toBeInTheDocument();
    expect(screen.getByLabelText('Enable QA visual element IDs'))
      .not.toBeChecked();
  });

  it('enables persistent visual QA identities from the login form', async ()=>{
    const ctrl = ctrlMount({userLanguage: 'en', langOptions: [{label: 'English',
      value: 'en'}], forgotPassUrl: '/forgot/url', csrfToken: 'some-token',
    loginUrl: '/login/url', authSources: ['internal'], authSourcesEnum: {
      OAUTH2: 'oauth2', KERBEROS: 'kerberos'}, oauth2Config: [], loginBanner: ''});
    fireEvent.click(screen.getByLabelText('Enable QA visual element IDs'));
    expect(window.localStorage.getItem(
      'cdeadmin.qa.visual-identities.enabled.v1')).toBe('true');
    await waitFor(() => expect(ctrl.container.querySelector('form'))
      .toHaveAttribute('data-cdeadmin-qa-id'));
    ctrl.unmount();
    ctrlMount({userLanguage: 'en', langOptions: [{label: 'English', value: 'en'}],
      forgotPassUrl: '/forgot/url', csrfToken: 'some-token',
      loginUrl: '/login/url', authSources: ['internal'], authSourcesEnum: {
        OAUTH2: 'oauth2', KERBEROS: 'kerberos'}, oauth2Config: [],
      loginBanner: ''});
    expect(screen.getByLabelText('Enable QA visual element IDs')).toBeChecked();
  });

  it('offers visual QA mode when internal authentication is unavailable', ()=>{
    const ctrl = ctrlMount({userLanguage: 'en', langOptions: [],
      forgotPassUrl: '/forgot/url', csrfToken: 'some-token',
      loginUrl: '/login/url', authSources: ['oauth2'], authSourcesEnum: {
        OAUTH2: 'oauth2', KERBEROS: 'kerberos'}, oauth2Config: [{
        OAUTH2_NAME: 'github', OAUTH2_BUTTON_COLOR: '#fff',
        OAUTH2_ICON: 'fa-github', OAUTH2_DISPLAY_NAME: 'Github'}],
      loginBanner: ''});
    expect(ctrl.container.querySelector('input[name="email"]')).toBeNull();
    expect(screen.getByLabelText('Enable QA visual element IDs'))
      .toBeInTheDocument();
  });

  it('oauth2', ()=>{
    const ctrl = ctrlMount({
      userLanguage: 'en',
      langOptions: [{
        label: 'English',
        value: 'en',
      }],
      forgotPassUrl: '/forgot/url',
      csrfToken: 'some-token',
      loginUrl: '/login/url',
      authSources: ['internal', 'oauth2'],
      authSourcesEnum: {
        OAUTH2: 'oauth2',
        KERBEROS: 'kerberos'
      },
      oauth2Config: [{
        OAUTH2_NAME: 'github',
        OAUTH2_BUTTON_COLOR: '#fff',
        OAUTH2_ICON: 'fa-github',
        OAUTH2_DISPLAY_NAME: 'Github'
      }],
      loginBanner: ''
    });
    expect(ctrl.container.querySelector('form').getAttribute('action')).toBe('/login/url');
    expect(ctrl.container.querySelector('input[name="email"]')).not.toBeNull();
    expect(ctrl.container.querySelector('input[name="password"]')).not.toBeNull();
    expect(ctrl.container.querySelector('button[name="oauth2_button"]')).toHaveValue('github');
  });
});
