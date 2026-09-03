import { Box, Button, darken } from '@mui/material';
import { styled } from '@mui/material/styles';
import { useSnackbar } from 'notistack';
import { useEffect } from 'react';
import { MESSAGE_TYPE, NotifierMessage } from '../components/FormComponents';
import { FinalNotifyContent } from '../helpers/Notifier';
import PropTypes from 'prop-types';
import CustomPropTypes from '../custom_prop_types';

const StyledBox = styled(Box)(({theme}) => ({
  backgroundColor: theme.palette.primary.main,
  color: theme.palette.primary.contrastText,
  display: 'flex',
  justifyContent: 'center',
  height: '100%',
  '& .BasePage-pageContent': {
    display: 'flex',
    flexDirection: 'column',
    padding: '16px',
    backgroundColor: contentBg,
    borderRadius: theme.shape.borderRadius,
    maxHeight: '100%',
    minWidth: '450px',
    maxWidth: '450px',
    '& .BasePage-item': {
      display: 'flex',
      justifyContent: 'center',
      marginBottom: '15px',
      fontSize: '1.2rem',
      '& .BasePage-logo': {
        width: '140px',
        height: '40px',
        backgroundPositionY: 'center',
        background: 'none',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '1.4rem',
        fontWeight: 700,
        letterSpacing: '0.03em',
      },
    },
    '& .BasePage-button': {
      backgroundColor: loginBtnBg,
      color: '#fff',
      padding: '4px 8px',
      width: '100%',
      '&:hover': {
        backgroundColor: darken(loginBtnBg, 0.1),
      },
      '&.Mui-disabled': {
        opacity: 0.60,
        color: '#fff'
      },
    }
  },
}));

const contentBg = '#2b709b';
const loginBtnBg = '#038bba';

export function SecurityButton({...props}) {

  return <Button type="submit" className='BasePage-button' {...props} />;
}

export default function BasePage({pageImage, title,  children, messages}) {
  const snackbar = useSnackbar();
  useEffect(()=>{
    messages?.forEach((message)=>{
      let options = {
        autoHideDuration: null,
        content:(key)=>{
          if(Array.isArray(message[0])) message[0] = message[0][0];
          const type = Object.values(MESSAGE_TYPE).includes(message[0]) ? message[0] : MESSAGE_TYPE.INFO;
          return <FinalNotifyContent>
            <NotifierMessage type={type} message={message[1]} closable={true} onClose={()=>{snackbar.closeSnackbar(key);}} style={{maxWidth: '400px'}} />
          </FinalNotifyContent>;
        }
      };
      options.content.displayName = 'content';
      snackbar.enqueueSnackbar(options);
    });
  }, [messages]);
  return (
    <StyledBox >
      <Box display="flex" minWidth="80%" gap="40px" alignItems="center" padding="20px 80px">
        <Box flexGrow={1} height="80%" textAlign="center">
          {pageImage}
        </Box>
        <Box className='BasePage-pageContent'>
          <Box className='BasePage-item'>
            <div className='BasePage-logo' aria-label='CDEadmin'>CDEadmin</div>
          </Box>
          <Box className='BasePage-item'>{title}</Box>
          <Box display="flex" flexDirection="column" minHeight={0}>{children}</Box>
        </Box>
      </Box>
    </StyledBox>
  );
}

BasePage.propTypes = {
  pageImage: CustomPropTypes.children,
  title: PropTypes.string,
  children: CustomPropTypes.children,
  messages: PropTypes.arrayOf(PropTypes.array)
};
