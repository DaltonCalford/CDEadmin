/////////////////////////////////////////////////////////////
// AI Interface binding for the shared contract workspace host.
/////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {ContractInterfaceSurface, ContractInterfaceWorkspace} from
  '../../workspace/ContractInterfaceWorkspace';
import {AI_FORM_BY_ID, AI_SCREEN_BY_ID, AI_SCREEN_FORM_BINDINGS,
  AI_SCREEN_GROUPS} from './AIInterfaceContracts';

export const DEFAULT_SCREEN = 'cdeadmin.ai_interface.ai_workbench';

export const AI_WORKSPACE_CONTRACT = Object.freeze({
  defaultScreenId: DEFAULT_SCREEN,
  moduleId: 'cdeadmin.ai_interface',
  moduleLabel: 'AI Interface',
  namespace: 'ai',
  forms: AI_FORM_BY_ID,
  screens: AI_SCREEN_BY_ID,
  bindings: AI_SCREEN_FORM_BINDINGS,
  groups: AI_SCREEN_GROUPS,
});

export function AIInterfaceWorkspace({screenId=DEFAULT_SCREEN, ...props}) {
  return <ContractInterfaceWorkspace {...props} screenId={screenId}
    contractConfig={AI_WORKSPACE_CONTRACT} />;
}

AIInterfaceWorkspace.propTypes = {screenId: PropTypes.string};

export function AIInterfaceSurface({screenId=DEFAULT_SCREEN, ...props}) {
  return <ContractInterfaceSurface {...props} screenId={screenId}
    contractConfig={AI_WORKSPACE_CONTRACT} />;
}

AIInterfaceSurface.propTypes = {screenId: PropTypes.string};

export default AIInterfaceWorkspace;
