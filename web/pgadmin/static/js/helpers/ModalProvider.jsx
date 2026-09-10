/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import { Box, Dialog, DialogContent, DialogTitle, Paper } from '@mui/material';
import React, { useState, useMemo, useRef } from 'react';
import { getEpoch } from 'sources/utils';
import { DefaultButton, PgIconButton, PrimaryButton } from '../components/Buttons';
import Draggable from 'react-draggable';
import CloseIcon from '@mui/icons-material/CloseRounded';
import DeleteIcon from '@mui/icons-material/Delete';
import CustomPropTypes from '../custom_prop_types';
import PropTypes from 'prop-types';
import gettext from 'sources/gettext';
import HTMLReactParser from 'html-react-parser';
import DOMPurify from 'dompurify';
import { SafeMessage } from '../components/SafeMessage';
import CheckRoundedIcon from '@mui/icons-material/CheckRounded';
import { Rnd } from 'react-rnd';
import { ExpandDialogIcon, MinimizeDialogIcon, DisconnectedIcon } from '../components/ExternalIcon';
import { styled } from '@mui/material/styles';

export const ModalContext = React.createContext({});
const MIN_HEIGHT = 190;
const MIN_WIDTH = 500;
const StyledBox = styled(Box)(({theme}) => ({
  '& .Alert-footer': {
    display: 'flex',
    justifyContent: 'flex-end',
    padding: '0.5rem',
    ...theme.mixins.panelBorder.top,
  },
  '& .Alert-margin': {
    marginLeft: '0.25rem',
  },
}));

const buttonIconMap = {
  disconnect: <DisconnectedIcon />,
  default: <CheckRoundedIcon />
};

export function useModal() {
  return React.useContext(ModalContext);
}

export function AlertContent({ text, confirm, okLabel = gettext('OK'), cancelLabel = gettext('Cancel'), onOkClick, onCancelClick, okIcon = 'default', plainText = false}) {
  const body = (typeof text === 'string')
    ? (plainText
      ? <SafeMessage text={text} />
      : HTMLReactParser(DOMPurify.sanitize(text)))
    : text;
  return (
    <StyledBox display="flex" flexDirection="column" height="100%">
      <Box flexGrow="1" p={2} whiteSpace='pre-line'>{body}</Box>
      <Box className='Alert-footer'>
        {confirm &&
          <DefaultButton startIcon={<CloseIcon />} onClick={onCancelClick}>{cancelLabel}</DefaultButton>
        }
        <PrimaryButton className='Alert-margin' startIcon={buttonIconMap[okIcon]} onClick={onOkClick} autoFocus>{okLabel}</PrimaryButton>
      </Box>
    </StyledBox>
  );
}
AlertContent.propTypes = {
  text: PropTypes.string,
  confirm: PropTypes.bool,
  onOkClick: PropTypes.func,
  onCancelClick: PropTypes.func,
  okLabel: PropTypes.string,
  cancelLabel: PropTypes.string,
  okIcon : PropTypes.string,
  plainText: PropTypes.bool,
};

function alert(title, text, onOkClick, okLabel = gettext('OK'), options = {}) {
  const {plainText = false} = options;
  // bind the modal provider before calling
  this.showModal(title, (closeModal) => {
    const onOkClickClose = () => {
      onOkClick?.();
      closeModal();
    };
    return (
      <AlertContent text={text} onOkClick={onOkClickClose} okLabel={okLabel} plainText={plainText} />
    );
  });
}

function confirm(title, text, onOkClick, onCancelClick, okLabel = gettext('Yes'), cancelLabel = gettext('No'), okIcon = 'default', modalId=null) {
  // bind the modal provider before calling
  this.showModal(title, (closeModal) => {
    const onOkClickClose = () => {
      onOkClick?.();
      closeModal();
    };
    return (
      <AlertContent text={text} confirm onOkClick={onOkClickClose} onCancelClick={closeModal} okLabel={okLabel} cancelLabel={cancelLabel} okIcon={okIcon}/>
    );
  }, {id: modalId, onClose: onCancelClick});
}

