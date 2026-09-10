/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import { useState, useRef, useEffect, useLayoutEffect } from 'react';
import gettext from 'sources/gettext';
import { Box } from '@mui/material';
import { DefaultButton, PrimaryButton } from '../components/Buttons';
import CloseIcon from '@mui/icons-material/CloseRounded';
import CheckRoundedIcon from '@mui/icons-material/CheckRounded';
import PropTypes from 'prop-types';
import { FormFooterMessage, InputCheckbox, InputText, MESSAGE_TYPE } from '../components/FormComponents';
import {
  DialogContent as ModalContent,
  DialogFooter as ModalFooter,
} from 'sources/cdeadmin_ui/overlays/DialogLayout';

export default function ConnectServerContent({closeModal, data, onOK, setHeight, hideSavePassword=false}) {

  const containerRef = useRef();
  const firstEleRef = useRef();
  const okBtnRef = useRef();
  const [formData, setFormData] = useState({
    tunnel_password: '',
    save_tunnel_password: false,
    password: '',
    save_password: false,
    connect_as: '',
    use_alternate_user: false,
  });
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [tunnelPasswordVisible, setTunnelPasswordVisible] = useState(false);

  const onTextChange = (e, id) => {
    let val = e;
    if(e?.target) {
      val = e.target.value;
    }
    setFormData((prev)=>({...prev, [id]: val}));
  };

  const onKeyDown = (e) => {
    // If enter key is pressed then click on OK button
    if (e.key === 'Enter') {
      okBtnRef.current?.click();
    }
  };


  useLayoutEffect(()=>{
    firstEleRef.current?.focus();
  }, []);

  useEffect(()=>{
    setHeight?.(containerRef.current?.offsetHeight);
  }, [containerRef.current]);

  if(!data) {
    return <>No data</>;
  }

  return (
    <ModalContent ref={containerRef}>
      <Box flexGrow="1" p={2}>
        {data.prompt_tunnel_password && <>
          <Box>
            <span style={{fontWeight: 'bold'}}>
              {data.tunnel_identity_file ?
                gettext('Please enter the SSH Tunnel password for the identity file \'%s\' to connect the server "%s"', data.tunnel_identity_file, data.tunnel_host)
                : gettext('Please enter the SSH Tunnel password for the user \'%s\' to connect the server "%s"', data.tunnel_username, data.tunnel_host)
              }
            </span>
          </Box>
          <Box marginTop='12px'>
            <InputText inputRef={firstEleRef}
              type={tunnelPasswordVisible ? 'text' : 'password'} value={formData['tunnel_password']} controlProps={{maxLength:null, autoComplete:'new-password'}}
              onChange={(e)=>onTextChange(e, 'tunnel_password')} onKeyDown={(e)=>onKeyDown(e)} />
            <DefaultButton data-test="toggle-tunnel-password-visibility"
              onClick={() => setTunnelPasswordVisible(!tunnelPasswordVisible)}>
              {tunnelPasswordVisible ? gettext('Hide password') :
                gettext('Show password')}
            </DefaultButton>
          </Box>
          <Box marginTop='12px' marginBottom='12px' visibility={hideSavePassword ? 'hidden' : 'unset'}>
            <InputCheckbox controlProps={{label: gettext('Save Password')}} value={formData['save_tunnel_password']}
              onChange={(e)=>onTextChange(e.target.checked, 'save_tunnel_password')} disabled={!data.allow_save_tunnel_password} />
          </Box>
        </>}
        {data.prompt_password && <>
          {data.allow_user_override && <Box marginBottom='12px'>
            <InputCheckbox controlProps={{label: gettext('Connect as a different user')}}
              value={formData.use_alternate_user} onChange={(event) => {
                const checked = event.target.checked;
                setFormData((previous) => ({...previous,
                  use_alternate_user: checked,
                  connect_as: checked ? previous.connect_as : '',
                  save_password: checked ? false : previous.save_password,
                }));
              }} />
            {formData.use_alternate_user && <Box marginTop='12px'>
              <Box marginBottom='4px'>{gettext('Alternate user or principal')}</Box>
              <InputText value={formData.connect_as}
                controlProps={{maxLength: 255, autoComplete: 'username'}}
                onChange={(event) => onTextChange(event, 'connect_as')}
                onKeyDown={(event) => onKeyDown(event)} />
            </Box>}
          </Box>}
          <Box>
            <span style={{fontWeight: 'bold'}}>
              {(formData.use_alternate_user ? formData.connect_as : data.username) ?
                gettext('Please enter the password for the user \'%s\' to connect the server - "%s"', formData.use_alternate_user ? formData.connect_as : data.username, data.server_label)
                : gettext('Please enter the password for the user to connect the server - "%s"', data.server_label)
              }
            </span>
          </Box>
          <Box marginTop='12px'>
            <InputText inputRef={(ele)=>{
              if(!data.prompt_tunnel_password) {
                /* Set only if no tunnel password asked */
                firstEleRef.current = ele;
              }
            }} type={passwordVisible ? 'text' : 'password'} value={formData['password']} controlProps={{maxLength:null, autoComplete: 'current-password'}}
            onChange={(e)=>onTextChange(e, 'password')} onKeyDown={(e)=>onKeyDown(e)}/>
            <DefaultButton data-test="toggle-password-visibility"
              onClick={() => setPasswordVisible(!passwordVisible)}>
              {passwordVisible ? gettext('Hide password') :
                gettext('Show password')}
            </DefaultButton>
          </Box>
          <Box marginTop='12px' visibility={hideSavePassword ? 'hidden' : 'unset'}>
            <InputCheckbox controlProps={{label: gettext('Save Password')}} value={formData['save_password']}
              onChange={(e)=>onTextChange(e.target.checked, 'save_password')}
              disabled={!data.allow_save_password || formData.use_alternate_user} />
          </Box>
        </>}
        <FormFooterMessage type={MESSAGE_TYPE.ERROR} message={_.escape(data.errmsg)} closable={false} style={{
          position: 'unset', padding: '12px 0px 0px'
        }}/>
      </Box>
      <ModalFooter>
        <DefaultButton data-test="close" startIcon={<CloseIcon />} onClick={()=>{
          closeModal();
        }} >{gettext('Cancel')}</DefaultButton>
        {(data.prompt_password || data.prompt_tunnel_password) && <>
          <PrimaryButton ref={okBtnRef} data-test="save" startIcon={<CheckRoundedIcon />} onClick={()=>{
            let postFormData = new FormData();
            if(data.prompt_tunnel_password) {
              postFormData.append('tunnel_password', formData.tunnel_password);
              formData.save_tunnel_password &&
                postFormData.append('save_tunnel_password', formData.save_tunnel_password);
            }
            if(data.prompt_password) {
              postFormData.append('password', formData.password);
              if(formData.use_alternate_user && formData.connect_as.trim()) {
                postFormData.append('connect_as', formData.connect_as.trim());
              }
              formData.save_password && !formData.use_alternate_user &&
                postFormData.append('save_password', formData.save_password);
            }
            onOK?.(postFormData);
            closeModal();
          }} >{gettext('OK')}</PrimaryButton>
        </>}
      </ModalFooter>
    </ModalContent>
  );
}

ConnectServerContent.propTypes = {
  closeModal: PropTypes.func,
  data: PropTypes.object,
  onOK: PropTypes.func,
  setHeight: PropTypes.func,
  hideSavePassword: PropTypes.bool
};
