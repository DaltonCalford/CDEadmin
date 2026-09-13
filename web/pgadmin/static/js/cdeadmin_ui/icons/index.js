/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

export {EngineIcon, Icon, ObjectIcon} from './Icon';
export {
  ICON_CATEGORIES,
  ICON_ASSIGNMENT_SCHEMA,
  createIconAssignmentDocument,
  inferActionIconKey,
  listIconDefinitions,
  mergeIconAssignments,
  normalizeIconAssignments,
  registerIconDefinition,
  resolveIconDefinition,
  semanticEngineIconKey,
  semanticObjectIconKey,
} from './registry';
