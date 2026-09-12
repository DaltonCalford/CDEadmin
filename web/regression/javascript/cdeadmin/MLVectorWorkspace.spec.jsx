import {fireEvent, render, screen} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  MLVectorNavigator, MLVectorWorkspace, mlVectorInspector,
} from 'sources/cdeadmin_ui/modules/ml_vector/MLVectorWorkspace';
import usePreferences from '../../../pgadmin/preferences/static/js/store';
import {mlVectorDefinition, user} from './MLVectorTestUtils';
import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform/CoordinationServices';
import {MLVectorService} from 'sources/cdeadmin_ui/modules/ml_vector/MLVectorService';

function sessionValue(overrides={}) { const service = new MLVectorService({tasks: new TaskExecutionService(),
  relationships: new RelationshipGraphService(), search: new FederatedSearchService()});
return {...service.create({id: 'ml-one', content: mlVectorDefinition()}), ...overrides}; }
function fakeService(session=sessionValue()) { const listeners = new Set();
  return {get: jest.fn(() => session), list: jest.fn(() => [session]),
    subscribe: jest.fn((listener) => { listeners.add(listener); return () => listeners.delete(listener); }),
    select: jest.fn(), listeners}; }
function mount(surface='vector_explorer', overrides={}) { const session = sessionValue(overrides);
  const service = fakeService(session); const executeCommand = jest.fn().mockResolvedValue(session);
  const Component = withTheme(MLVectorWorkspace); render(<Component service={service} sessionId="ml-one"
    surface={surface} executeCommand={executeCommand} currentUser={user()} />);
  return {session, service, executeCommand}; }

describe('ML / Vector workspace', () => {
  beforeEach(() => usePreferences.setState({data: [], version: Date.now(), isLoading: false, failed: false}));
  test.each([['vector_explorer', 'grid', 'Vector designs fields and indexes'],
    ['index_designer', 'grid', 'Vector index plans'],
    ['vector_search_console', 'toolbar', 'Vector search controls'],
    ['embedding_pipeline', 'grid', 'Embedding pipelines'],
    ['model_registry', 'grid', 'Model registry versions'],
    ['experiments_evaluation', 'grid', 'ML experiments and evaluation'],
    ['deployment_bindings', 'grid', 'ML deployment bindings']])(
    'renders the complete %s surface', (surface, role, label) => { mount(surface);
      expect(screen.getByRole(role, {name: label})).toBeVisible();
      expect(screen.getByRole('navigation', {name: 'ML / Vector surfaces'})).toBeVisible(); });
  test('discovers provider capability evidence only through the validation command', () => {
    const {executeCommand} = mount('index_designer'); fireEvent.click(screen.getByRole('button',
      {name: 'Discover capabilities and validate'})); expect(executeCommand).toHaveBeenCalledWith(
      'vector.index.validate', {designId: 'documents', indexId: 'documents-hnsw'});
    expect(screen.getByText(/does not infer HNSW/)).toBeVisible();
  });
  test('runs text search and explain through separate command boundaries', () => {
    const {executeCommand} = mount('vector_search_console'); fireEvent.change(screen.getByLabelText('Query text'),
      {target: {value: 'database administration'}}); fireEvent.click(screen.getByRole('button',
      {name: 'Run search'})); fireEvent.click(screen.getByRole('button', {name: 'Explain'}));
    expect(executeCommand).toHaveBeenCalledWith('vector.search.run', {request: expect.objectContaining({
      designId: 'documents', indexId: 'documents-hnsw', queryText: 'database administration', k: 10})});
    expect(executeCommand).toHaveBeenCalledWith('vector.search.explain', {request: expect.objectContaining({
      queryText: 'database administration'})});
  });
  test('runs embeddings and reproducible evaluations through commands', () => {
    let result = mount('embedding_pipeline'); fireEvent.click(screen.getByRole('button', {name: 'Run pipeline'}));
    expect(result.executeCommand).toHaveBeenCalledWith('embedding.run', {pipelineId: 'document-embedding'});
    result = mount('experiments_evaluation'); fireEvent.click(screen.getByRole('button',
      {name: 'Run reproducible evaluation'})); expect(result.executeCommand).toHaveBeenCalledWith(
      'ml.evaluation.run', {evaluationId: 'retrieval-evaluation'});
  });
  test('keeps tag and alias mutations separate and confirms alias movement', () => {
    const {executeCommand} = mount('model_registry'); fireEvent.change(screen.getByLabelText('Version tags'),
      {target: {value: 'approved, production'}}); fireEvent.click(screen.getByRole('button', {name: 'Set tags'}));
    expect(executeCommand).toHaveBeenCalledWith('model.version.tag', {modelId: 'minilm',
      versionId: 'minilm-v1', tags: ['approved', 'production']});
    fireEvent.change(screen.getByLabelText('Alias'), {target: {value: 'production'}});
    fireEvent.change(screen.getByLabelText('Confirmation reference'), {target: {value: 'review-one'}});
    fireEvent.click(screen.getByRole('button', {name: 'Move alias'}));
    expect(executeCommand).toHaveBeenCalledWith('model.alias.set', {modelId: 'minilm',
      versionId: 'minilm-v1', alias: 'production', confirmationRef: 'review-one'});
  });
  test.each([['permission_denied', 'Permission denied'], ['runtime_failure', 'runtime failure'],
    ['partial', 'is partial'], ['disconnected', 'is disconnected'], ['read_only', 'is read only'],
    ['stale', 'is stale'], ['validation_error', 'validation error']])('renders %s explicitly', (state, label) => {
    mount('vector_explorer', {state, error: ''}); expect(screen.getAllByText(new RegExp(label, 'i'))[0]).toBeVisible();
  });
  test.each(['cdeadmin_standard', 'compact_expert', 'low_vision', 'high_contrast_light'])(
    'remains operable under %s presentation', (profile) => { usePreferences.setState({data: [{id: 1,
      module: 'misc', name: 'accessibility_profile', value: profile}], version: Date.now(),
    isLoading: false, failed: false}); const {executeCommand} = mount('vector_search_console');
    expect(screen.getByRole('button', {name: 'Run search'})).toBeDisabled();
    expect(executeCommand).not.toHaveBeenCalled(); });
  test('provides inspector facts and opens through the shared activity navigator', () => {
    expect(mlVectorInspector(sessionValue())).toMatchObject({selectedId: null, validation: {valid: true}});
    const service = fakeService(); const onOpen = jest.fn(); const Component = withTheme(MLVectorNavigator);
    render(<Component service={service} onOpen={onOpen} />); fireEvent.click(screen.getByRole('treeitem',
      {name: /Document retrieval/})); expect(onOpen).toHaveBeenCalledWith('ml-one', 'vector_explorer');
  });
});
