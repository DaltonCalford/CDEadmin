import {useEffect, useState} from 'react';
import PropTypes from 'prop-types';
import gettext from 'sources/gettext';
import {Alert, CircularProgress} from '@mui/material';
import getApiInstance from '../../static/js/api_instance';
import {ObjectInspectorSection} from '../../static/js/Dialogs/ProviderWorkspaceContent';

/** Resolve by endpoint/target/resource identity; ignore stale selection replies. */
export default function ProviderObjectProperties({nodeData}) {
  const [state, setState] = useState({loading: true});
  const url = nodeData.cde_workspace_url;
  const identity = nodeData.cde_resource_id;
  const targetId = nodeData.cde_database_target_id;
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const api = getApiInstance();
    setState({loading: true});
    api.get(url, {signal: controller.signal}).then(async ({data}) => {
      const workspace = data.data;
      const response = await api.post(url, {
        action: 'resource_inspect', request: {
          resource_id: identity,
          generation: workspace.resource_page?.generation,
          ...(targetId ? {database_target_id: targetId} : {}),
        },
      }, {signal: controller.signal});
      if (active) setState({
        resource: response.data.data,
        descriptor: workspace.visual_admin?.objects?.find((item) =>
          item.resource_kind === nodeData.cde_resource_kind),
      });
    }).catch((error) => {
      if (active) setState({error: error.response?.data?.errormsg ||
        error.message});
    });
    return () => { active = false; controller.abort(); };
  }, [url, identity, nodeData.cde_resource_kind, targetId]);
  if (state.loading) return <CircularProgress
    aria-label={gettext('Loading object properties')} />;
  if (state.error) return <Alert severity="error">{state.error}</Alert>;
  return <ObjectInspectorSection resource={state.resource}
    descriptor={state.descriptor} />;
}

ProviderObjectProperties.propTypes = {nodeData: PropTypes.object.isRequired};
