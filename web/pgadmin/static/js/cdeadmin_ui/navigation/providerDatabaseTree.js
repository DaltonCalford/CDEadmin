/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

export function providerEndpointSessionReady(serverData) {
  return Boolean(
    serverData?.cde_endpoint &&
    serverData.runtime_verification_state === 'verified' &&
    (serverData.is_password_saved ||
      serverData.cde_session_authenticated)
  );
}

/** Mirror a committed profile edit without reusing its old verification. */
export function invalidateProviderEndpointProfile(tree, item, result={}) {
  const data = tree?.itemData?.(item);
  if (!data?.cde_endpoint) return false;
  data.runtime_verification_state = 'stale';
  data.cde_session_authenticated = false;
  data.connected = false;
  data.verified_runtime_family = null;
  data.verified_runtime_version = null;
  data.runtime_evidence_reference = null;
  data.icon = 'icon-server-not-connected';
  // The response contains an owner-safe route catalog, never credentials.
  // Keep the saved-credential flag until the normal verification response
  // supplies its current value; do not erase credentials to invalidate state.
  const primary = result?.route_catalog?.routes?.[0]?.configuration;
  if (typeof primary?.user === 'string') data.username = primary.user;
  if (typeof result?.display_name === 'string' && result.display_name.trim()) {
    data.label = result.display_name;
    data._label = result.display_name;
    tree.setLabel(item, {label: result.display_name});
  }
  tree.addIcon(item, {icon: data.icon});
  return true;
}

/** Gate a provider database branch on its owning endpoint verification. */
export function beforeOpenProviderDatabase(tree, serverNode, item) {
  const serverItem = tree.parent(item);
  const serverData = serverItem ? tree.itemData(serverItem) : undefined;
  if (!serverData?.cde_endpoint) return true;
  if (providerEndpointSessionReady(serverData)) {
    return true;
  }
  serverNode.callbacks.verify_cde_endpoint.call(serverNode, {
    item: serverItem,
    openOnSuccess: true,
    openOnSuccessItem: item,
    databaseTargetId: tree.itemData(item)?._id,
  });
  return false;
}