function confirmDelete(title, text, onDeleteClick, onCancelClick, deleteLabel = gettext('Delete'), cancelLabel = gettext('Cancel')) {
  this.showModal(
    title,
    (closeModal)=>{
      const handleOkClose = (callback) => {
        callback?.();
        closeModal();
      };
      return (
        <StyledBox display="flex" flexDirection="column" height="100%">
          <Box flexGrow="1" p={2}>
            {typeof (text) == 'string' ? HTMLReactParser(DOMPurify.sanitize(text)) : text}
          </Box>
          <Box className='Alert-footer'>
            <DefaultButton className='Alert-margin' startIcon={<CloseIcon />} onClick={() => handleOkClose(onCancelClick)} autoFocus>{cancelLabel}</DefaultButton>
            <DefaultButton className='Alert-margin' color={'error'} startIcon={<DeleteIcon/> } onClick={() => handleOkClose(onDeleteClick)}>{deleteLabel}</DefaultButton>
          </Box>
        </StyledBox>
      );
    },
    { isFullScreen: false, isResizeable: false, showFullScreen: false, isFullWidth: false, showTitle: true},
  );
}

export default function ModalProvider({ children }) {
  const [modals, setModals] = React.useState([]);
  const showModal = (title, content, modalOptions) => {
    let id = getEpoch().toString() + crypto.getRandomValues(new Uint8Array(4));
    if(modalOptions?.id){
      id = modalOptions.id;
    }
    setModals((prev) => {
      if(prev?.find(modal=> modal.id === modalOptions?.id)){
        return prev;
      }
      return [...prev, {
        id: id,
        title: title,
        content: content,
        ...modalOptions,
      }];});
  };

  const closeModal = (id) => {
    setModals((prev) => {
      return prev.filter((o) => o.id != id);
    });
  };

  const fullScreenModal = (fullScreen) => {
    setModals((prev) => [...prev, {
      fullScreen: fullScreen,
    }]);
  };

  const modalContextBase = {
    showModal: showModal,
    closeModal: closeModal,
    fullScreenModal: fullScreenModal
  };
  const modalContext = React.useMemo(() => ({
    ...modalContextBase,
    confirm: confirm.bind(modalContextBase),
    alert: alert.bind(modalContextBase),
    confirmDelete: confirmDelete.bind(modalContextBase)
  }), []);
  return (
    <ModalContext.Provider value={modalContext}>
      {children}
      {modals.map((modalOptions) => (
        <ModalContainer key={modalOptions.id} {...modalOptions} />
      ))}
    </ModalContext.Provider>
  );
}

ModalProvider.propTypes = {
  children: CustomPropTypes.children,
};

const StyledRnd = styled(Rnd)(({theme}) => ({
  '&.Dialog-content': {
    // Rnd's x/y coordinates are viewport coordinates.  A relative flex item
    // receives the MUI dialog container's centering offset as well, which can
    // place an otherwise clamped dialog below a short viewport.
    position: 'fixed !important',
    top: '0 !important',
    left: '0 !important',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    border: '1px solid ' + theme.otherVars.inputBorderColor,
    borderRadius: theme.shape.borderRadius,
  },
  '&.Dialog-fullScreen': {
    transform: 'none !important'
  },
}));


function checkIsResizable(props) {
  return props.isresizeable == 'true';
}

function setEnableResizing(props, resizeable) {
  return props.isfullscreen == 'true' ? false : resizeable;
}

export function viewportDialogGeometry({
  viewportWidth, viewportHeight, width, height,
  minWidth=MIN_WIDTH, minHeight=MIN_HEIGHT,
}) {
  const horizontalMargin = Math.min(16, viewportWidth * 0.02);
  const verticalMargin = Math.min(16, viewportHeight * 0.02);
  const maximumWidth = Math.max(1, viewportWidth - horizontalMargin * 2);
  const maximumHeight = Math.max(1, viewportHeight - verticalMargin * 2);
  const resolvedWidth = Math.min(width || MIN_WIDTH, maximumWidth);
  const resolvedHeight = Math.min(height || MIN_HEIGHT, maximumHeight);
  return {
    width: resolvedWidth,
    height: resolvedHeight,
    minWidth: Math.min(minWidth || MIN_WIDTH, maximumWidth),
    minHeight: Math.min(minHeight || MIN_HEIGHT, maximumHeight),
    maxWidth: maximumWidth,
    maxHeight: maximumHeight,
    x: Math.max(horizontalMargin, (viewportWidth - resolvedWidth) / 2),
    y: verticalMargin,
  };
}

