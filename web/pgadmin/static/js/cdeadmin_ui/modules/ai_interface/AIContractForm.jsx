/////////////////////////////////////////////////////////////
// AI binding for the shared contract-driven form renderer.
/////////////////////////////////////////////////////////////

import PropTypes from 'prop-types';
import ContractForm from '../../foundations/ContractForm';
import {AI_FORM_BY_ID} from './AIInterfaceContracts';

export function AIContractForm(props) {
  return <ContractForm {...props} namespace="ai" formCatalog={AI_FORM_BY_ID} />;
}

AIContractForm.propTypes = {
  form: PropTypes.oneOfType([PropTypes.string, PropTypes.object]).isRequired,
};

export default AIContractForm;
