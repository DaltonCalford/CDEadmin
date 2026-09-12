/////////////////////////////////////////////////////////////
// Complete 38-screen Discovery composition using the shared workbench host.
/////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import {ContractInterfaceSurface, ContractInterfaceWorkspace} from
  '../../workspace/ContractInterfaceWorkspace';
import {DISCOVERY_FORM_BY_ID, DISCOVERY_SCREEN_BY_ID,
  DISCOVERY_SCREEN_FORM_BINDINGS, DISCOVERY_SCREEN_GROUPS} from
  './DiscoveryInterfaceContracts';

export const DEFAULT_DISCOVERY_SCREEN =
  'cdeadmin.discovery_intelligence.discovery_home';

export const DISCOVERY_WORKSPACE_CONTRACT = Object.freeze({
  defaultScreenId: DEFAULT_DISCOVERY_SCREEN,
  moduleId: 'cdeadmin.discovery_intelligence',
  moduleLabel: 'Discovery Intelligence',
  namespace: 'discovery',
  forms: DISCOVERY_FORM_BY_ID,
  screens: DISCOVERY_SCREEN_BY_ID,
  bindings: DISCOVERY_SCREEN_FORM_BINDINGS,
  groups: DISCOVERY_SCREEN_GROUPS,
});

export function DiscoveryInterfaceWorkspace({screenId=DEFAULT_DISCOVERY_SCREEN,
  ...props}) {
  return <ContractInterfaceWorkspace {...props} screenId={screenId}
    contractConfig={DISCOVERY_WORKSPACE_CONTRACT} />;
}

DiscoveryInterfaceWorkspace.propTypes = {
  screenId: PropTypes.string,
};

export function DiscoveryInterfaceSurface({screenId=DEFAULT_DISCOVERY_SCREEN,
  ...props}) {
  return <ContractInterfaceSurface {...props} screenId={screenId}
    contractConfig={DISCOVERY_WORKSPACE_CONTRACT} />;
}

DiscoveryInterfaceSurface.propTypes = {
  screenId: PropTypes.string,
};

export default DiscoveryInterfaceWorkspace;
