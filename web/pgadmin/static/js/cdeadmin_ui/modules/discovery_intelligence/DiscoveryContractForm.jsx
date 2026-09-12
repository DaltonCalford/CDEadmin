/////////////////////////////////////////////////////////////
// Discovery binding for the shared contract-driven form renderer.
/////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import ContractForm from '../../foundations/ContractForm';
import {DISCOVERY_FORM_BY_ID} from './DiscoveryInterfaceContracts';

export function DiscoveryContractForm(props) {
  return <ContractForm {...props} namespace="discovery"
    formCatalog={DISCOVERY_FORM_BY_ID} />;
}

DiscoveryContractForm.propTypes = {
  form: PropTypes.oneOfType([PropTypes.string, PropTypes.object]).isRequired,
};

export default DiscoveryContractForm;