export function availableDialogViewport({
  windowWidth, windowHeight, containerBounds, applicationTop=0,
}) {
  // A disabled-portal MUI dialog is positioned in the dialog container's
  // coordinate system.  The container can begin below both the application
  // menu and a workspace toolbar, so using the menu edge alone overstates the
  // usable height on short windows.  Clamp against the actual container top
  // as well as its declared bounds.
  const containerTop = Math.max(0, containerBounds?.top || 0);
  const contentTop = Math.max(0, applicationTop, containerTop);
  const width = containerBounds ? Math.min(
    containerBounds.width,
    Math.max(1, windowWidth - Math.max(0, containerBounds.left)),
  ) : windowWidth;
  const windowHeightBelowContent = Math.max(1, windowHeight - contentTop);
  const containerHeight = containerBounds ? Math.max(
    1,
    containerBounds.height - Math.max(0, contentTop - containerTop),
  ) : windowHeightBelowContent;
  return {
    width,
    height: Math.min(windowHeightBelowContent, containerHeight),
  };
}

export function modalTitleId(id) {
  const safeId = String(id || 'dialog').replace(/[^A-Za-z0-9_-]/g, '-');
  return `cdeadmin-modal-title-${safeId}`;
}

export function modalAccessibilityAttributes(id, title, showTitle=true) {
  const textualTitle = typeof title === 'string' && title.trim() ?
    title.trim() : null;
  return {
    'aria-label': textualTitle || undefined,
    'aria-labelledby': !textualTitle && showTitle ? modalTitleId(id) :
      undefined,
  };
}

function PaperComponent({minHeight, minWidth, ...props}) {
  let [dialogPosition, setDialogPosition] = useState(null);
  let resizeable = checkIsResizable(props);
  const nodeRef = useRef(null);
  const dialogContainers = document.querySelectorAll('.MuiDialog-container');
  const dialogContainer = dialogContainers[dialogContainers.length - 1];
  const containerBounds = dialogContainer?.getBoundingClientRect();
  const applicationMenu = document.querySelector('[data-test="app-menu-bar"]');
  const applicationTop = Math.max(
    0, applicationMenu?.getBoundingClientRect().bottom || 0,
  );
  const availableViewport = availableDialogViewport({
    windowWidth: window.innerWidth,
    windowHeight: window.innerHeight,
    containerBounds,
    applicationTop,
  });
  const geometry = viewportDialogGeometry({
    viewportWidth: availableViewport.width,
    viewportHeight: availableViewport.height,
    width: props.width,
    height: props.height,
    minWidth,
    minHeight,
  });

  const setConditionalPosition = () => {
    return props.isfullscreen == 'true' ? { x: 0, y: 0 } : dialogPosition && { x: dialogPosition.x, y: dialogPosition.y };
  };

  return (
    props.isresizeable == 'true' ?
      <StyledRnd
        size={props.isfullscreen == 'true' && { width: '100%', height: '100%' }}
        className={'Dialog-content ' + ( props.isfullscreen == 'true' ? 'Dialog-fullScreen' : '')}
        default={{
          x: geometry.x,
          y: geometry.y,
          width: geometry.width,
          height: geometry.height,
        }}
        minWidth={props.isfullscreen == 'true' ? 0 : geometry.minWidth}
        minHeight={props.isfullscreen == 'true' ? 0 : geometry.minHeight}
        maxWidth={props.isfullscreen == 'true' ? undefined : geometry.maxWidth}
        maxHeight={props.isfullscreen == 'true' ? undefined : geometry.maxHeight}
        bounds="window"
        enableResizing={setEnableResizing(props, resizeable)}
        position={setConditionalPosition()}
        onDragStop={(e, position) => {
          if (props.isfullscreen !== 'true') {
            setDialogPosition({
              ...position,
            });
          }
        }}
        onResize={(e, direction, ref, delta, position) => {
          setDialogPosition({
            ...position,
          });
        }}
        dragHandleClassName="modal-drag-area"
      >
        <Paper {...props} style={{
          width: '100%', height: '100%', maxHeight: '100%', maxWidth: '100%',
          margin: 0,
        }} />
      </StyledRnd>
      :
      <Draggable nodeRef={nodeRef} cancel={'[class*="MuiDialogContent-root"]'}>
        <Paper {...props} ref={nodeRef} style={{ minWidth: '600px' }} />
      </Draggable>
  );
}

