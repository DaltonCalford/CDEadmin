/////////////////////////////////////////////////////////////
// ML / Vector provider-aware workbench surfaces.
/////////////////////////////////////////////////////////////

import {useEffect, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {Select} from '../../primitives/Choice';
import {TextArea, TextField} from '../../primitives/Field';
import {TreeRow} from '../../navigation/AdvancedNavigation';
import {Toolbar} from '../../layout/WorkbenchChrome';
import {Badge, Banner, ProgressBar, Skeleton} from '../../feedback/Indicators';
import {EmptyState} from '../../feedback/EmptyState';
import DataGrid from '../../data/DataGrid';

export const ML_VECTOR_SURFACES = Object.freeze([
  {id: 'vector_explorer', title: 'Vector Explorer'}, {id: 'index_designer', title: 'Index Designer'},
  {id: 'vector_search_console', title: 'Vector Search Console'},
  {id: 'embedding_pipeline', title: 'Embedding Pipeline'}, {id: 'model_registry', title: 'Model Registry'},
  {id: 'experiments_evaluation', title: 'Experiments / Evaluation'},
  {id: 'deployment_bindings', title: 'Deployment Bindings'},
]);
function useSession(service, id) { const [session, setSession] = useState(() => service.get(id));
  useEffect(() => service.subscribe((next) => { if(next.id === id) setSession(next); }), [service, id]);
  return session; }
function permitted(user, permission) { return user.permissions?.includes(permission) === true; }
function actionable(session, user, permission) { return permitted(user, permission) && ![
  'loading', 'read_only', 'permission_denied', 'disconnected', 'background_task_active',
].includes(session.state); }
function invoke(execute, command, args, setError) { setError('');
  return Promise.resolve(execute(command, args)).catch((error) => setError(error.message)); }
function parseJSON(value, label) { try { return JSON.parse(value); } catch(error) {
  throw new TypeError(`${label} is not valid JSON: ${error.message}`); } }
function referenceLabel(reference) { return reference?.canonical ?? reference?.assetId ?? reference?.id ?? ''; }
function StateBoundary({session, children}) { const limitations = session.providerStatuses.flatMap((item) => [
  ...(item.warnings ?? []), ...(item.limitations ?? [])]); return <>
  {session.state === 'loading' && <Box sx={{p: 2}}><Skeleton lines={8} /></Box>}
  {session.state === 'empty' && <EmptyState message="Create or open an ML / Vector asset to begin." />}
  {session.state === 'background_task_active' && <ProgressBar label="ML / Vector background task"
    status="indeterminate" />}{session.error && <Banner status="error">{session.error}</Banner>}
  {!session.error && session.state === 'runtime_failure' && <Banner status="error">
    ML / Vector runtime failure. Authored definitions and prior evidence remain available.</Banner>}
  {session.state === 'permission_denied' && <Banner status="error">
    Permission denied. This is distinct from provider support.</Banner>}
  {['stale', 'partial', 'disconnected', 'read_only', 'validation_error'].includes(session.state) &&
    <Banner status="warning">ML / Vector asset is {session.state.replaceAll('_', ' ')}.</Banner>}
  {limitations.length > 0 && <Banner status="warning">Provider limitations: {
    [...new Set(limitations)].join(' ')}</Banner>}{children}</>; }
StateBoundary.propTypes = {session: PropTypes.object, children: PropTypes.node};

function VectorExplorer({session, service, currentUser}) {
  const rows = session.content.vectorDesigns.flatMap((design) => [
    {id: design.id, kind: 'design', name: design.name, parent: '', dimensions: design.dimensions,
      metric: '', provider: design.resourceRef.providerId, target: referenceLabel(design.resourceRef)},
    ...design.fields.map((field) => ({id: `${design.id}:field:${field.id}`, kind: 'embedding field',
      name: field.name, parent: design.name, dimensions: field.dimensions, metric: '',
      provider: field.modelRef.providerId, target: field.targetField})),
    ...design.indexes.map((index) => ({id: `${design.id}:index:${index.id}`, kind: 'index', name: index.name,
      parent: design.name, dimensions: index.dimensions, metric: `${index.normalizedMetric} / ${index.nativeMetric}`,
      provider: design.resourceRef.providerId, target: index.algorithm})),
  ]);
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Vector explorer controls"><Badge label={`${rows.length} vector objects`} /></Toolbar>
    {!permitted(currentUser, 'ml_vector.view') && <Banner status="error">ML / Vector view permission is required.</Banner>}
    {!rows.length ? <EmptyState message="No vector designs are configured." /> :
      <DataGrid gridId="ml-vector/explorer" aria-label="Vector designs fields and indexes" rows={rows} readOnly
        enableRowSelect rowKeyGetter={(row) => row.id} onItemEnter={(row) => row && service.select(session.id,
          {selectedId: row.id})} columns={[{key: 'kind', name: 'Kind'}, {key: 'name', name: 'Name'},
          {key: 'parent', name: 'Design'}, {key: 'dimensions', name: 'Dimensions'},
          {key: 'metric', name: 'Metric'}, {key: 'provider', name: 'Provider'},
          {key: 'target', name: 'Native target'}]} />}</Box>;
}
VectorExplorer.propTypes = {session: PropTypes.object, service: PropTypes.object,
  currentUser: PropTypes.object};

function IndexDesigner({session, execute, currentUser}) {
  const designs = session.content.vectorDesigns; const [designId, setDesignId] = useState(designs[0]?.id ?? '');
  const design = designs.find((item) => item.id === designId); const [indexId, setIndexId] = useState(
    design?.indexes[0]?.id ?? ''); const [error, setError] = useState('');
  useEffect(() => setIndexId(design?.indexes[0]?.id ?? ''), [designId]);
  const snapshot = session.runtime.capabilitySnapshots.find((item) => item.providerId === design?.resourceRef.providerId);
  const rows = (design?.indexes ?? []).map((index) => ({...index,
    providerConfigText: JSON.stringify(index.providerConfig), validation: session.runtime.validationResults
      .filter((item) => item.indexId === index.id).at(-1)?.provider?.valid === true ? 'valid' : 'not validated'}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Vector index controls"><Select label="Vector design" value={designId} onChange={setDesignId}
      options={designs.map((item) => ({value: item.id, label: item.name}))} />
    <Select label="Index" value={indexId} onChange={setIndexId} options={(design?.indexes ?? []).map((item) =>
      ({value: item.id, label: item.name}))} /><Button disabled={!actionable(session, currentUser,
      'ml_vector.view') || !indexId} onClick={() => invoke(execute, 'vector.index.validate',
      {designId, indexId}, setError)}>Discover capabilities and validate</Button></Toolbar>
    {error && <Banner status="error">{error}</Banner>}
    {!snapshot && <Banner status="info">Provider-specific index controls remain hidden until capability discovery
      returns evidence. CDEadmin does not infer HNSW, IVF, quantization, or build options.</Banner>}
    {snapshot && <Box sx={{p: 1}} aria-label="Provider-advertised vector capabilities">
      <Badge label={`${snapshot.providerId} ${snapshot.providerVersion}`} /> Metrics: {
        snapshot.capabilities.supported_metrics.map((item) => `${item.normalized} → ${item.native}`).join(', ')}.
      Index families: {snapshot.capabilities.supported_index_families.map((item) => item.label).join(', ')}.
      Maximum dimensions: {snapshot.capabilities.max_dimensions ?? 'not reported'}.</Box>}
    {!rows.length ? <EmptyState message="No vector index plans are configured for this design." /> :
      <DataGrid gridId="ml-vector/indexes" aria-label="Vector index plans" rows={rows} readOnly
        rowKeyGetter={(row) => row.id} columns={[{key: 'name', name: 'Index'},
          {key: 'algorithm', name: 'Provider algorithm'}, {key: 'normalizedMetric', name: 'Normalized metric'},
          {key: 'nativeMetric', name: 'Native metric'}, {key: 'dimensions', name: 'Dimensions'},
          {key: 'buildMode', name: 'Build mode'}, {key: 'providerConfigText', name: 'Advertised parameters'},
          {key: 'validation', name: 'Validation'}]} />}</Box>;
}
IndexDesigner.propTypes = {session: PropTypes.object, execute: PropTypes.func,
  currentUser: PropTypes.object};

function VectorSearchConsole({session, execute, currentUser}) {
  const designs = session.content.vectorDesigns; const [designId, setDesignId] = useState(designs[0]?.id ?? '');
  const design = designs.find((item) => item.id === designId); const [indexId, setIndexId] = useState(
    design?.indexes[0]?.id ?? ''); const [mode, setMode] = useState('text'); const [query, setQuery] = useState('');
  const [filters, setFilters] = useState('{}'); const [fields, setFields] = useState('');
  const [k, setK] = useState('10'); const [error, setError] = useState('');
  useEffect(() => setIndexId(design?.indexes[0]?.id ?? ''), [designId]);
  const request = () => ({designId, indexId, ...(mode === 'text' ? {queryText: query} :
    {queryVector: parseJSON(query, 'Query vector')}), filters: parseJSON(filters, 'Filters'), k: Number(k),
  outputFields: fields.split(',').map((item) => item.trim()).filter(Boolean)});
  const run = (command) => { try { return invoke(execute, command, {request: request()}, setError); }
  catch(caught) { setError(caught.message); } };
  const latest = session.runtime.searchResults.at(-1); const explain = session.runtime.explainResults.at(-1);
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}>
    <Toolbar label="Vector search controls"><Select label="Design" value={designId} onChange={setDesignId}
      options={designs.map((item) => ({value: item.id, label: item.name}))} />
    <Select label="Index" value={indexId} onChange={setIndexId} options={(design?.indexes ?? []).map((item) =>
      ({value: item.id, label: item.name}))} /><Select label="Query source" value={mode} onChange={setMode}
      options={[{value: 'text', label: 'Text'}, {value: 'vector', label: 'Vector'}]} />
    <TextField label="Top k" value={k} onChange={(event) => setK(event.target.value)} />
    <Button disabled={!actionable(session, currentUser, 'ml_vector.search') || !query || !indexId}
      onClick={() => run('vector.search.run')}>Run search</Button><Button disabled={!actionable(session,
      currentUser, 'ml_vector.search') || !query || !indexId} onClick={() => run('vector.search.explain')}>
      Explain</Button></Toolbar><TextArea label={mode === 'text' ? 'Query text' : 'Query vector JSON'}
      value={query} onChange={(event) => setQuery(event.target.value)} rows={3} />
    <Toolbar label="Vector search projection"><TextField label="Output fields (comma separated)" value={fields}
      onChange={(event) => setFields(event.target.value)} /><TextField label="Filters JSON" value={filters}
      onChange={(event) => setFilters(event.target.value)} /></Toolbar>{error && <Banner status="error">{error}</Banner>}
    {latest ? <DataGrid gridId="ml-vector/search-results" aria-label="Vector search results" rows={latest.rows}
      readOnly rowKeyGetter={(row, index) => row.id ?? index} columns={Object.keys(latest.rows[0] ?? {result: ''})
        .map((key) => ({key, name: key}))} /> : <EmptyState message="Run a bounded search to view results." />}
    {latest?.truncated && <Banner status="warning">Result preview is truncated. Continue with cursor {
      latest.cursorRef ?? 'when provided by the engine'}.</Banner>}
    {explain && <Box component="pre" aria-label="Provider-native vector query plan" sx={{overflow: 'auto'}}>{
      JSON.stringify(explain.plan, null, 2)}</Box>}</Box>;
}
VectorSearchConsole.propTypes = {session: PropTypes.object, execute: PropTypes.func,
  currentUser: PropTypes.object};

function EmbeddingPipeline({session, execute, currentUser}) {
  const [pipelineId, setPipelineId] = useState(session.content.embeddingPipelines[0]?.id ?? '');
  const [error, setError] = useState(''); const rows = session.content.embeddingPipelines.map((item) => ({...item,
    source: referenceLabel(item.resourceRef), fields: item.sourceFields.join(', '), model: `${item.modelRef.modelId}@${
      item.modelRef.versionId}`, provider: item.modelRef.providerIdentity ?? item.modelRef.providerId,
    sharing: item.dataSharingPolicy.containsSensitiveData ? item.dataSharingPolicy.approvedForSensitiveData ?
      'sensitive · approved' : 'sensitive · blocked' : 'non-sensitive'}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}><Toolbar
    label="Embedding pipeline controls"><Select label="Pipeline" value={pipelineId} onChange={setPipelineId}
      options={rows.map((item) => ({value: item.id, label: item.name}))} /><Button disabled={!actionable(session,
      currentUser, 'ml_vector.edit') || !pipelineId} onClick={() => invoke(execute, 'embedding.run',
      {pipelineId}, setError)}>Run pipeline</Button></Toolbar>{error && <Banner status="error">{error}</Banner>}
  <Banner status="info">External model provider identity and sensitive-data approval are enforced before any data
      leaves its provider. Assets contain credential references, never raw credentials.</Banner>
  {!rows.length ? <EmptyState message="No embedding pipelines are configured." /> :
    <DataGrid gridId="ml-vector/embedding-pipelines" aria-label="Embedding pipelines" rows={rows} readOnly
      rowKeyGetter={(row) => row.id} columns={[{key: 'name', name: 'Pipeline'}, {key: 'source', name: 'Source'},
        {key: 'fields', name: 'Source fields'}, {key: 'model', name: 'Model version'},
        {key: 'provider', name: 'Provider identity'}, {key: 'targetField', name: 'Target field'},
        {key: 'outputDimensions', name: 'Dimensions'}, {key: 'sharing', name: 'Data-sharing policy'}]} />}</Box>;
}
EmbeddingPipeline.propTypes = {session: PropTypes.object, execute: PropTypes.func,
  currentUser: PropTypes.object};

function ModelRegistry({session, execute, currentUser}) {
  const rows = session.content.modelEntries.flatMap((model) => model.versions.map((version) => ({id: version.id,
    modelId: model.id, model: model.name, version: version.version, digest: version.contentDigest,
    artifact: referenceLabel(version.artifactRef), aliases: model.aliases.filter((item) =>
      item.versionId === version.id).map((item) => item.name).join(', '), tags: model.versionTags.filter((item) =>
      item.versionId === version.id).flatMap((item) => item.tags).join(', '), description: version.description})));
  const [rowId, setRowId] = useState(rows[0]?.id ?? ''); const selected = rows.find((item) => item.id === rowId);
  const [tags, setTags] = useState(''); const [alias, setAlias] = useState('');
  const [confirmationRef, setConfirmationRef] = useState(''); const [error, setError] = useState('');
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}><Toolbar label="Model controls">
    <Select label="Model version" value={rowId} onChange={setRowId} options={rows.map((item) =>
      ({value: item.id, label: `${item.model} ${item.version}`}))} /><TextField label="Version tags" value={tags}
      onChange={(event) => setTags(event.target.value)} /><Button disabled={!actionable(session, currentUser,
      'ml_vector.edit') || !selected} onClick={() => invoke(execute, 'model.version.tag', {modelId: selected.modelId,
      versionId: selected.id, tags: tags.split(',').map((item) => item.trim()).filter(Boolean)}, setError)}>
      Set tags</Button><TextField label="Alias" value={alias} onChange={(event) => setAlias(event.target.value)} />
    <TextField label="Confirmation reference" value={confirmationRef}
      onChange={(event) => setConfirmationRef(event.target.value)} /><Button disabled={!actionable(session,
      currentUser, 'ml_vector.admin') || !selected || !alias || !confirmationRef} onClick={() => invoke(execute,
      'model.alias.set', {modelId: selected.modelId, versionId: selected.id, alias, confirmationRef}, setError)}>
      Move alias</Button></Toolbar>{error && <Banner status="error">{error}</Banner>}
  <Banner status="info">Model versions are immutable. Tags and aliases are mutable associations; alias movement
      requires consequential confirmation.</Banner>{!rows.length ? <EmptyState message="No models are registered." /> :
    <DataGrid gridId="ml-vector/models" aria-label="Model registry versions" rows={rows} readOnly enableRowSelect
      rowKeyGetter={(row) => row.id} onItemEnter={(row) => row && setRowId(row.id)} columns={[
        {key: 'model', name: 'Model'}, {key: 'version', name: 'Version'}, {key: 'digest', name: 'Digest'},
        {key: 'artifact', name: 'Artifact'}, {key: 'aliases', name: 'Aliases'}, {key: 'tags', name: 'Tags'},
        {key: 'description', name: 'Description'}]} />}</Box>;
}
ModelRegistry.propTypes = {session: PropTypes.object, execute: PropTypes.func,
  currentUser: PropTypes.object};

function ExperimentsEvaluation({session, execute, currentUser}) {
  const [evaluationId, setEvaluationId] = useState(session.content.evaluationCases[0]?.id ?? '');
  const [error, setError] = useState(''); const rows = session.content.evaluationCases.map((item) => ({...item,
    groundTruth: referenceLabel(item.groundTruthRef), datasetRevision: referenceLabel(item.datasetRevisionRef),
    metricsText: item.metrics.join(', '), result: session.runtime.evaluationResults.filter((result) =>
      result.evaluationId === item.id).at(-1)?.metrics?.metrics ?? null}));
  return <Box sx={{height: '100%', display: 'flex', flexDirection: 'column'}}><Toolbar
    label="ML evaluation controls"><Select label="Evaluation" value={evaluationId} onChange={setEvaluationId}
      options={rows.map((item) => ({value: item.id, label: item.name}))} /><Button disabled={!actionable(session,
      currentUser, 'ml_vector.view') || !evaluationId} onClick={() => invoke(execute, 'ml.evaluation.run',
      {evaluationId}, setError)}>Run reproducible evaluation</Button></Toolbar>
  {error && <Banner status="error">{error}</Banner>}{!rows.length ? <EmptyState
    message="No evaluation cases are configured." /> : <DataGrid gridId="ml-vector/evaluations"
    aria-label="ML experiments and evaluation" rows={rows} readOnly rowKeyGetter={(row) => row.id}
    columns={[{key: 'name', name: 'Evaluation'}, {key: 'vectorDesignId', name: 'Design'},
      {key: 'indexId', name: 'Index'}, {key: 'k', name: 'Top k'}, {key: 'metricsText', name: 'Metrics'},
      {key: 'groundTruth', name: 'Ground truth'}, {key: 'datasetRevision', name: 'Dataset revision'},
      {key: 'result', name: 'Latest result', renderCell: ({row}) => JSON.stringify(row.result ?? {})}]} />}</Box>;
}
ExperimentsEvaluation.propTypes = {session: PropTypes.object, execute: PropTypes.func,
  currentUser: PropTypes.object};

function DeploymentBindings({session}) { const rows = session.content.deploymentBindings.map((item) => ({...item,
  endpoint: referenceLabel(item.endpointRef), model: `${item.modelId}@${item.modelVersionId}`,
  credential: item.credentialRef ? referenceLabel(item.credentialRef) : 'not configured',
  policyText: JSON.stringify(item.policy), configText: JSON.stringify(item.config)}));
return <Box sx={{height: '100%'}}><Banner status="info">Deployment bindings describe serving resources and
  referenced credentials; this module does not silently deploy or infer provider support.</Banner>{!rows.length ?
  <EmptyState message="No deployment bindings are configured." /> :
  <DataGrid gridId="ml-vector/deployments" aria-label="ML deployment bindings" rows={rows} readOnly
    rowKeyGetter={(row) => row.id} columns={[{key: 'name', name: 'Binding'},
      {key: 'environment', name: 'Environment'}, {key: 'endpoint', name: 'Endpoint'},
      {key: 'model', name: 'Model version'}, {key: 'vectorDesignId', name: 'Vector design'},
      {key: 'indexId', name: 'Index'}, {key: 'credential', name: 'Credential reference'},
      {key: 'policyText', name: 'Policy'}, {key: 'configText', name: 'Provider configuration'}]} />}</Box>; }
DeploymentBindings.propTypes = {session: PropTypes.object};

const COMPONENTS = {vector_explorer: VectorExplorer, index_designer: IndexDesigner,
  vector_search_console: VectorSearchConsole, embedding_pipeline: EmbeddingPipeline,
  model_registry: ModelRegistry, experiments_evaluation: ExperimentsEvaluation,
  deployment_bindings: DeploymentBindings};
export function MLVectorWorkspace({service, sessionId, surface='vector_explorer', executeCommand,
  currentUser={}, onStateChange}) { const session = useSession(service, sessionId);
  const [active, setActive] = useState(surface); useEffect(() => setActive(surface), [surface]);
  useEffect(() => onStateChange?.(session), [onStateChange, session]);
  const Component = COMPONENTS[active] ?? VectorExplorer; const navigate = (next) => {
    setActive(next); service.select(sessionId, {surface: next}); };
  return <Box data-testid="ml-vector-workspace" sx={{height: '100%', display: 'grid',
    gridTemplateColumns: '288px minmax(0, 1fr) 340px'}}><Box component="nav"
      aria-label="ML / Vector surfaces" sx={{overflow: 'auto'}}>{ML_VECTOR_SURFACES.map((item) =>
        <TreeRow key={item.id} label={item.title} selected={active === item.id}
          onSelect={() => navigate(item.id)} />)}</Box><Box component="main" sx={{minWidth: 0, overflow: 'hidden'}}>
      <StateBoundary session={session}><Component session={session} service={service}
        execute={executeCommand} currentUser={currentUser} /></StateBoundary></Box>
    <Box component="aside" aria-label="ML / Vector inspector" sx={{overflow: 'auto', p: 1}}>
      <MLVectorInspector session={session} /></Box></Box>; }
MLVectorWorkspace.propTypes = {service: PropTypes.object.isRequired, sessionId: PropTypes.string.isRequired,
  surface: PropTypes.string, executeCommand: PropTypes.func.isRequired, currentUser: PropTypes.object,
  onStateChange: PropTypes.func};
export function MLVectorNavigator({service, onOpen}) { const [sessions, setSessions] = useState(() => service.list());
  useEffect(() => service.subscribe(() => setSessions(service.list())), [service]);
  return <Box role="tree" aria-label="ML / Vector assets">{sessions.length ? sessions.map((session) =>
    <TreeRow key={session.id} label={`${session.content.name || session.id} · ${session.state}`}
      onSelect={() => onOpen(session.id, session.surface)} />) :
    <EmptyState message="No ML / Vector assets are open." />}</Box>; }
MLVectorNavigator.propTypes = {service: PropTypes.object.isRequired, onOpen: PropTypes.func.isRequired};
function MLVectorInspector({session}) { const values = [...session.content.vectorDesigns,
  ...session.content.vectorDesigns.flatMap((item) => [...item.fields, ...item.indexes]),
  ...session.content.embeddingPipelines, ...session.content.modelEntries, ...session.content.experiments,
  ...session.content.evaluationCases, ...session.content.deploymentBindings];
const selected = values.find((item) => item.id === session.selectedId || session.selectedId?.endsWith(`:${item.id}`));
return <Box><Box component="h3">Selected definition</Box><Box component="pre">{
  JSON.stringify(selected ?? {}, null, 2)}</Box><Box component="h3">Provider evidence</Box><Box component="pre">{
  JSON.stringify(session.providerStatuses, null, 2)}</Box><Box component="h3">Validation</Box>
<Badge label={session.validation.valid ? 'valid' : 'invalid'} /></Box>; }
MLVectorInspector.propTypes = {session: PropTypes.object};
export function mlVectorInspector(session) { return {selectedId: session.selectedId,
  validation: session.validation, providerStatuses: session.providerStatuses,
  capabilitySnapshots: session.runtime.capabilitySnapshots}; }
