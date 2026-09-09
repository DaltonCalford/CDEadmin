/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

/** Keep provider ownership levels visible and free of connection side effects. */
export function allowsSoleChildAutomation(itemData) {
  return itemData?.cde_endpoint !== true;
}