PaperComponent.propTypes = {
  isfullscreen: PropTypes.string,
  isresizeable: PropTypes.string,
  width: PropTypes.number,
  height: PropTypes.number,
  minWidth: PropTypes.number,
  minHeight: PropTypes.number,
};

const StyleDialog = styled(Dialog)(({theme}) => ({
  '&.Modal-resizeable .MuiDialog-container': {
    alignItems: 'flex-start',
    justifyContent: 'flex-start',
  },
  '& .Modal-container': {
    backgroundColor: theme.palette.background.default
  },
  '& .Modal-titleBar': {
    display: 'flex',
    flexGrow: 1
  },
  '& .Modal-icon': {
    fill: 'currentColor',
    width: '1em',
    height: '1em',
    display: 'inline-block',
    fontSize: '1.5rem',
    transition: 'none',
    flexShrink: 0,
    userSelect: 'none',
  },
  '& .Modal-footer': {
    display: 'flex',
    justifyContent: 'flex-end',
    padding: '0.5rem',
    ...theme.mixins.panelBorder?.top,
  },
  '& .Modal-iconButtonStyle': {
    marginLeft: 'auto',
    marginRight: '4px'
  },
}));

function ModalContainer({ id, title, content, dialogHeight, dialogWidth, onClose, fullScreen = false, isFullWidth = false, showFullScreen = false, isResizeable = false, minHeight = MIN_HEIGHT, minWidth = MIN_WIDTH, showTitle=true, ...props }) {
  let useModalRef = useModal();
  const titleId = modalTitleId(id);
  const accessibilityAttributes = modalAccessibilityAttributes(
    id, title, showTitle);
  let closeModal = (_e, reason) => {
    if(reason == 'backdropClick' && showTitle) {
      return;
    }
    useModalRef.closeModal(id);
    if(reason == 'escapeKeyDown' || reason == undefined) {
      onClose?.();
    }
  };
  const [isFullScreen, setIsFullScreen] = useState(fullScreen);

  return (
    <StyleDialog
      className={isResizeable ? 'Modal-resizeable' : undefined}
      open={true}
      onClose={closeModal}
      PaperComponent={PaperComponent}
      slotProps={{
        paper: {
          'isfullscreen': isFullScreen.toString(),
          'isresizeable': isResizeable.toString(),
          width: dialogWidth,
          height: dialogHeight,
          minHeight: minHeight,
          minWidth: minWidth,
          ...accessibilityAttributes,
        },
      }}
      fullScreen={isFullScreen}
      fullWidth={isFullWidth}
      disablePortal
      {...accessibilityAttributes}
      {...props}
    >
      { showTitle &&
        <DialogTitle id={titleId} className='modal-drag-area'>
          <Box className='Modal-titleBar'>
            <Box sx={{ marginRight:'0.25rem', flexGrow: 1}}>{title}</Box>
            {
              showFullScreen && !isFullScreen &&
                <Box className='Modal-iconButtonStyle'><PgIconButton title={gettext('Maximize')} icon={<ExpandDialogIcon className='Modal-icon' />} size="xs" noBorder onClick={() => { setIsFullScreen(!isFullScreen); }} /></Box>
            }
            {
              showFullScreen && isFullScreen &&
                <Box className='Modal-iconButtonStyle'><PgIconButton title={gettext('Minimize')} icon={<MinimizeDialogIcon  className='Modal-icon' />} size="xs" noBorder onClick={() => { setIsFullScreen(!isFullScreen); }} /></Box>
            }

            <Box marginLeft="auto"><PgIconButton title={gettext('Close')} icon={<CloseIcon  />} size="xs" noBorder onClick={closeModal} /></Box>
          </Box>
        </DialogTitle>
      }
      <DialogContent height="100%">
        {useMemo(()=>{ return content(closeModal); }, [])}
      </DialogContent>
    </StyleDialog>
  );
}
ModalContainer.propTypes = {
  id: PropTypes.string,
  title: CustomPropTypes.children,
  content: PropTypes.func,
  fullScreen: PropTypes.bool,
  isFullWidth: PropTypes.bool,
  showFullScreen: PropTypes.bool,
  isResizeable: PropTypes.bool,
  dialogHeight: PropTypes.number,
  dialogWidth: PropTypes.number,
  onClose: PropTypes.func,
  minWidth: PropTypes.number,
  minHeight: PropTypes.number,
  showTitle: PropTypes.bool,
};
