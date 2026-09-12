/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {moduleRegistry} from './ModuleRegistry';
import {serviceRegistry} from './PlatformRegistry';
import {
  DDN_MODULE_ID, PROJECT_ASSET_SERVICE_ID, registerDDNModule,
} from '../integrations/ddn/module';
import {
  PLATFORM_SERVICE_IDS, registerCorePlatformServices,
} from './PlatformServices';
import {
  SCHEMA_COMPARE_MODULE_ID, SCHEMA_COMPARE_SERVICE_ID,
  registerSchemaCompareModule,
} from '../modules/schema_compare';
import {
  LINEAGE_MODULE_ID, LINEAGE_SERVICE_ID, registerLineageModule,
} from '../modules/lineage';
import {
  QUALITY_MODULE_ID, QUALITY_SERVICE_ID, registerQualityModule,
} from '../modules/quality';
import {
  CONTRACT_MODULE_ID, CONTRACT_SERVICE_ID, registerContractModule,
} from '../modules/data_contract';
import {
  ETL_MODULE_ID, ETL_SERVICE_ID, registerETLModule,
} from '../modules/etl';
import {
  CDC_MODULE_ID, CDC_SERVICE_ID, registerCDCModule,
} from '../modules/cdc';
import {
  REPLICATION_MODULE_ID, REPLICATION_SERVICE_ID, registerReplicationModule,
} from '../modules/replication';
import {
  TRACING_MODULE_ID, TRACING_SERVICE_ID, registerTracingModule,
} from '../modules/tracing';
import {
  MIGRATION_MODULE_ID, MIGRATION_SERVICE_ID, registerMigrationModule,
} from '../modules/migration';
import {API_MODULE_ID, API_SERVICE_ID, registerAPIModule} from '../modules/api';
import {
  ML_VECTOR_MODULE_ID, ML_VECTOR_SERVICE_ID, registerMLVectorModule,
} from '../modules/ml_vector';

const CORE_MODULE_ID = 'cdeadmin.core-shell';
const PROJECTS_MODULE_ID = 'cdeadmin.projects';
let initialization = null;

function requiredCallback(name, context) {
  if(typeof context[name] !== 'function') {
    throw new Error(`${name} authority is unavailable in this workspace.`);
  }
  return context[name];
}

function ensureCoreModules() {
  if(!moduleRegistry.has(CORE_MODULE_ID)) moduleRegistry.register({
    id: CORE_MODULE_ID, version: '1.0.0', title: 'CDEadmin shell',
    contributions: {
      activity: [{id: 'activity.data', label: 'Data Explorer',
        iconKey: 'object.database', priority: 10}],
      commands: [{id: 'view.data-explorer.focus', label: 'Data Explorer',
        iconKey: 'object.database', surfaces: ['view'],
        execute: (_args, context) => requiredCallback('showActivity', context)(
          'activity.data'
        )}],
    },
  });
  if(!moduleRegistry.has(PROJECTS_MODULE_ID)) moduleRegistry.register({
    id: PROJECTS_MODULE_ID, version: '1.0.0', title: 'Projects',
    iconKey: 'object.schema',
    serviceRequirements: [PROJECT_ASSET_SERVICE_ID],
    contributions: {
      activity: [{id: 'activity.projects', label: 'Project Explorer',
        iconKey: 'object.schema', priority: 20}],
      commands: [
        {id: 'project.create', label: 'New Project', iconKey: 'action.new',
          surfaces: ['file', 'project'],
          execute: (_args, context) => requiredCallback(
            'createProject', context
          )()},
        {id: 'project.explorer.focus', label: 'Project Explorer',
          iconKey: 'object.schema', surfaces: ['view', 'project'],
          execute: (_args, context) => requiredCallback(
            'showActivity', context
          )('activity.projects')},
      ],
    },
  });
}

