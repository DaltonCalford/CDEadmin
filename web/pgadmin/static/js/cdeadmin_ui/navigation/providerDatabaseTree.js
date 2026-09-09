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
  });
  return false;
}
