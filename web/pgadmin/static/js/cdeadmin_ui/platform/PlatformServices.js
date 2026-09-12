/////////////////////////////////////////////////////////////
// CDEadmin controlling platform-service composition.
/////////////////////////////////////////////////////////////

import {
  CredentialReferenceService, LayoutPersistenceService, ProviderRegistry,
  ResourceIdentityService,
} from './FoundationServices';
import {
  ConnectionSessionService, MetadataCatalogService, NativeOperationService,
} from './RuntimeServices';
import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from './CoordinationServices';

export const PLATFORM_SERVICE_IDS = Object.freeze({
  PROVIDERS: 'platform.providers',
  RESOURCE_IDENTITIES: 'platform.resource-identities',
  CONNECTIONS: 'platform.connections',
  OPERATIONS: 'platform.operations',
  METADATA: 'platform.metadata',
  RELATIONSHIPS: 'platform.relationships',
  TASKS: 'platform.tasks',
  SEARCH: 'platform.search',
  CREDENTIALS: 'platform.credentials',
  LAYOUTS: 'platform.layouts',
});

export function corePlatformServiceDefinitions(options={}) {
  return [
    {id: PLATFORM_SERVICE_IDS.PROVIDERS, factory: () => new ProviderRegistry()},
    {id: PLATFORM_SERVICE_IDS.RESOURCE_IDENTITIES,
      factory: () => new ResourceIdentityService()},
    {id: PLATFORM_SERVICE_IDS.CREDENTIALS,
      factory: () => new CredentialReferenceService(options.credentials)},
    {id: PLATFORM_SERVICE_IDS.CONNECTIONS,
      dependencies: [PLATFORM_SERVICE_IDS.CREDENTIALS],
      factory: ({services}) => new ConnectionSessionService({
        credentialService: services[PLATFORM_SERVICE_IDS.CREDENTIALS],
      })},
    {id: PLATFORM_SERVICE_IDS.OPERATIONS, factory: () => new NativeOperationService()},
    {id: PLATFORM_SERVICE_IDS.METADATA, factory: () => new MetadataCatalogService()},
    {id: PLATFORM_SERVICE_IDS.RELATIONSHIPS,
      factory: () => new RelationshipGraphService()},
    {id: PLATFORM_SERVICE_IDS.TASKS,
      factory: () => new TaskExecutionService(options.tasks)},
    {id: PLATFORM_SERVICE_IDS.SEARCH, factory: () => new FederatedSearchService()},
    {id: PLATFORM_SERVICE_IDS.LAYOUTS,
      factory: () => new LayoutPersistenceService(options.storage)},
  ].map((definition) => ({version: '1.0.0', ...definition}));
}

export function registerCorePlatformServices(registry, options={}) {
  const removers = corePlatformServiceDefinitions(options).filter((definition) =>
    !registry.has(definition.id)
  ).map((definition) => registry.register(definition));
  return () => removers.reverse().forEach((remove) => remove());
}

export * from './FoundationServices';
export * from './RuntimeServices';
export * from './CoordinationServices';
export {noRawSecrets} from './serviceUtils';