export function initializeCDEadminPlatform({api, services: serviceOptions}={}) {
  if(initialization) return initialization;
  registerCorePlatformServices(serviceRegistry, serviceOptions);
  if(!serviceRegistry.has(PROJECT_ASSET_SERVICE_ID) ||
      !moduleRegistry.has(DDN_MODULE_ID)) {
    registerDDNModule({modules: moduleRegistry, services: serviceRegistry, api});
  }
  if(!moduleRegistry.has(SCHEMA_COMPARE_MODULE_ID)) registerSchemaCompareModule({
    modules: moduleRegistry, services: serviceRegistry, api,
  });
  if(!moduleRegistry.has(LINEAGE_MODULE_ID)) registerLineageModule({
    modules: moduleRegistry, services: serviceRegistry, api,
  });
  if(!moduleRegistry.has(QUALITY_MODULE_ID)) registerQualityModule({
    modules: moduleRegistry, services: serviceRegistry, api,
  });
  if(!moduleRegistry.has(CONTRACT_MODULE_ID)) registerContractModule({
    modules: moduleRegistry, services: serviceRegistry, api,
  });
  if(!moduleRegistry.has(ETL_MODULE_ID)) registerETLModule({
    modules: moduleRegistry, services: serviceRegistry, api,
  });
  if(!moduleRegistry.has(CDC_MODULE_ID)) registerCDCModule({
    modules: moduleRegistry, services: serviceRegistry, api,
  });
  if(!moduleRegistry.has(REPLICATION_MODULE_ID)) registerReplicationModule({
    modules: moduleRegistry, services: serviceRegistry, api,
  });
  if(!moduleRegistry.has(TRACING_MODULE_ID)) registerTracingModule({
    modules: moduleRegistry, services: serviceRegistry, api,
  });
  if(!moduleRegistry.has(MIGRATION_MODULE_ID)) registerMigrationModule({
    modules: moduleRegistry, services: serviceRegistry, api,
  });
  if(!moduleRegistry.has(API_MODULE_ID)) registerAPIModule({
    modules: moduleRegistry, services: serviceRegistry, api,
  });
  if(!moduleRegistry.has(ML_VECTOR_MODULE_ID)) registerMLVectorModule({
    modules: moduleRegistry, services: serviceRegistry, api,
  });
  ensureCoreModules();
  initialization = Promise.all([
    moduleRegistry.activate(CORE_MODULE_ID),
    moduleRegistry.activate(PROJECTS_MODULE_ID),
    moduleRegistry.activate(DDN_MODULE_ID),
    moduleRegistry.activate(SCHEMA_COMPARE_MODULE_ID),
    moduleRegistry.activate(LINEAGE_MODULE_ID),
    moduleRegistry.activate(QUALITY_MODULE_ID),
    moduleRegistry.activate(CONTRACT_MODULE_ID),
    moduleRegistry.activate(ETL_MODULE_ID),
    moduleRegistry.activate(CDC_MODULE_ID),
    moduleRegistry.activate(REPLICATION_MODULE_ID),
    moduleRegistry.activate(TRACING_MODULE_ID),
    moduleRegistry.activate(MIGRATION_MODULE_ID),
    moduleRegistry.activate(API_MODULE_ID),
    moduleRegistry.activate(ML_VECTOR_MODULE_ID),
  ]).then(async () => {
    const services = {};
    for(const [name, id] of Object.entries(PLATFORM_SERVICE_IDS)) {
      services[name.toLowerCase()] = await serviceRegistry.resolve(id);
    }
    return Object.freeze({
      modules: moduleRegistry, services: Object.freeze(services),
      projectAssets: await serviceRegistry.resolve(PROJECT_ASSET_SERVICE_ID),
      schemaCompare: await serviceRegistry.resolve(SCHEMA_COMPARE_SERVICE_ID),
      lineage: await serviceRegistry.resolve(LINEAGE_SERVICE_ID),
      quality: await serviceRegistry.resolve(QUALITY_SERVICE_ID),
      contract: await serviceRegistry.resolve(CONTRACT_SERVICE_ID),
      etl: await serviceRegistry.resolve(ETL_SERVICE_ID),
      cdc: await serviceRegistry.resolve(CDC_SERVICE_ID),
      replication: await serviceRegistry.resolve(REPLICATION_SERVICE_ID),
      tracing: await serviceRegistry.resolve(TRACING_SERVICE_ID),
      migration: await serviceRegistry.resolve(MIGRATION_SERVICE_ID),
      apiDesigner: await serviceRegistry.resolve(API_SERVICE_ID),
      mlVector: await serviceRegistry.resolve(ML_VECTOR_SERVICE_ID),
    });
  }).catch((error) => {
    initialization = null;
    throw error;
  });
  return initialization;
}

export function platformInitializationState() { return initialization; }

export {CORE_MODULE_ID, PROJECTS_MODULE_ID};
