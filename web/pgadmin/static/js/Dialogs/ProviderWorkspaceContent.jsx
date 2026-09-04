/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import gettext from 'sources/gettext';
import {
  Alert, Box, Button, Checkbox, CircularProgress, FormControlLabel,
  MenuItem, Tab, Tabs, TextField,
} from '@mui/material';
import getApiInstance from '../api_instance';
import BaseChart from '../chartjs';
import { ModalContent, ModalFooter } from '../components/ModalContent';

function errorMessage(error) {
  return error?.response?.data?.errormsg || error?.message ||
    gettext('The provider workspace request failed.');
}

function defaultSource(languageProfile) {
  const sources = {
    cypher: 'MATCH (n) OPTIONAL MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 100',
    'redis-resp3-command': 'PING',
    'opensearch-query-dsl': '{"query":{"match_all":{}},"size":100}',
    'opensearch-sql-ppl': 'SELECT * FROM index_name LIMIT 100',
    'milvus-query-search-api': '{"operation":"query","collection_name":"collection","filter":"","output_fields":["*"],"limit":100}',
    'influxdb3-sql-influxql': 'SELECT * FROM measurement LIMIT 100',
    'mongodb-query-api': '{"operation":"find","database":"database","collection":"collection","filter":{},"limit":100}',
    'mongodb-query-api-json': '{"operation":"aggregate","database":"database","collection":"collection","pipeline":[{"$limit":100}]}',
    cql: 'SELECT * FROM keyspace.table LIMIT 100',
    'cql-3': 'SELECT * FROM keyspace.table LIMIT 100',
  };
  return sources[languageProfile] || 'SELECT 1';
}

function sourcePresets(languageProfile) {
  const presets = {
    'opensearch-sql-ppl': [
      ['SQL', 'SELECT * FROM index_name LIMIT 100'],
      ['PPL', 'source = index_name | head 100'],
    ],
    'influxdb3-sql-influxql': [
      ['SQL', 'SELECT * FROM measurement LIMIT 100'],
      ['InfluxQL', 'SHOW MEASUREMENTS'],
    ],
    'mongodb-query-api-json': [
      ['Find', '{"operation":"find","database":"database","collection":"collection","filter":{},"limit":100}'],
      ['Aggregation pipeline', '{"operation":"aggregate","database":"database","collection":"collection","pipeline":[{"$limit":100}]}'],
    ],
  };
  if (presets[languageProfile]) return presets[languageProfile];
  return [[languageProfile || gettext('Native'), defaultSource(languageProfile)]];
}

function downloadBase64(payload) {
  const bytes = Uint8Array.from(atob(payload.content_base64),
    (character) => character.charCodeAt(0));
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([bytes], {type: payload.media_type}));
  link.download = payload.filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function deliveryRequestId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  const values = new Uint8Array(16);
  window.crypto.getRandomValues(values);
  values[6] = (values[6] & 0x0f) | 0x40;
  values[8] = (values[8] & 0x3f) | 0x80;
  return [...values].map((value, index) =>
    `${index === 4 || index === 6 || index === 8 || index === 10 ? '-' : ''}${
      value.toString(16).padStart(2, '0')}`).join('');
}

function resourceHierarchy(groups, expanded, filtering) {
  const rows = [];
  groups.forEach((group) => {
    const groupId = `group:${group.group_id}`;
    rows.push({
      id: groupId, parentId: null, level: 1, expandable: true,
      expanded: filtering || expanded.has(groupId), title: group.title,
      kind: 'group', group,
    });
    if (!filtering && !expanded.has(groupId)) return;
    const root = {children: new Map(), resources: []};
    group.items.forEach((resource) => {
      const sourcePath = resource.display_path?.length ?
        resource.display_path : resource.authority_path || [];
      const path = [...sourcePath];
      if (path[path.length - 1] === resource.display_name) path.pop();
      let parent = root;
      let parentId = groupId;
      path.forEach((segment) => {
        const title = String(segment);
        const id = `${parentId}/path:${encodeURIComponent(title)}`;
        if (!parent.children.has(title)) {
          parent.children.set(title, {
            id, parentId, title, children: new Map(), resources: [],
          });
        }
        parent = parent.children.get(title);
        parentId = id;
      });
      parent.resources.push({resource, parentId});
    });
    const visit = (node, level) => {
      [...node.children.values()].sort((left, right) =>
        left.title.localeCompare(right.title)).forEach((branch) => {
        const isOpen = filtering || expanded.has(branch.id);
        rows.push({
          ...branch, level, kind: 'branch', expandable: true,
          expanded: isOpen,
        });
        if (isOpen) visit(branch, level + 1);
      });
      node.resources.sort((left, right) =>
        left.resource.display_name.localeCompare(
          right.resource.display_name
        )).forEach(({resource, parentId}) => rows.push({
        id: `resource:${resource.resource_id}`, parentId, level,
        expandable: false, expanded: false, title: resource.display_name,
        kind: 'resource', resource,
      }));
    };
    visit(root, 2);
  });
  return rows;
}

function ResourceExplorer({catalog, page, selectedResourceId, onSelect,
  onOpenAdministration, onOpenData, onLoadMore, onRefresh, loadingMore}) {
  const items = page?.items || [];
  const [filter, setFilter] = useState('');
  const [expanded, setExpanded] = useState(new Set());
  const [activeId, setActiveId] = useState(null);
  const rowRefs = useRef(new Map());
  const objectDescriptors = useMemo(() => Object.fromEntries(
    (catalog?.objects || []).map((item) => [item.resource_kind, item])
  ), [catalog]);
  const groups = useMemo(() => {
    const admitted = items.filter((item) => {
      const needle = filter.trim().toLocaleLowerCase();
      if (!needle) return true;
      return [item.display_name, item.resource_kind,
        ...(item.display_path || []), ...(item.authority_path || [])]
        .some((value) => String(value || '').toLocaleLowerCase()
          .includes(needle));
    });
    const declared = catalog?.navigator?.groups || [];
    const result = declared.map((group) => ({...group, items: []}));
    const byId = Object.fromEntries(result.map((group) =>
      [group.group_id, group]));
    admitted.forEach((item) => {
      const groupId = objectDescriptors[item.resource_kind]?.navigator
        ?.group_id || 'operations';
      if (!byId[groupId]) {
        byId[groupId] = {
          group_id: groupId,
          title: gettext('Other provider objects'),
          order: result.length,
          items: [],
        };
        result.push(byId[groupId]);
      }
      byId[groupId].items.push(item);
    });
    return result.filter((group) => group.items.length > 0);
  }, [catalog, filter, items, objectDescriptors]);
  const filtering = Boolean(filter.trim());
  const rows = useMemo(
    () => resourceHierarchy(groups, expanded, filtering),
    [expanded, filtering, groups]
  );
  const selected = items.find((item) =>
    item.resource_id === selectedResourceId);
  const selectedDescriptor = selected ?
    objectDescriptors[selected.resource_kind] : null;
  const canOpenData = selectedDescriptor?.editor?.sections?.includes('data');

  useEffect(() => {
    setExpanded((current) => {
      const next = new Set(current);
      groups.forEach((group) => next.add(`group:${group.group_id}`));
      return next.size === current.size ? current : next;
    });
  }, [groups]);

  useEffect(() => {
    if (activeId && rows.some((row) => row.id === activeId)) return;
    setActiveId(rows[0]?.id || null);
  }, [activeId, rows]);

  const toggle = (id, force) => setExpanded((current) => {
    const next = new Set(current);
    const shouldExpand = force ?? !next.has(id);
    if (shouldExpand) next.add(id);
    else next.delete(id);
    return next;
  });
  const focusRow = (id) => {
    if (!id) return;
    setActiveId(id);
    setTimeout(() => rowRefs.current.get(id)?.focus(), 0);
  };
  const keyDown = (event, row, index) => {
    let destination = null;
    if (event.key === 'ArrowDown') destination = rows[index + 1]?.id;
    if (event.key === 'ArrowUp') destination = rows[index - 1]?.id;
    if (event.key === 'Home') destination = rows[0]?.id;
    if (event.key === 'End') destination = rows[rows.length - 1]?.id;
    if (event.key === 'ArrowRight' && row.expandable) {
      if (!row.expanded) toggle(row.id, true);
      else destination = rows.find((item) => item.parentId === row.id)?.id;
    }
    if (event.key === 'ArrowLeft') {
      if (row.expandable && row.expanded) toggle(row.id, false);
      else destination = row.parentId;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      if (row.kind === 'resource') onSelect(row.resource);
      else if (row.expandable) toggle(row.id);
    }
    if (destination || ['ArrowRight', 'ArrowLeft', 'Enter', ' ']
      .includes(event.key)) {
      event.preventDefault();
      focusRow(destination);
    }
  };
  return (
    <Box sx={{overflow: 'auto', flex: 1, p: 2}}
      aria-label={gettext('Provider resource explorer')}>
      <TextField fullWidth size="small" value={filter}
        label={gettext('Filter provider objects')}
        onChange={(event) => setFilter(event.target.value)} />
      {selected && <Box component="nav" aria-label={gettext('Selected object breadcrumb')}
        sx={{display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 1}}>
        {(selected.display_path || selected.authority_path || []).map(
          (part, index) => <Box component="span" key={`${part}:${index}`}>
            {index > 0 && <Box component="span" aria-hidden="true"> / </Box>}
            {String(part)}
          </Box>)}
        <Box sx={{display: 'flex', gap: 1, width: '100%', mt: 0.5}}>
          <Button size="small" onClick={onOpenAdministration}>
            {gettext('Open object editor')}
          </Button>
          <Button size="small" onClick={onOpenData} disabled={!canOpenData}>
            {gettext('Open object data')}
          </Button>
        </Box>
      </Box>}
      <Box role="tree"
        aria-label={catalog?.navigator?.navigator_id ||
          gettext('Provider resource navigator')}
        sx={{mt: 1, border: 1, borderColor: 'divider'}}>
        {rows.map((row, index) => {
          const descriptor = row.resource ?
            objectDescriptors[row.resource.resource_kind] : null;
          return <Box key={row.id} role="treeitem" aria-level={row.level}
            aria-expanded={row.expandable ? row.expanded : undefined}
            aria-selected={row.kind === 'resource' ?
              selectedResourceId === row.resource.resource_id : undefined}
            tabIndex={activeId === row.id ? 0 : -1}
            ref={(element) => {
              if (element) rowRefs.current.set(row.id, element);
              else rowRefs.current.delete(row.id);
            }}
            onFocus={() => setActiveId(row.id)}
            onKeyDown={(event) => keyDown(event, row, index)}
            onClick={() => row.kind === 'resource' ?
              onSelect(row.resource) : toggle(row.id)}
            sx={{
              display: 'flex', alignItems: 'center', gap: 1, py: 0.5,
              px: 1, pl: `${8 + (row.level - 1) * 18}px`, cursor: 'pointer',
              bgcolor: row.kind === 'resource' &&
                selectedResourceId === row.resource.resource_id ?
                'action.selected' : undefined,
              '&:focus-visible': {outline: '2px solid', outlineColor: 'primary.main'},
            }}>
            <Box component="span" aria-hidden="true" sx={{width: 14}}>
              {row.expandable ? (row.expanded ? '▾' : '▸') : '•'}
            </Box>
            <Box component="span" aria-hidden="true" title={
              descriptor?.title || row.kind
            } data-icon-id={descriptor?.navigator?.icon_id}
            sx={{fontWeight: 700, minWidth: 18}}>
              {row.kind === 'resource' ?
                row.resource.resource_kind.slice(0, 1).toUpperCase() : '◇'}
            </Box>
            <Box component="span" sx={{fontWeight:
              row.kind === 'group' ? 700 : 400}}>{row.title}</Box>
            {row.kind === 'resource' && <Box component="small"
              sx={{ml: 'auto', color: 'text.secondary'}}>
              {row.resource.resource_kind}
            </Box>}
          </Box>;
        })}
      </Box>
      {items.length === 0 && <Box sx={{mt: 2}}>
        {gettext('No resources returned.')}
      </Box>}
      {items.length > 0 && groups.length === 0 && <Box sx={{mt: 2}}>
        {gettext('No provider objects match the filter.')}
      </Box>}
      <Box sx={{display: 'flex', gap: 1, mt: 1}}>
        {page?.next_cursor && <Button disabled={loadingMore}
          onClick={onLoadMore}>{gettext('Load more provider objects')}</Button>}
        <Button disabled={loadingMore || !page?.generation}
          onClick={onRefresh}>{gettext('Refresh provider objects')}</Button>
      </Box>
    </Box>
  );
}

ResourceExplorer.propTypes = {
  catalog: PropTypes.object,
  page: PropTypes.object,
  selectedResourceId: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  onOpenAdministration: PropTypes.func.isRequired,
  onOpenData: PropTypes.func.isRequired,
  onLoadMore: PropTypes.func.isRequired,
  onRefresh: PropTypes.func.isRequired,
  loadingMore: PropTypes.bool,
};

function initialFieldValue(field) {
  if (field.default === undefined || field.default === null) {
    if (field.control === 'boolean') return false;
    if (field.control === 'multiselect') return [];
    return '';
  }
  if (field.control === 'json') return JSON.stringify(field.default, null, 2);
  return field.default;
}

function fieldVisible(field, draft) {
  const condition = field.visible_when;
  if (!condition) return true;
  if (Object.prototype.hasOwnProperty.call(condition, 'equals')) {
    return draft[condition.field_id] === condition.equals;
  }
  if (Array.isArray(condition.in)) {
    return condition.in.includes(draft[condition.field_id]);
  }
  return false;
}

function VisualAdminField({field, value, onChange}) {
  const admittedValue = value ?? initialFieldValue(field);
  if (field.control === 'boolean') {
    return <FormControlLabel control={<Checkbox checked={Boolean(admittedValue)}
      onChange={(event) => onChange(event.target.checked)} />}
    label={field.label} />;
  }
  if (field.control === 'select') {
    return <TextField select fullWidth label={field.label} value={admittedValue}
      required={field.required}
      onChange={(event) => onChange(event.target.value)}>
      {(field.options || []).map((option) => (
        <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>
      ))}
    </TextField>;
  }
  if (field.control === 'multiselect') {
    return <TextField select fullWidth label={field.label}
      value={Array.isArray(admittedValue) ? admittedValue : []}
      required={field.required} SelectProps={{
        multiple: true,
        MenuProps: {
          PaperProps: {
            sx: {maxHeight: 'min(320px, calc(100vh - 96px))'},
          },
        },
      }}
      onChange={(event) => {
        const nextValue = event.target.value;
        onChange(typeof nextValue === 'string' ?
          nextValue.split(',').filter(Boolean) : nextValue);
      }}>
      {(field.options || []).map((option) => (
        <MenuItem key={option.value} value={option.value}>
          <Checkbox checked={Array.isArray(admittedValue) &&
            admittedValue.includes(option.value)} />
          {option.label}
        </MenuItem>
      ))}
    </TextField>;
  }
  const multiline = ['multiline', 'code', 'json'].includes(field.control);
  return <TextField fullWidth label={field.label} value={admittedValue}
    required={field.required} multiline={multiline}
    minRows={multiline ? 3 : undefined} maxRows={multiline ? 12 : undefined}
    type={field.control === 'number' ? 'number' :
      field.control === 'password' ? 'password' : 'text'}
    inputProps={field.control === 'number' ? {
      min: field.minimum, max: field.maximum,
    } : undefined}
    helperText={field.help || ''}
    onChange={(event) => onChange(
      field.control === 'number' && event.target.value !== '' ?
        Number(event.target.value) : event.target.value
    )} />;
}

VisualAdminField.propTypes = {
  field: PropTypes.object.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
};

function providerNative(resource) {
  const extensions = resource?.extensions || {};
  return Object.values(extensions).find((item) =>
    item && typeof item === 'object' && item.native)?.native || {};
}

function sectionPayload(section, resource, descriptor) {
  const native = providerNative(resource);
  if (section === 'properties') {
    return {
      name: resource?.display_name, kind: resource?.resource_kind,
      display_path: resource?.display_path,
      authority_path: resource?.authority_path,
      generation: resource?.generation,
    };
  }
  if (section === 'definition') return native.definition ?? native;
  if (section === 'dependencies') {
    return native.dependencies ?? native.dependents ?? [];
  }
  if (section === 'security') {
    return native.security ?? native.privileges ?? native.grants ?? [];
  }
  if (section === 'statistics') {
    return native.statistics ?? native.stats ?? native.metrics ?? {};
  }
  if (section === 'state') {
    return native.state ?? native.status ?? native.health ?? native;
  }
  if (section === 'data') {
    return {
      presentation: descriptor?.editor?.data_presentation || null,
      workspace: gettext('Use Open object data from the navigator.'),
    };
  }
  if (section === 'operations') {
    return (descriptor?.operations || []).map((item) => ({
      operation_id: item.operation_id, title: item.title,
      execution_available: item.execution_available,
      blockers: item.blockers || [],
    }));
  }
  return {};
}

const DEFAULT_INSPECTOR_SECTIONS = ['properties'];

function ObjectInspectorTabs({resource, descriptor, loading}) {
  const sections = descriptor?.editor?.sections ||
    DEFAULT_INSPECTOR_SECTIONS;
  const [section, setSection] = useState(sections[0]);
  useEffect(() => {
    if (!sections.includes(section)) setSection(sections[0]);
  }, [section, sections]);
  if (!resource) return null;
  return <Box sx={{mb: 2, border: 1, borderColor: 'divider'}}>
    <Box sx={{p: 1}}>
      <Box component="strong">{resource.display_name}</Box>
      {' · '}{descriptor?.title || resource.resource_kind}
      {loading && <CircularProgress size={16} sx={{ml: 1}} />}
    </Box>
    <Tabs value={section} variant="scrollable"
      onChange={(_event, value) => setSection(value)}
      aria-label={gettext('Provider object sections')}>
      {sections.map((item) => <Tab key={item} value={item}
        label={item.replaceAll('-', ' ')} />)}
    </Tabs>
    <Box role="tabpanel" aria-label={`${section} ${gettext('object section')}`}
      component="pre" sx={{m: 0, p: 1, overflow: 'auto', maxHeight: 220,
        whiteSpace: 'pre-wrap', bgcolor: 'background.default'}}>
      {JSON.stringify(sectionPayload(section, resource, descriptor), null, 2)}
    </Box>
  </Box>;
}

ObjectInspectorTabs.propTypes = {
  resource: PropTypes.object,
  descriptor: PropTypes.object,
  loading: PropTypes.bool,
};

function VisualAdministration({catalog, resources, selectedResource, post,
  setError, resourceGeneration}) {
  const objects = catalog?.objects || [];
  const [resourceKind, setResourceKind] = useState(objects[0]?.resource_kind || '');
  const [operationId, setOperationId] = useState(objects[0]?.operations?.[0]?.operation_id || '');
  const [targetId, setTargetId] = useState('');
  const [draft, setDraft] = useState({});
  const [plan, setPlan] = useState(null);
  const [validation, setValidation] = useState(null);
  const [result, setResult] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [working, setWorking] = useState(false);
  const [inspectedResource, setInspectedResource] = useState(null);
  const [inspecting, setInspecting] = useState(false);
  const objectDescriptor = objects.find((item) => item.resource_kind === resourceKind);
  const operations = objectDescriptor?.operations || [];
  const operation = operations.find((item) => item.operation_id === operationId);
  const allFields = operation?.form?.fields || [];
  const fields = allFields.filter((field) => fieldVisible(field, draft));
  const targetKinds = operation?.target_resource_kinds || [resourceKind];
  const matchingResources = (resources || []).filter(
    (item) => targetKinds.includes(item.resource_kind)
  );

  useEffect(() => {
    if (!objectDescriptor && objects.length) {
      setResourceKind(objects[0].resource_kind);
    }
  }, [objectDescriptor, objects]);

  useEffect(() => {
    if (!selectedResource) return;
    if (objects.some((item) =>
      item.resource_kind === selectedResource.resource_kind)) {
      setResourceKind(selectedResource.resource_kind);
      setTargetId(selectedResource.resource_id);
    }
  }, [objects, selectedResource]);

  useEffect(() => {
    let active = true;
    if (!selectedResource) {
      setInspectedResource(null);
      return () => { active = false; };
    }
    setInspecting(true);
    post({
      action: 'resource_inspect', request: {
        resource_id: selectedResource.resource_id,
        generation: resourceGeneration,
      },
    }).then((resource) => {
      if (active) setInspectedResource(resource);
    }).catch((requestError) => {
      if (active) setError(errorMessage(requestError));
    }).finally(() => {
      if (active) setInspecting(false);
    });
    return () => { active = false; };
  }, [post, resourceGeneration, selectedResource, setError]);

  useEffect(() => {
    const nextOperation = operations.find((item) => item.operation_id === operationId) || operations[0];
    if (nextOperation?.operation_id !== operationId) {
      setOperationId(nextOperation?.operation_id || '');
    }
  }, [operationId, operations]);

  useEffect(() => {
    const values = {};
    allFields.forEach((field) => {
      values[field.field_id] = initialFieldValue(field);
    });
    setDraft(values);
    setPlan(null);
    setValidation(null);
    setResult(null);
    setConfirmed(false);
  }, [operationId, resourceKind]);

  useEffect(() => {
    if (!matchingResources.some((item) => item.resource_id === targetId)) {
      setTargetId(matchingResources[0]?.resource_id || '');
    }
  }, [matchingResources, targetId]);

  const request = () => ({
    resource_kind: resourceKind,
    operation_id: operationId,
    target_resource: matchingResources.find((item) =>
      item.resource_id === targetId) || null,
    draft: Object.fromEntries(fields.filter((field) => {
      const value = draft[field.field_id];
      return field.required || (value !== '' &&
        !(Array.isArray(value) && value.length === 0));
    }).map((field) => [field.field_id, draft[field.field_id]])),
  });

  const preview = async () => {
    setWorking(true);
    setError(null);
    setPlan(null);
    setResult(null);
    try {
      const checked = await post({
        action: 'visual_admin_validate', request: request(),
      });
      setValidation(checked);
      if (!checked.valid) return;
      setPlan(await post({
        action: 'visual_admin_plan', request: request(),
      }));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const apply = async () => {
    setWorking(true);
    setError(null);
    try {
      setResult(await post({
        action: 'visual_admin_apply',
        request: {
          plan_id: plan.plan_id,
          plan_digest: plan.plan_digest,
          confirmed,
        },
      }));
      setPlan(null);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  if (!catalog) return <Alert severity="info">{gettext('This provider does not publish a visual administration catalog.')}</Alert>;
  return <Box sx={{p: 2, overflow: 'auto', flex: 1}}>
    <ObjectInspectorTabs resource={inspectedResource || selectedResource}
      descriptor={objectDescriptor} loading={inspecting} />
    <Box sx={{display: 'grid', gridTemplateColumns: 'minmax(180px, 1fr) minmax(180px, 1fr)', gap: 2}}>
      <TextField select label={gettext('Object type')} value={resourceKind}
        onChange={(event) => setResourceKind(event.target.value)}>
        {objects.map((item) => <MenuItem key={item.resource_kind}
          value={item.resource_kind}>{item.title}</MenuItem>)}
      </TextField>
      <TextField select label={gettext('Operation')} value={operationId}
        onChange={(event) => setOperationId(event.target.value)}>
        {operations.map((item) => <MenuItem key={item.operation_id}
          value={item.operation_id}>{item.title}</MenuItem>)}
      </TextField>
    </Box>
    {operation?.target_required && <TextField select fullWidth sx={{mt: 2}}
      label={gettext('Target resource')} value={targetId}
      onChange={(event) => setTargetId(event.target.value)}>
      {matchingResources.map((item) => <MenuItem key={item.resource_id}
        value={item.resource_id}>{item.display_name}</MenuItem>)}
    </TextField>}
    {operation?.target_required && matchingResources.length === 0 &&
      <Alert severity="warning" sx={{mt: 2}}>{gettext('No discovered resource of this type is available. Refresh provider metadata or choose Create.')}</Alert>}
    <Box sx={{display: 'flex', flexDirection: 'column', gap: 2, mt: 2}}>
      {fields.map((field) => <VisualAdminField key={field.field_id}
        field={field} value={draft[field.field_id]}
        onChange={(value) => setDraft((current) => ({...current, [field.field_id]: value}))} />)}
    </Box>
    {(operation?.blockers || []).length > 0 && <Alert severity="info" sx={{mt: 2}}>
      {gettext('Execution readiness')}: {operation.blockers.join(', ')}
    </Alert>}
    {validation && !validation.valid && <Alert severity="error" sx={{mt: 2}}>
      {validation.errors.map((item) => item.message).join(' ')}
    </Alert>}
    {plan && <Box component="pre" aria-label={gettext('Provider plan preview')}
      sx={{mt: 2, p: 1, overflow: 'auto', maxHeight: 240, bgcolor: 'background.default'}}>
      {JSON.stringify(plan, null, 2)}
    </Box>}
    {plan?.impact && <Alert severity={
      plan.impact.availability_risk === 'high' ? 'warning' : 'info'
    } sx={{mt: 2}}>
      {gettext('Impact scope')}: {plan.impact.scope || operation?.impact_scope}.
      {' '}{gettext('Availability risk')}: {plan.impact.availability_risk || gettext('provider assessed')}.
      {' '}{plan.impact.data_movement_possible ?
        gettext('Data movement may occur.') : gettext('No data movement is expected.')}
    </Alert>}
    {result && <>
      <Alert severity="info" sx={{mt: 2}}>
        {gettext('The provider response was recorded. Finality remains provider-owned; review the returned state and any required post-state validation.')}
      </Alert>
      <Box component="pre" aria-label={gettext('Provider operation result')}
        sx={{mt: 1, p: 1, overflow: 'auto', maxHeight: 320,
          bgcolor: 'background.default'}}>
        {JSON.stringify(result.provider_result ?? result, null, 2)}
      </Box>
    </>}
    {operation?.confirmation_required && plan?.state === 'ready' &&
      <FormControlLabel control={<Checkbox checked={confirmed}
        onChange={(event) => setConfirmed(event.target.checked)} />}
      label={gettext('I confirm this provider-planned operation.')} />}
    <Box sx={{display: 'flex', gap: 1, mt: 2}}>
      <Button variant="contained" disabled={working || !operation ||
        (operation.target_required && !targetId)} onClick={preview}>
        {gettext('Validate and preview')}
      </Button>
      <Button color="warning" disabled={working || plan?.state !== 'ready' ||
        !plan?.execution_available || (operation?.confirmation_required && !confirmed)}
      onClick={apply}>{gettext('Apply provider plan')}</Button>
      {working && <CircularProgress size={24} />}
    </Box>
  </Box>;
}

VisualAdministration.propTypes = {
  catalog: PropTypes.object,
  resources: PropTypes.array,
  selectedResource: PropTypes.object,
  resourceGeneration: PropTypes.string,
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

function editorValue(value) {
  if (value === null) return 'null';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value ?? '');
}

function nativeValue(value) {
  if (value === 'null') return null;
  if (value === 'true') return true;
  if (value === 'false') return false;
  if (/^-?(?:\d+\.?\d*|\d*\.\d+)$/.test(value)) return Number(value);
  try {
    if (value.startsWith('{') || value.startsWith('[')) {
      return JSON.parse(value);
    }
  } catch {
    // Preserve invalid JSON-looking text as a text cell value.
  }
  return value;
}

const KEY_VALUE_KINDS = new Set([
  'key', 'string', 'hash', 'list', 'set', 'sorted-set', 'stream',
  'geospatial', 'bitmap', 'hyperloglog', 'vector-set',
]);

function KeyValueDataGrid({catalog, resources, post, setError}) {
  const keys = (resources || []).filter(
    (item) => KEY_VALUE_KINDS.has(item.resource_kind)
  );
  const [targetId, setTargetId] = useState(keys[0]?.resource_id || '');
  const [page, setPage] = useState(null);
  const [edits, setEdits] = useState({});
  const [newValue, setNewValue] = useState('{}');
  const [deleteCandidate, setDeleteCandidate] = useState(null);
  const [working, setWorking] = useState(false);
  const target = keys.find((item) => item.resource_id === targetId);
  const descriptor = (catalog?.objects || []).find(
    (item) => item.resource_kind === target?.resource_kind
  );
  const admitted = (operationId) => (descriptor?.operations || []).some(
    (item) => item.operation_id === operationId && item.execution_available
  );

  useEffect(() => {
    if (!keys.some((item) => item.resource_id === targetId)) {
      setTargetId(keys[0]?.resource_id || '');
      setPage(null);
    }
  }, [keys, targetId]);

  const load = async (continuation=null) => {
    if (!target) return;
    setWorking(true);
    setError(null);
    try {
      const nextPage = await post({
        action: 'visual_admin_rows',
        request: {target_resource: target, limit: 200, continuation},
      });
      setPage(nextPage);
      setEdits({});
      setDeleteCandidate(null);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const mutate = async (operationId, draft, confirmed=false) => {
    const request = {
      resource_kind: target.resource_kind, operation_id: operationId,
      target_resource: target, draft,
    };
    const validation = await post({
      action: 'visual_admin_validate', request,
    });
    if (!validation.valid) {
      throw new Error(validation.errors.map((item) => item.message).join(' '));
    }
    const plan = await post({action: 'visual_admin_plan', request});
    if (plan.state !== 'ready' || !plan.execution_available) {
      throw new Error(
        plan.blockers?.join(', ') || gettext('The key operation is blocked.')
      );
    }
    await post({
      action: 'visual_admin_apply',
      request: {
        plan_id: plan.plan_id, plan_digest: plan.plan_digest, confirmed,
      },
    });
  };

  const saveValue = async (row, index) => {
    setWorking(true);
    setError(null);
    try {
      const original = JSON.stringify(row.values.value, null, 2);
      const source = edits[index] ?? original;
      if (source === original) return;
      await mutate('update', {
        selector: {identity_token: row.identity_token},
        changes: {value: JSON.parse(source)},
        concurrency_token: row.identity_token,
      });
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const insertValue = async () => {
    setWorking(true);
    setError(null);
    try {
      await mutate('insert', {values: JSON.parse(newValue), options: {}});
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const deleteValue = async (row) => {
    if (deleteCandidate !== row.identity_token) {
      setDeleteCandidate(row.identity_token);
      return;
    }
    setWorking(true);
    setError(null);
    try {
      await mutate('delete', {
        selector: {identity_token: row.identity_token},
        concurrency_token: row.identity_token,
        confirmation: 'provider-value-delete',
      }, true);
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  return <Box sx={{p: 2, overflow: 'auto', flex: 1}}>
    <Box sx={{display: 'flex', gap: 1, alignItems: 'center'}}>
      <TextField select sx={{minWidth: 320}}
        label={gettext('Key and native data type')} value={targetId}
        onChange={(event) => {
          setTargetId(event.target.value); setPage(null);
        }}>
        {keys.map((item) => <MenuItem key={item.resource_id}
          value={item.resource_id}>{item.resource_kind}: {item.display_name}</MenuItem>)}
      </TextField>
      <Button variant="contained" disabled={working || !target}
        onClick={() => load()}>{gettext('Load values')}</Button>
      {working && <CircularProgress size={24} />}
    </Box>
    {keys.length === 0 && <Alert severity="info" sx={{mt: 2}}>
      {gettext('No discovered Redis key is available.')}
    </Alert>}
    {target && <Box component="pre" aria-label={gettext('Redis key metadata')}
      sx={{mt: 1, p: 1, maxHeight: 160, overflow: 'auto',
        bgcolor: 'background.default'}}>
      {JSON.stringify(target.extensions || target.native || {}, null, 2)}
    </Box>}
    {page && <>
      <Alert severity="info" sx={{mt: 2}}>
        {gettext('Edits retain Redis native types and use route-bound, single-use value identities with optimistic concurrency checks.')}
      </Alert>
      {(page.rows || []).map((row, index) => <Box key={row.identity_token}
        sx={{mt: 1, p: 1, border: 1, borderColor: 'divider'}}>
        <Box component="code">{JSON.stringify(row.values.selector)}</Box>
        <TextField fullWidth multiline minRows={2} maxRows={10} sx={{mt: 1}}
          disabled={working || !admitted('update')}
          value={edits[index] ?? JSON.stringify(row.values.value, null, 2)}
          onChange={(event) => setEdits((current) => ({
            ...current, [index]: event.target.value,
          }))} />
        <Box sx={{display: 'flex', gap: 1, mt: 1}}>
          <Button disabled={working || !admitted('update')}
            onClick={() => saveValue(row, index)}>{gettext('Save')}</Button>
          <Button color="warning" disabled={working || !admitted('delete')}
            onClick={() => deleteValue(row)}>
            {deleteCandidate === row.identity_token ?
              gettext('Confirm delete') : gettext('Delete')}
          </Button>
        </Box>
      </Box>)}
      {page.continuation && <Box sx={{display: 'flex', gap: 1, mt: 1}}>
        <Button onClick={() => load(page.continuation)}>
          {gettext('Next value page')}
        </Button>
      </Box>}
      {admitted('insert') && <Box sx={{mt: 2}}>
        <TextField fullWidth multiline minRows={3}
          label={gettext('Native insert value (JSON)')} value={newValue}
          onChange={(event) => setNewValue(event.target.value)} />
        <Button sx={{mt: 1}} onClick={insertValue} disabled={working}>
          {gettext('Insert value')}
        </Button>
      </Box>}
    </>}
  </Box>;
}

KeyValueDataGrid.propTypes = {
  catalog: PropTypes.object,
  resources: PropTypes.array,
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

function StructuredDataGrid({catalog, resources, post, setError}) {
  const tables = (resources || []).filter(
    (item) => item.resource_kind === 'table'
  );
  const tableDescriptor = (catalog?.objects || []).find(
    (item) => item.resource_kind === 'table'
  );
  const operations = tableDescriptor?.operations || [];
  const admitted = (operationId) => operations.some(
    (item) => item.operation_id === operationId && item.execution_available
  );
  const [targetId, setTargetId] = useState(tables[0]?.resource_id || '');
  const [page, setPage] = useState(null);
  const [edits, setEdits] = useState({});
  const [newValues, setNewValues] = useState({});
  const [deleteCandidate, setDeleteCandidate] = useState(null);
  const [working, setWorking] = useState(false);
  const target = tables.find((item) => item.resource_id === targetId);

  useEffect(() => {
    if (!tables.some((item) => item.resource_id === targetId)) {
      setTargetId(tables[0]?.resource_id || '');
      setPage(null);
    }
  }, [tables, targetId]);

  const load = async (continuation=null) => {
    if (!target) return;
    setWorking(true);
    setError(null);
    try {
      const nextPage = await post({
        action: 'visual_admin_rows',
        request: {target_resource: target, limit: 200, continuation},
      });
      setPage(nextPage);
      setEdits({});
      setNewValues({});
      setDeleteCandidate(null);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const mutate = async (operationId, draft, confirmed=false) => {
    const request = {
      resource_kind: 'table', operation_id: operationId,
      target_resource: target, draft,
    };
    const validation = await post({
      action: 'visual_admin_validate', request,
    });
    if (!validation.valid) {
      throw new Error(validation.errors.map((item) => item.message).join(' '));
    }
    const plan = await post({action: 'visual_admin_plan', request});
    if (plan.state !== 'ready' || !plan.execution_available) {
      throw new Error(
        plan.blockers?.join(', ') || gettext('The row plan is blocked.')
      );
    }
    await post({
      action: 'visual_admin_apply',
      request: {
        plan_id: plan.plan_id, plan_digest: plan.plan_digest, confirmed,
      },
    });
  };

  const saveRow = async (row, index) => {
    setWorking(true);
    setError(null);
    try {
      const changes = {};
      Object.entries(edits[index] || {}).forEach(([name, value]) => {
        if (value !== editorValue(row.values[name])) {
          changes[name] = nativeValue(value);
        }
      });
      if (Object.keys(changes).length === 0) return;
      await mutate('update', {
        selector: {identity_token: row.identity_token}, changes,
        concurrency_token: row.identity_token,
      });
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const insertRow = async () => {
    setWorking(true);
    setError(null);
    try {
      const values = {};
      Object.entries(newValues).forEach(([name, value]) => {
        if (value !== '') values[name] = nativeValue(value);
      });
      await mutate('insert', {values, options: {}});
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const deleteRow = async (row) => {
    if (deleteCandidate !== row.identity_token) {
      setDeleteCandidate(row.identity_token);
      return;
    }
    setWorking(true);
    setError(null);
    try {
      await mutate('delete', {
        selector: {identity_token: row.identity_token},
        concurrency_token: row.identity_token,
        confirmation: 'provider-row-delete',
      }, true);
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  return <Box sx={{p: 2, overflow: 'auto', flex: 1}}>
    <Box sx={{display: 'flex', gap: 1, alignItems: 'center'}}>
      <TextField select sx={{minWidth: 260}} label={gettext('Table')}
        value={targetId} onChange={(event) => {
          setTargetId(event.target.value); setPage(null);
        }}>
        {tables.map((item) => <MenuItem key={item.resource_id}
          value={item.resource_id}>{(item.display_path || [item.display_name]).join('.')}</MenuItem>)}
      </TextField>
      <Button variant="contained" disabled={working || !target}
        onClick={load}>{gettext('Load rows')}</Button>
      {working && <CircularProgress size={24} />}
    </Box>
    {tables.length === 0 && <Alert severity="info" sx={{mt: 2}}>
      {gettext('No discovered base table is available.')}
    </Alert>}
    {page && <>
      <Alert severity={page.editable ? 'info' : 'warning'} sx={{mt: 2}}>
        {page.editable ? gettext('Edits use provider-issued native row identities.') :
          gettext('This table is read-only because the provider did not admit a stable row identity.')}
      </Alert>
      <Box component="table" sx={{mt: 2, width: '100%', borderCollapse: 'collapse'}}>
        <thead><tr>{page.columns.map((column) =>
          <th align="left" key={column.name}>{column.name}{column.key ? ' 🔑' : ''}</th>)}
        <th>{gettext('Actions')}</th></tr></thead>
        <tbody>
          {page.rows.map((row, index) => <tr key={row.identity_token || index}>
            {page.columns.map((column) => <td key={column.name}>
              <TextField size="small" value={edits[index]?.[column.name] ?? editorValue(row.values[column.name])}
                disabled={!page.editable || column.editable === false || working}
                onChange={(event) => setEdits((current) => ({
                  ...current,
                  [index]: {...current[index], [column.name]: event.target.value},
                }))} />
            </td>)}
            <td><Box sx={{display: 'flex', gap: 1}}>
              <Button disabled={working || !row.identity_token || !admitted('update')}
                onClick={() => saveRow(row, index)}>{gettext('Save')}</Button>
              <Button color="warning" disabled={working || !row.identity_token || !admitted('delete')}
                onClick={() => deleteRow(row)}>{deleteCandidate === row.identity_token ?
                  gettext('Confirm delete') : gettext('Delete')}</Button>
            </Box></td>
          </tr>)}
          {admitted('insert') && <tr>
            {page.columns.map((column) => <td key={column.name}>
              <TextField size="small" placeholder={gettext('New value')}
                value={newValues[column.name] || ''}
                disabled={column.editable === false}
                onChange={(event) => setNewValues((current) => ({
                  ...current, [column.name]: event.target.value,
                }))} />
            </td>)}
            <td><Button disabled={working || Object.keys(newValues).length === 0}
              onClick={insertRow}>{gettext('Insert row')}</Button></td>
          </tr>}
        </tbody>
      </Box>
    </>}
    {page?.continuation && <Box sx={{display: 'flex', gap: 1, mt: 1}}>
      <Button disabled={working} onClick={() => load(page.continuation)}>
        {gettext('Next row page')}</Button>
      <Button disabled={working} onClick={async () => {
        await post({action: 'visual_admin_rows_cancel', request: {
          continuation: page.continuation,
        }}); setPage({...page, continuation: null, complete: true});
      }}>{gettext('Cancel row cursor')}</Button>
    </Box>}
  </Box>;
}

StructuredDataGrid.propTypes = {
  catalog: PropTypes.object,
  resources: PropTypes.array,
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

const ANALYTIC_CONTAINER_KINDS = {
  'time-series-analytic': ['table'],
  'vector-analytic': ['collection'],
  'search-analytic': ['index'],
  'search-document-analytic': ['index'],
  'columnar-analytic': ['table'],
  'wide-column': ['table'],
  'bitemporal-document-relational': ['table'],
};

function AnalyticDataBrowser({modelFamily, resources, post, setError}) {
  const kinds = ANALYTIC_CONTAINER_KINDS[modelFamily] || [];
  const containers = (resources || []).filter(
    (item) => kinds.includes(item.resource_kind)
  );
  const [targetId, setTargetId] = useState(containers[0]?.resource_id || '');
  const [filterSource, setFilterSource] = useState('{}');
  const [page, setPage] = useState(null);
  const [working, setWorking] = useState(false);
  const target = containers.find((item) => item.resource_id === targetId);

  useEffect(() => {
    if (!containers.some((item) => item.resource_id === targetId)) {
      setTargetId(containers[0]?.resource_id || '');
      setPage(null);
    }
  }, [containers, targetId]);

  const load = async (continuation=null) => {
    if (!target) return;
    setWorking(true);
    setError(null);
    try {
      const request = {target_resource: target, limit: 200, continuation};
      if (['vector-analytic', 'search-analytic',
        'search-document-analytic'].includes(modelFamily)) {
        request.filter = JSON.parse(filterSource || '{}');
      }
      setPage(await post({action: 'visual_admin_rows', request}));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const records = page?.records || page?.documents || [];
  const components = {
    'time-series-analytic': 'cdeadmin/results/TimeSeriesView',
    'vector-analytic': 'cdeadmin/results/VectorView',
    'search-analytic': 'cdeadmin/results/SearchView',
    'search-document-analytic': 'cdeadmin/results/SearchView',
    'columnar-analytic': 'cdeadmin/results/ColumnarView',
    'wide-column': 'cdeadmin/results/WideColumnView',
    'bitemporal-document-relational':
      'cdeadmin/results/BitemporalDocumentView',
  };
  const viewModel = ['columnar-analytic', 'wide-column'].includes(modelFamily) ? {
    rows: records, columns: page?.columns || page?.schema?.columns || [],
    native_observation: page?.native_observation || {},
  } : {
    records, schema: page?.schema || page?.metadata || {},
    temporal_fields: page?.temporal_fields || {},
  };
  const specializedResult = page ? {
    component_reference: components[modelFamily],
    view_model: viewModel,
  } : null;
  return <Box sx={{p: 2, overflow: 'auto', flex: 1}}>
    <Box sx={{display: 'flex', gap: 1, alignItems: 'center'}}>
      <TextField select sx={{minWidth: 280}}
        label={gettext('Analytic data container')} value={targetId}
        onChange={(event) => {setTargetId(event.target.value); setPage(null);}}>
        {containers.map((item) => <MenuItem key={item.resource_id}
          value={item.resource_id}>
          {(item.display_path || [item.display_name]).join('.')}
        </MenuItem>)}
      </TextField>
      <Button variant="contained" disabled={working || !target}
        onClick={() => load()}>{gettext('Load data')}</Button>
      {working && <CircularProgress size={24} />}
    </Box>
    {['vector-analytic', 'search-analytic',
      'search-document-analytic'].includes(modelFamily) &&
      <TextField fullWidth multiline
        minRows={2} sx={{mt: 2}} label={gettext('Native filter (JSON)')}
        value={filterSource}
        onChange={(event) => setFilterSource(event.target.value)} />}
    {containers.length === 0 && <Alert severity="info" sx={{mt: 2}}>
      {gettext('No discovered analytic data container is available.')}
    </Alert>}
    {page && <Alert severity="info" sx={{mt: 2}}>
      {gettext('Use the Administration tab for provider-validated inserts, updates, deletes, schema changes, indexes, retention, and security operations.')}
    </Alert>}
    {specializedResult && <ResultView rendered={specializedResult} />}
    {page?.continuation && <Button sx={{mt: 1}} disabled={working}
      onClick={() => load(page.continuation)}>{gettext('Next data page')}</Button>}
  </Box>;
}

AnalyticDataBrowser.propTypes = {
  modelFamily: PropTypes.string.isRequired,
  resources: PropTypes.array,
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

function ResultTable({rendered}) {
  const view = rendered?.view_model;
  const columns = view?.columns || [];
  const rows = view?.rows || [];
  const names = columns.map((item) => item.name);
  return (
    <Box sx={{overflow: 'auto', mt: 1, flex: 1}}>
      <Box component="table" sx={{width: '100%', borderCollapse: 'collapse'}}>
        <thead><tr>{names.map((name) => (
          <th align="left" key={name}>{name}</th>
        ))}</tr></thead>
        <tbody>{rows.map((row, index) => (
          <tr key={index}>{names.map((name) => (
            <td key={name}>{String(row?.[name] ?? '')}</td>
          ))}</tr>
        ))}</tbody>
      </Box>
      {rows.length === 0 && <Box>{gettext('Query completed without rows.')}</Box>}
    </Box>
  );
}

ResultTable.propTypes = {rendered: PropTypes.object};

function KeyValueView({rendered}) {
  const entries = rendered?.view_model?.entries ||
    rendered?.view_model?.records || rendered?.view_model?.rows || [];
  return <Box aria-label={gettext('Key-value results')}
    sx={{overflow: 'auto', mt: 1, flex: 1}}>
    {entries.map((entry, index) => <Box key={index}
      sx={{p: 1, mb: 1, border: 1, borderColor: 'divider'}}>
      <Box component="code">{entry.command || gettext('Redis reply')}</Box>
      <Box component="pre" sx={{whiteSpace: 'pre-wrap', mb: 0}}>
        {JSON.stringify(entry.value, null, 2)}
      </Box>
      {entry.outcome && <Box component="small">{entry.outcome}</Box>}
    </Box>)}
    {entries.length === 0 && <Box>
      {gettext('Command completed without values.')}
    </Box>}
  </Box>;
}

KeyValueView.propTypes = {rendered: PropTypes.object};

function resultCell(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function WideColumnView({rendered}) {
  const view = rendered?.view_model || {};
  const columns = view.columns || [];
  const rows = view.rows || [];
  const warnings = view.native_observation?.warnings || [];
  return <Box aria-label={gettext('Wide-column results')}
    sx={{overflow: 'auto', mt: 1, flex: 1}}>
    {warnings.length > 0 && <Alert severity="warning" sx={{mb: 1}}>
      {warnings.join(' ')}
    </Alert>}
    <Box component="table" sx={{width: '100%', borderCollapse: 'collapse'}}>
      <thead><tr>{columns.map((column) => <th align="left" key={column.name}>
        <Box>{column.name}</Box>
        <Box component="small">{column.type || gettext('CQL value')}</Box>
      </th>)}</tr></thead>
      <tbody>{rows.map((row, index) => <tr key={index}>
        {columns.map((column) => <td key={column.name}>
          <Box component="code" sx={{whiteSpace: 'pre-wrap'}}>
            {resultCell(row?.[column.name])}
          </Box>
        </td>)}
      </tr>)}</tbody>
    </Box>
    {rows.length === 0 && <Box>
      {gettext('Query completed without wide-column rows.')}
    </Box>}
  </Box>;
}

WideColumnView.propTypes = {rendered: PropTypes.object};

function ColumnarView({rendered}) {
  const view = rendered?.view_model || {};
  const columns = view.columns || [];
  const rows = view.rows || [];
  return <Box aria-label={gettext('Columnar results')}
    sx={{overflow: 'auto', mt: 1, flex: 1}}>
    <Box component="table" sx={{borderCollapse: 'collapse', minWidth: '100%'}}>
      <thead><tr>{columns.map((column) => <Box component="th"
        key={column.name} sx={{border: 1, borderColor: 'divider', p: 0.5,
          textAlign: 'left'}}>{column.name}<br/><small>{column.type}</small>
      </Box>)}</tr></thead>
      <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>
        {columns.map((column) => <Box component="td" key={column.name}
          sx={{border: 1, borderColor: 'divider', p: 0.5}}>
          {resultCell(row?.[column.name])}
        </Box>)}
      </tr>)}</tbody>
    </Box>
    {rows.length === 0 && <Box>
      {gettext('Query completed without columnar rows.')}
    </Box>}
  </Box>;
}

ColumnarView.propTypes = {rendered: PropTypes.object};

function CubePivotView({rendered}) {
  const view = rendered?.view_model || {};
  const [transposed, setTransposed] = useState(false);
  const [drillDepth, setDrillDepth] = useState((view.levels || []).length);
  const levels = (view.levels || []).slice(0, drillDepth);
  const measures = view.measures || [];
  const cells = view.cells || [];
  const exportCellset = (format) => {
    let content;
    let type;
    if (format === 'json') {
      content = JSON.stringify(view, null, 2);
      type = 'application/json';
    } else {
      const quote = (value) => `"${String(value ?? '').replaceAll('"', '""')}"`;
      const names = [...levels, ...measures];
      content = [names, ...cells.map((cell) => [
        ...levels.map((name) => cell.coordinates?.[name]),
        ...measures.map((name) => cell.measures?.[name]),
      ])].map((row) => row.map(quote).join(',')).join('\n');
      type = 'text/csv';
    }
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([content], {type}));
    link.download = `cellset.${format}`;
    link.click();
    URL.revokeObjectURL(link.href);
  };
  return <Box aria-label={gettext('Cube pivot results')}
    sx={{overflow: 'auto', mt: 1, flex: 1}}>
    <Box sx={{display: 'flex', gap: 1, alignItems: 'center', mb: 1}}>
      <Button onClick={() => setTransposed((value) => !value)}>
        {gettext('Transpose axes')}</Button>
      <Button disabled={drillDepth >= (view.levels || []).length}
        onClick={() => setDrillDepth((value) => value + 1)}>
        {gettext('Drill down')}</Button>
      <Button disabled={drillDepth <= 1}
        onClick={() => setDrillDepth((value) => value - 1)}>
        {gettext('Drill up')}</Button>
      <Button onClick={() => exportCellset('csv')}>{gettext('Export CSV')}</Button>
      <Button onClick={() => exportCellset('json')}>{gettext('Export JSON')}</Button>
    </Box>
    {(view.slice || []).length > 0 && <Alert severity="info" sx={{mb: 1}}>
      {gettext('Active slice')}: {JSON.stringify(view.slice)}
    </Alert>}
    <Box component="table" sx={{borderCollapse: 'collapse', minWidth: '100%'}}>
      <thead><tr>{(transposed ? measures : levels).map((name) =>
        <th align="left" key={name}>{name}</th>)}
      {(transposed ? levels : measures).map((name) =>
        <th align="left" key={name}>{name}</th>)}</tr></thead>
      <tbody>{cells.map((cell, index) => <tr key={index}>
        {(transposed ? measures : levels).map((name) => <td key={name}>
          {resultCell(transposed ? cell.measures?.[name] :
            cell.coordinates?.[name])}</td>)}
        {(transposed ? levels : measures).map((name) => <td key={name}>
          {resultCell(transposed ? cell.coordinates?.[name] :
            cell.measures?.[name])}</td>)}
      </tr>)}</tbody>
    </Box>
    {cells.length === 0 && <Box>{gettext('Query completed without cells.')}</Box>}
  </Box>;
}

CubePivotView.propTypes = {rendered: PropTypes.object};

function TimeSeriesView({rendered}) {
  const records = rendered?.view_model?.records || [];
  const schema = rendered?.view_model?.schema || {};
  const timeField = schema.time_field || 'time';
  return <Box aria-label={gettext('Time-series results')}
    sx={{overflow: 'auto', mt: 1, flex: 1}}>
    <Box sx={{display: 'flex', gap: 2, flexWrap: 'wrap', mb: 1}}>
      {schema.measurement && <Box><strong>{gettext('Measurement')}:</strong>
        {' '}{resultCell(schema.measurement)}</Box>}
      {schema.tags && <Box><strong>{gettext('Tags')}:</strong>
        {' '}{resultCell(schema.tags)}</Box>}
      {schema.fields && <Box><strong>{gettext('Fields')}:</strong>
        {' '}{resultCell(schema.fields)}</Box>}
      {(schema.retention || schema.retention_policy) && <Box>
        <strong>{gettext('Retention')}:</strong>{' '}
        {resultCell(schema.retention || schema.retention_policy)}</Box>}
    </Box>
    <Box component="table" sx={{borderCollapse: 'collapse', minWidth: '100%'}}>
      <thead><tr><th align="left">{timeField}</th>
        <th align="left">{gettext('Series values')}</th></tr></thead>
      <tbody>{records.map((record, index) => <tr key={index}>
        <td><Box component="code">{resultCell(record?.[timeField])}</Box></td>
        <td><Box component="pre" sx={{whiteSpace: 'pre-wrap', mb: 0}}>
          {JSON.stringify(Object.fromEntries(Object.entries(record || {}).filter(
            ([name]) => name !== timeField
          )), null, 2)}
        </Box></td>
      </tr>)}</tbody>
    </Box>
    {records.length === 0 && <Box>
      {gettext('Query completed without time-series points.')}
    </Box>}
  </Box>;
}

TimeSeriesView.propTypes = {rendered: PropTypes.object};

function VectorView({rendered}) {
  const records = rendered?.view_model?.records || [];
  const schema = rendered?.view_model?.schema || {};
  return <Box aria-label={gettext('Vector results')}
    sx={{overflow: 'auto', mt: 1, flex: 1}}>
    {Object.keys(schema).length > 0 && <Box component="pre"
      aria-label={gettext('Vector collection metadata')}
      sx={{p: 1, bgcolor: 'background.default', whiteSpace: 'pre-wrap'}}>
      {JSON.stringify(schema, null, 2)}
    </Box>}
    {records.map((record, index) => <Box key={index}
      sx={{p: 1, mb: 1, border: 1, borderColor: 'divider'}}>
      <Box sx={{display: 'flex', gap: 2}}>
        <Box component="strong">{String(record?.id ?? gettext('Vector hit'))}</Box>
        {record?.distance != null && <Box component="code">
          {gettext('distance')}: {String(record.distance)}
        </Box>}
        {record?.score != null && <Box component="code">
          {gettext('score')}: {String(record.score)}
        </Box>}
      </Box>
      <Box component="pre" sx={{whiteSpace: 'pre-wrap', mb: 0}}>
        {JSON.stringify(record?.entity ?? record, null, 2)}
      </Box>
    </Box>)}
    {records.length === 0 && <Box>
      {gettext('Query completed without vector matches.')}
    </Box>}
  </Box>;
}

VectorView.propTypes = {rendered: PropTypes.object};

function SearchView({rendered}) {
  const records = rendered?.view_model?.records || [];
  return <Box aria-label={gettext('Search results')}
    sx={{overflow: 'auto', mt: 1, flex: 1}}>
    {records.map((hit, index) => <Box key={`${hit?._index || ''}:${hit?._id || index}`}
      sx={{p: 1, mb: 1, border: 1, borderColor: 'divider'}}>
      <Box sx={{display: 'flex', gap: 2, flexWrap: 'wrap'}}>
        <Box component="strong">{hit?._index || gettext('Search hit')}</Box>
        {hit?._id != null && <Box component="code">{String(hit._id)}</Box>}
        {hit?._score != null && <Box component="code">
          {gettext('score')}: {String(hit._score)}
        </Box>}
      </Box>
      <Box component="pre" sx={{whiteSpace: 'pre-wrap', mb: 0}}>
        {JSON.stringify(hit?._source ?? hit, null, 2)}
      </Box>
      {hit?.highlight && <Alert severity="info" sx={{mt: 1}}>
        {JSON.stringify(hit.highlight)}
      </Alert>}
    </Box>)}
    {records.length === 0 && <Box>
      {gettext('Query completed without search hits.')}
    </Box>}
  </Box>;
}

SearchView.propTypes = {rendered: PropTypes.object};

function DocumentTreeView({rendered}) {
  const records = rendered?.view_model?.records || [];
  return <Box aria-label={gettext('Document results')}
    sx={{overflow: 'auto', mt: 1, flex: 1}}>
    {records.map((record, index) => <Box component="pre" key={index}
      sx={{p: 1, mb: 1, bgcolor: 'background.default', whiteSpace: 'pre-wrap'}}>
      {JSON.stringify(record, null, 2)}
    </Box>)}
    {records.length === 0 && <Box>{gettext('Query completed without documents.')}</Box>}
  </Box>;
}

DocumentTreeView.propTypes = {rendered: PropTypes.object};

function QueryPlanView({rendered}) {
  const view = rendered?.view_model || {};
  const nodes = view.nodes || view.plan || view.records || view.rows || view;
  return <Box aria-label={gettext('Query plan results')}
    sx={{overflow: 'auto', mt: 1, flex: 1}}>
    <Alert severity="info" sx={{mb: 1}}>
      {gettext('This plan is the provider\'s native presentation; CDEadmin does not reinterpret cost, finality, or execution state.')}
    </Alert>
    <Box component="pre" sx={{whiteSpace: 'pre-wrap', mb: 0}}>
      {JSON.stringify(nodes, null, 2)}
    </Box>
  </Box>;
}

QueryPlanView.propTypes = {rendered: PropTypes.object};

function BitemporalDocumentView({rendered}) {
  const records = rendered?.view_model?.records || [];
  const temporal = rendered?.view_model?.temporal_fields || {};
  const temporalNames = [
    ...(temporal.valid_time || []), ...(temporal.system_time || []),
  ];
  return <Box aria-label={gettext('Bitemporal document results')}
    sx={{overflow: 'auto', mt: 1, flex: 1}}>
    {records.map((record, index) => <Box key={index}
      sx={{p: 1, mb: 1, border: 1, borderColor: 'divider'}}>
      <Box sx={{display: 'flex', gap: 2, flexWrap: 'wrap', mb: 1}}>
        <Box component="strong">{String(record?._id ?? gettext('Document'))}</Box>
        {temporalNames.filter((name) => record?.[name] != null).map((name) =>
          <Box component="small" key={name}>
            {name}: {resultCell(record[name])}
          </Box>)}
      </Box>
      <Box component="pre" sx={{whiteSpace: 'pre-wrap', mb: 0}}>
        {JSON.stringify(record, null, 2)}
      </Box>
    </Box>)}
    {records.length === 0 && <Box>
      {gettext('Query completed without bitemporal documents.')}
    </Box>}
  </Box>;
}

BitemporalDocumentView.propTypes = {rendered: PropTypes.object};

function graphEntities(records) {
  const nodes = new Map();
  const relationships = new Map();
  const visit = (value) => {
    if (Array.isArray(value)) {
      value.forEach(visit);
    } else if (value && typeof value === 'object') {
      if (value.kind === 'node' && value.element_id) {
        nodes.set(value.element_id, value);
      } else if (value.kind === 'relationship' && value.element_id) {
        relationships.set(value.element_id, value);
      } else if (value.kind === 'path') {
        visit(value.nodes); visit(value.relationships);
      } else {
        Object.values(value).forEach(visit);
      }
    }
  };
  visit(records);
  return {nodes: [...nodes.values()], relationships: [...relationships.values()]};
}

function GraphView({rendered, records: suppliedRecords}) {
  const records = suppliedRecords || rendered?.view_model?.records || [];
  const graph = useMemo(() => graphEntities(records), [records]);
  const positions = useMemo(() => new Map(graph.nodes.map((node, index) => {
    const angle = (2 * Math.PI * index) / Math.max(graph.nodes.length, 1);
    return [node.element_id, {
      x: 300 + 220 * Math.cos(angle), y: 220 + 160 * Math.sin(angle),
    }];
  })), [graph.nodes]);
  return <Box aria-label={gettext('Graph results')} sx={{mt: 1, overflow: 'auto'}}>
    <Box component="svg" viewBox="0 0 600 440" role="img"
      aria-label={gettext('Neo4j graph visualization')}
      sx={{width: '100%', minWidth: 500, maxHeight: 440, bgcolor: 'background.default'}}>
      {graph.relationships.map((relationship) => {
        const start = positions.get(relationship.start_node_element_id);
        const end = positions.get(relationship.end_node_element_id);
        if (!start || !end) return null;
        return <g key={relationship.element_id}>
          <line x1={start.x} y1={start.y} x2={end.x} y2={end.y}
            stroke="currentColor" strokeWidth="2" />
          <text x={(start.x + end.x) / 2} y={(start.y + end.y) / 2 - 5}
            textAnchor="middle" fontSize="11">{relationship.type}</text>
        </g>;
      })}
      {graph.nodes.map((node) => {
        const point = positions.get(node.element_id);
        const label = node.labels?.join(':') || node.element_id;
        return <g key={node.element_id}>
          <circle cx={point.x} cy={point.y} r="34" fill="#4c9bd6"
            stroke="currentColor" />
          <text x={point.x} y={point.y + 4} textAnchor="middle"
            fontSize="11" fill="white">{label.slice(0, 18)}</text>
        </g>;
      })}
    </Box>
    <Box component="table" sx={{width: '100%', mt: 1}}>
      <thead><tr><th align="left">{gettext('Element')}</th>
        <th align="left">{gettext('Type')}</th>
        <th align="left">{gettext('Properties')}</th></tr></thead>
      <tbody>{[...graph.nodes, ...graph.relationships].map((item) => <tr
        key={`${item.kind}:${item.element_id}`}>
        <td>{item.element_id}</td>
        <td>{item.kind === 'node' ? item.labels?.join(':') : item.type}</td>
        <td><Box component="pre" sx={{whiteSpace: 'pre-wrap'}}>
          {JSON.stringify(item.properties || {}, null, 2)}</Box></td>
      </tr>)}</tbody>
    </Box>
    {graph.nodes.length === 0 && <Box>
      {gettext('Query completed without graph entities.')}
    </Box>}
  </Box>;
}

GraphView.propTypes = {
  rendered: PropTypes.object,
  records: PropTypes.array,
};

function ResultView({rendered}) {
  if (rendered?.descriptor?.result_kind === 'plan' ||
    rendered?.component_reference === 'cdeadmin/results/PlanView') {
    return <QueryPlanView rendered={rendered} />;
  }
  if (rendered?.component_reference === 'cdeadmin/results/CubePivotView') {
    return <CubePivotView rendered={rendered} />;
  }
  if (rendered?.component_reference === 'cdeadmin/results/KeyValueView') {
    return <KeyValueView rendered={rendered} />;
  }
  if (rendered?.component_reference === 'cdeadmin/results/WideColumnView') {
    return <WideColumnView rendered={rendered} />;
  }
  if (rendered?.component_reference === 'cdeadmin/results/ColumnarView') {
    return <ColumnarView rendered={rendered} />;
  }
  if (rendered?.component_reference === 'cdeadmin/results/TimeSeriesView') {
    return <TimeSeriesView rendered={rendered} />;
  }
  if (rendered?.component_reference === 'cdeadmin/results/VectorView') {
    return <VectorView rendered={rendered} />;
  }
  if (rendered?.component_reference === 'cdeadmin/results/SearchView') {
    return <SearchView rendered={rendered} />;
  }
  if (rendered?.component_reference === 'cdeadmin/results/GraphView') {
    return <GraphView rendered={rendered} />;
  }
  if (rendered?.component_reference ===
    'cdeadmin/results/BitemporalDocumentView') {
    return <BitemporalDocumentView rendered={rendered} />;
  }
  if (Array.isArray(rendered?.view_model?.records)) {
    return <DocumentTreeView rendered={rendered} />;
  }
  return <ResultTable rendered={rendered} />;
}

ResultView.propTypes = {rendered: PropTypes.object};

export function semanticCrossFilter(definition, chart, selection) {
  const encoding = chart?.encodings?.x;
  if (!encoding || selection?.value === undefined) return null;
  let reference = null;
  for (const dimension of definition?.dimensions || []) {
    if (dimension.id === encoding) reference = dimension.field;
    for (const hierarchy of dimension.hierarchies || []) {
      const level = (hierarchy.levels || []).find((item) =>
        item.id === encoding);
      if (level) reference = level.field;
    }
  }
  if (!reference) return null;
  return selection.value === null ? {
    field: reference, operator: 'is_null',
  } : {
    field: reference, operator: 'eq', value: selection.value,
  };
}

function SemanticChartView({chart, rendered, onSelect}) {
  const records = rendered?.view_model?.records || [];
  const chartType = chart.chart_type;
  if (['table', 'pivot', 'graph', 'vector-neighbors'].includes(chartType)) {
    return <ResultView rendered={rendered} />;
  }
  const xField = chart.encodings?.x;
  const yField = chart.encodings?.y;
  const points = records.map((record, index) => ({
    label: String(record?.[xField] ?? index + 1),
    x: Number(record?.[xField]),
    y: Number(record?.[yField]),
    record,
  })).filter((point) => Number.isFinite(point.y));
  if (!points.length) {
    return <Alert severity="info" sx={{mt: 1}}>
      {gettext('The provider result has no numeric values for this chart encoding.')}
    </Alert>;
  }
  if (chartType === 'metric') {
    return <Box aria-label={gettext('Semantic metric result')}
      sx={{fontSize: '2rem', fontWeight: 'bold', mt: 1}}>{points[0].y}</Box>;
  }
  const nativeType = chartType === 'area' || chartType === 'timeline' ?
    'line' : chartType === 'histogram' ? 'bar' : chartType;
  const plotted = nativeType === 'scatter' ? points.filter((point) =>
    Number.isFinite(point.x)) : points;
  const data = nativeType === 'scatter' ? {
    datasets: [{label: chart.name, data: plotted.map((point) =>
      ({x: point.x, y: point.y})),
    backgroundColor: '#4c9bd6'}],
  } : {
    labels: plotted.map((point) => point.label),
    datasets: [{label: chart.name, data: plotted.map((point) => point.y),
      backgroundColor: '#4c9bd6', borderColor: '#2878b5',
      fill: chartType === 'area'}],
  };
  const selectPoint = (_event, elements) => {
    const point = elements?.[0] && plotted[elements[0].index];
    if (point && xField && onSelect) onSelect({
      encoding: xField, value: point.record?.[xField], label: point.label,
    });
  };
  return <Box aria-label={gettext('Semantic chart result')}
    sx={{height: 260, mt: 1}}>
    <BaseChart id={`semantic-chart-${chart.id}`} type={nativeType}
      data={data} options={{responsive: true, maintainAspectRatio: false,
        parsing: true, plugins: {legend: {display: true}},
        onClick: selectPoint}} />
  </Box>;
}

SemanticChartView.propTypes = {
  chart: PropTypes.object.isRequired,
  rendered: PropTypes.object.isRequired,
  onSelect: PropTypes.func,
};

export function ResultControls({rendered, history, post, onRendered, setError,
  setBusy, allowedFormats, deliveryProfiles=[]}) {
  const [comparison, setComparison] = useState(null);
  const [deliveryProfileId, setDeliveryProfileId] = useState(
    deliveryProfiles[0]?.profile_id || '');
  const [deliveryTarget, setDeliveryTarget] = useState('');
  const [deliveryFormat, setDeliveryFormat] = useState('');
  const [deliveryOccurrence, setDeliveryOccurrence] = useState(null);
  const resultId = rendered?.descriptor?.result_id;
  const exportFormats = (rendered?.descriptor?.export_formats || []).filter(
    (format) => !allowedFormats || allowedFormats.includes(format));
  const deliveryProfile = deliveryProfiles.find(
    (item) => item.profile_id === deliveryProfileId);
  const deliverableFormats = exportFormats.filter((format) =>
    deliveryProfile?.allowed_formats?.includes(format));
  const selectedDeliveryFormat = deliverableFormats.includes(deliveryFormat) ?
    deliveryFormat : (deliverableFormats[0] || '');
  const previous = [...history].reverse().find((item) =>
    item.descriptor?.result_id !== resultId);
  const perform = async (callback) => {
    setBusy(true);
    setError(null);
    try {
      await callback();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  };
  if (!resultId) return null;
  return <Box sx={{mt: 1}}>
    <Box sx={{display: 'flex', gap: 1, flexWrap: 'wrap'}}
      aria-label={gettext('Result actions')}>
      <Button disabled={!rendered.page?.next_cursor} onClick={() => perform(
        async () => onRendered(await post({action: 'result_page', request: {
          result_id: resultId, cursor: rendered.page.next_cursor,
          page_size: rendered.page.page_size || 500,
        }})))}>{gettext('Next result page')}</Button>
      {exportFormats.map((format) =>
        <Button key={format} onClick={() => perform(async () => downloadBase64(
          await post({action: 'result_export', request: {
            result_id: resultId, format,
          }})
        ))}>{gettext('Export')} {format.toUpperCase()}</Button>)}
      <Button disabled={!previous} onClick={() => perform(async () =>
        setComparison(await post({action: 'result_compare', request: {
          left_result_id: previous.descriptor.result_id,
          right_result_id: resultId,
        }})))}>{gettext('Compare with previous result')}</Button>
    </Box>
    {deliveryProfiles.length > 0 && <Box sx={{display: 'grid', mt: 1,
      gridTemplateColumns: '1fr 1fr 2fr auto', gap: 1}}
    aria-label={gettext('Authenticated report delivery')}>
      <TextField select label={gettext('Delivery profile')}
        value={deliveryProfileId} onChange={(event) => {
          setDeliveryProfileId(event.target.value);
          setDeliveryFormat('');
          setDeliveryOccurrence(null);
        }}>
        {deliveryProfiles.map((profile) => <MenuItem
          key={profile.profile_id} value={profile.profile_id}>
          {profile.label} ({profile.kind.toUpperCase()})
        </MenuItem>)}
      </TextField>
      <TextField select label={gettext('Delivery format')}
        value={selectedDeliveryFormat} onChange={(event) =>
          setDeliveryFormat(event.target.value)}>
        {deliverableFormats.map((format) => <MenuItem key={format}
          value={format}>{format.toUpperCase()}</MenuItem>)}
      </TextField>
      <TextField value={deliveryTarget} onChange={(event) =>
        setDeliveryTarget(event.target.value)} label={deliveryProfile?.kind ===
        'smtp' ? gettext('Recipients (comma separated)') :
        gettext('Object filename')} />
      <Button disabled={!deliveryProfile || !selectedDeliveryFormat ||
        !deliveryTarget.trim()} onClick={() => perform(async () => {
        const target = deliveryProfile.kind === 'smtp' ? {
          recipients: deliveryTarget.split(',').map((item) => item.trim())
            .filter(Boolean),
        } : {object_name: deliveryTarget.trim()};
        setDeliveryOccurrence(await post({action: 'result_delivery',
          request: {
            request_key: deliveryRequestId(), result_id: resultId,
            format: selectedDeliveryFormat,
            profile_id: deliveryProfile.profile_id, target,
          }}));
      })}>{gettext('Deliver')}</Button>
    </Box>}
    {deliveryOccurrence && <Alert sx={{mt: 1}} severity={
      deliveryOccurrence.state === 'delivered' ? 'success' : 'warning'}>
      {gettext('Delivery state')}: {deliveryOccurrence.state}. {
        gettext('Automatic retry is disabled.')}
    </Alert>}
    {comparison && <Box component="pre"
      aria-label={gettext('Result comparison')}
      sx={{p: 1, mt: 1, overflow: 'auto', maxHeight: 260,
        bgcolor: 'background.default', whiteSpace: 'pre-wrap'}}>
      {JSON.stringify(comparison, null, 2)}
    </Box>}
  </Box>;
}

ResultControls.propTypes = {
  rendered: PropTypes.object,
  history: PropTypes.array.isRequired,
  post: PropTypes.func.isRequired,
  onRendered: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
  setBusy: PropTypes.func.isRequired,
  allowedFormats: PropTypes.array,
  deliveryProfiles: PropTypes.array,
};

function DocumentDataGrid({catalog, resources, post, setError}) {
  const collections = (resources || []).filter(
    (item) => item.resource_kind === 'collection'
  );
  const documentDescriptor = (catalog?.objects || []).find(
    (item) => item.resource_kind === 'document'
  );
  const operations = documentDescriptor?.operations || [];
  const admitted = (operationId) => operations.some(
    (item) => item.operation_id === operationId && item.execution_available
  );
  const [targetId, setTargetId] = useState(collections[0]?.resource_id || '');
  const [page, setPage] = useState(null);
  const [edits, setEdits] = useState({});
  const [newDocument, setNewDocument] = useState('{}');
  const [deleteCandidate, setDeleteCandidate] = useState(null);
  const [filterSource, setFilterSource] = useState('{}');
  const [projectionSource, setProjectionSource] = useState('{}');
  const [sortSource, setSortSource] = useState('[]');
  const [unsetPaths, setUnsetPaths] = useState('');
  const [diff, setDiff] = useState(null);
  const [working, setWorking] = useState(false);
  const target = collections.find((item) => item.resource_id === targetId);

  useEffect(() => {
    if (!collections.some((item) => item.resource_id === targetId)) {
      setTargetId(collections[0]?.resource_id || '');
      setPage(null);
    }
  }, [collections, targetId]);

  const load = async (continuation=null) => {
    if (!target) return;
    setWorking(true);
    setError(null);
    try {
      const nextPage = await post({
        action: 'visual_admin_rows',
        request: {
          target_resource: target, limit: 200, continuation,
          filter: JSON.parse(filterSource || '{}'),
          projection: JSON.parse(projectionSource || '{}'),
          sort: JSON.parse(sortSource || '[]'),
        },
      });
      setPage(nextPage);
      setEdits({});
      setDeleteCandidate(null);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const mutate = async (operationId, draft, confirmed=false) => {
    const request = {
      resource_kind: 'document', operation_id: operationId,
      target_resource: target, draft,
    };
    const validation = await post({
      action: 'visual_admin_validate', request,
    });
    if (!validation.valid) {
      throw new Error(validation.errors.map((item) => item.message).join(' '));
    }
    const plan = await post({action: 'visual_admin_plan', request});
    if (plan.state !== 'ready' || !plan.execution_available) {
      throw new Error(
        plan.blockers?.join(', ') || gettext('The document plan is blocked.')
      );
    }
    await post({
      action: 'visual_admin_apply',
      request: {
        plan_id: plan.plan_id, plan_digest: plan.plan_digest, confirmed,
      },
    });
  };

  const saveDocument = async (document, index) => {
    setWorking(true);
    setError(null);
    try {
      const replacement = JSON.parse(
        edits[index] ?? JSON.stringify(document, null, 2)
      );
      const selector = {_id: document._id};
      if (replacement._id !== undefined &&
          JSON.stringify(replacement._id) !== JSON.stringify(document._id)) {
        throw new Error(gettext('MongoDB _id is immutable.'));
      }
      delete replacement._id;
      const unset = unsetPaths.split(',').map((item) => item.trim())
        .filter(Boolean).reduce((value, path) => ({...value, [path]: ''}), {});
      const changes = Object.keys(unset).length ? {
        $set: replacement, $unset: unset,
      } : replacement;
      const before = JSON.stringify(document, null, 2).split('\n');
      const after = JSON.stringify({...replacement, _id: document._id}, null, 2).split('\n');
      setDiff({before, after, unset: Object.keys(unset)});
      await mutate('update', {selector, changes});
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const insertDocument = async () => {
    setWorking(true);
    setError(null);
    try {
      await mutate('insert', {
        values: JSON.parse(newDocument), options: {},
      });
      setNewDocument('{}');
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const deleteDocument = async (document, index) => {
    if (deleteCandidate !== index) {
      setDeleteCandidate(index);
      return;
    }
    setWorking(true);
    setError(null);
    try {
      await mutate('delete', {
        selector: {_id: document._id}, confirmation: 'delete-document',
      }, true);
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  return <Box sx={{p: 2, overflow: 'auto', flex: 1}}>
    <Box sx={{display: 'flex', gap: 1, alignItems: 'center'}}>
      <TextField select sx={{minWidth: 260}} label={gettext('Collection')}
        value={targetId} onChange={(event) => {
          setTargetId(event.target.value); setPage(null);
        }}>
        {collections.map((item) => <MenuItem key={item.resource_id}
          value={item.resource_id}>{(item.display_path || [item.display_name]).join('.')}</MenuItem>)}
      </TextField>
      <Button variant="contained" disabled={working || !target}
        onClick={() => load()}>{gettext('Load documents')}</Button>
      {working && <CircularProgress size={24} />}
    </Box>
    <Box sx={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 1, mt: 2}}>
      <TextField multiline minRows={2} label={gettext('Filter builder (Extended JSON)')}
        value={filterSource} onChange={(event) => setFilterSource(event.target.value)} />
      <TextField multiline minRows={2} label={gettext('Projection builder')}
        value={projectionSource} onChange={(event) => setProjectionSource(event.target.value)} />
      <TextField multiline minRows={2} label={gettext('Sort builder')}
        value={sortSource} onChange={(event) => setSortSource(event.target.value)} />
    </Box>
    <TextField fullWidth sx={{mt: 1}} label={gettext('Unset nested paths (comma separated)')}
      value={unsetPaths} onChange={(event) => setUnsetPaths(event.target.value)}
      helperText={gettext('Uses MongoDB $unset and never permits _id.')} />
    {collections.length === 0 && <Alert severity="info" sx={{mt: 2}}>
      {gettext('No discovered collection is available.')}
    </Alert>}
    {page?.documents?.map((document, index) => <Box key={index}
      sx={{mt: 2, display: 'flex', gap: 1, alignItems: 'flex-start'}}>
      <TextField fullWidth multiline minRows={4} maxRows={14}
        inputProps={{'aria-label': gettext('Document JSON')}}
        value={edits[index] ?? JSON.stringify(document, null, 2)}
        disabled={working}
        onChange={(event) => setEdits((current) => ({
          ...current, [index]: event.target.value,
        }))} />
      <Box sx={{display: 'flex', flexDirection: 'column', gap: 1}}>
        <Button disabled={working || !admitted('update')}
          onClick={() => saveDocument(document, index)}>{gettext('Save')}</Button>
        <Button color="warning" disabled={working || !admitted('delete')}
          onClick={() => deleteDocument(document, index)}>
          {deleteCandidate === index ? gettext('Confirm delete') : gettext('Delete')}
        </Button>
      </Box>
    </Box>)}
    {page?.schema_sample && <Box sx={{mt: 2}}>
      <Box>{gettext('Schema sample')}: {page.schema_sample.sample_size}</Box>
      <Box component="table" sx={{width: '100%'}}><thead><tr>
        <th align="left">{gettext('Path')}</th><th>{gettext('Types')}</th>
        <th>{gettext('Present')}</th><th>{gettext('Missing')}</th>
      </tr></thead><tbody>{page.schema_sample.fields.map((field) => <tr key={field.path}>
        <td>{field.path}</td><td>{field.types.join(', ')}</td>
        <td>{field.present_count}</td><td>{field.missing_count}</td>
      </tr>)}</tbody></Box>
    </Box>}
    {diff && <Box component="pre" aria-label={gettext('Nested document diff')}
      sx={{mt: 2, maxHeight: 220, overflow: 'auto'}}>{JSON.stringify(diff, null, 2)}</Box>}
    {page?.continuation && <Box sx={{display: 'flex', gap: 1, mt: 2}}>
      <Button disabled={working} onClick={() => load(page.continuation)}>
        {gettext('Next document batch')}
      </Button>
      <Button disabled={working} onClick={async () => {
        await post({action: 'visual_admin_rows_cancel', request: {
          continuation: page.continuation,
        }}); setPage({...page, continuation: null, complete: true});
      }}>{gettext('Cancel document cursor')}</Button>
    </Box>}
    {page && admitted('insert') && <Box sx={{mt: 2, display: 'flex', gap: 1}}>
      <TextField fullWidth multiline minRows={4}
        label={gettext('New document JSON')} value={newDocument}
        onChange={(event) => setNewDocument(event.target.value)} />
      <Button disabled={working || !newDocument.trim()}
        onClick={insertDocument}>{gettext('Insert document')}</Button>
    </Box>}
  </Box>;
}

DocumentDataGrid.propTypes = {
  catalog: PropTypes.object,
  resources: PropTypes.array,
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

function GraphDataStudio({catalog, resources, post, setError}) {
  const graphs = (resources || []).filter((item) => item.resource_kind === 'graph');
  const nodeDescriptor = (catalog?.objects || []).find(
    (item) => item.resource_kind === 'node'
  );
  const relationshipDescriptor = (catalog?.objects || []).find(
    (item) => item.resource_kind === 'relationship'
  );
  const admitted = (descriptor, operationId) => (descriptor?.operations || []).some(
    (item) => item.operation_id === operationId && item.execution_available
  );
  const [targetId, setTargetId] = useState(graphs[0]?.resource_id || '');
  const [page, setPage] = useState(null);
  const [labels, setLabels] = useState('Node');
  const [properties, setProperties] = useState('{}');
  const [edits, setEdits] = useState({});
  const [relationshipType, setRelationshipType] = useState('RELATED_TO');
  const [relationshipStart, setRelationshipStart] = useState('');
  const [relationshipEnd, setRelationshipEnd] = useState('');
  const [relationshipProperties, setRelationshipProperties] = useState('{}');
  const [deleteCandidate, setDeleteCandidate] = useState(null);
  const [working, setWorking] = useState(false);
  const target = graphs.find((item) => item.resource_id === targetId);

  const load = async (continuation=null) => {
    if (!target) return;
    setWorking(true); setError(null);
    try {
      setPage(await post({action: 'visual_admin_rows', request: {
        target_resource: target, limit: 200, filter: {}, continuation,
      }}));
      setEdits({});
      setDeleteCandidate(null);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const mutate = async (resourceKind, operationId, targetResource, draft,
    confirmed=false) => {
    const request = {resource_kind: resourceKind, operation_id: operationId,
      target_resource: targetResource, draft};
    const validation = await post({action: 'visual_admin_validate', request});
    if (!validation.valid) {
      throw new Error(validation.errors.map((item) => item.message).join(' '));
    }
    const plan = await post({action: 'visual_admin_plan', request});
    if (plan.state !== 'ready' || !plan.execution_available) {
      throw new Error(plan.blockers?.join(', ') || gettext('The graph plan is blocked.'));
    }
    return post({action: 'visual_admin_apply', request: {
      plan_id: plan.plan_id, plan_digest: plan.plan_digest, confirmed,
    }});
  };

  const insertNode = async () => {
    setWorking(true); setError(null);
    try {
      await mutate('node', 'insert', target, {values: {
        labels: labels.split(',').map((item) => item.trim()).filter(Boolean),
        properties: JSON.parse(properties || '{}'),
      }, options: {}});
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const entityTarget = (resourceKind, entity) => {
    const native = {element_id: entity.element_id};
    return {
      resource_kind: resourceKind,
      resource_id: `neo4j:${resourceKind}:${entity.element_id}`,
      extensions: {neo4j: {native}}, display_name: entity.element_id,
    };
  };

  const saveEntity = async (resourceKind, entity) => {
    setWorking(true); setError(null);
    try {
      const source = edits[`${resourceKind}:${entity.element_id}`] ??
        JSON.stringify(entity.properties || {}, null, 2);
      await mutate(resourceKind, 'update', entityTarget(resourceKind, entity), {
        selector: {element_id: entity.element_id},
        changes: {properties: JSON.parse(source)},
      });
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const deleteEntity = async (resourceKind, entity) => {
    const candidate = `${resourceKind}:${entity.element_id}`;
    if (deleteCandidate !== candidate) {
      setDeleteCandidate(candidate);
      return;
    }
    setWorking(true); setError(null);
    try {
      await mutate(resourceKind, 'delete', entityTarget(resourceKind, entity), {
        selector: {element_id: entity.element_id},
        confirmation: `delete-${resourceKind}`,
      }, true);
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const insertRelationship = async () => {
    setWorking(true); setError(null);
    try {
      await mutate('relationship', 'insert', target, {values: {
        type: relationshipType,
        start_node_element_id: relationshipStart,
        end_node_element_id: relationshipEnd,
        properties: JSON.parse(relationshipProperties || '{}'),
      }, options: {}});
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const entities = graphEntities(page?.records || []);
  return <Box sx={{p: 2, overflow: 'auto', flex: 1}}>
    <Box sx={{display: 'flex', gap: 1, alignItems: 'center'}}>
      <TextField select sx={{minWidth: 260}} label={gettext('Graph database')}
        value={targetId} onChange={(event) => setTargetId(event.target.value)}>
        {graphs.map((item) => <MenuItem key={item.resource_id}
          value={item.resource_id}>{item.display_name}</MenuItem>)}
      </TextField>
      <Button variant="contained" disabled={working || !target}
        onClick={load}>{gettext('Load graph')}</Button>
      {working && <CircularProgress size={24} />}
    </Box>
    {page && <GraphView records={page.records || []} />}
    {entities.nodes.map((node) => <Box key={node.element_id}
      sx={{display: 'grid', gridTemplateColumns: '1fr 2fr auto auto', gap: 1,
        alignItems: 'center', mt: 1}}>
      <Box>{node.labels?.join(':')} — {node.element_id}</Box>
      <TextField multiline minRows={2} label={gettext('Node properties')}
        value={edits[`node:${node.element_id}`] ??
          JSON.stringify(node.properties || {}, null, 2)}
        onChange={(event) => setEdits((current) => ({...current,
          [`node:${node.element_id}`]: event.target.value}))} />
      <Button disabled={working || !admitted(nodeDescriptor, 'update')}
        onClick={() => saveEntity('node', node)}>{gettext('Save')}</Button>
      <Button color="warning"
        disabled={working || !admitted(nodeDescriptor, 'delete')}
        onClick={() => deleteEntity('node', node)}>{deleteCandidate ===
          `node:${node.element_id}` ? gettext('Confirm delete node') :
          gettext('Delete node')}</Button>
    </Box>)}
    <Box sx={{display: 'grid', gridTemplateColumns: '1fr 2fr auto', gap: 1, mt: 2}}>
      <TextField label={gettext('Labels (comma separated)')} value={labels}
        onChange={(event) => setLabels(event.target.value)} />
      <TextField multiline minRows={3} label={gettext('Node properties JSON')}
        value={properties} onChange={(event) => setProperties(event.target.value)} />
      <Button disabled={working || !target || !admitted(nodeDescriptor, 'insert')}
        onClick={insertNode}>{gettext('Create node')}</Button>
    </Box>
    {entities.relationships.map((relationship) => <Box
      key={relationship.element_id}
      sx={{display: 'grid', gridTemplateColumns: '1fr 2fr auto auto', gap: 1,
        alignItems: 'center', mt: 1}}>
      <Box>{relationship.type} — {relationship.element_id}</Box>
      <TextField multiline minRows={2} label={gettext('Relationship properties')}
        value={edits[`relationship:${relationship.element_id}`] ??
          JSON.stringify(relationship.properties || {}, null, 2)}
        onChange={(event) => setEdits((current) => ({...current,
          [`relationship:${relationship.element_id}`]: event.target.value}))} />
      <Button disabled={working || !admitted(relationshipDescriptor, 'update')}
        onClick={() => saveEntity('relationship', relationship)}>{gettext('Save')}</Button>
      <Button color="warning"
        disabled={working || !admitted(relationshipDescriptor, 'delete')}
        onClick={() => deleteEntity('relationship', relationship)}>
        {deleteCandidate === `relationship:${relationship.element_id}` ?
          gettext('Confirm delete relationship') :
          gettext('Delete relationship')}</Button>
    </Box>)}
    <Box sx={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 2fr auto',
      gap: 1, mt: 2}}>
      <TextField label={gettext('Relationship type')} value={relationshipType}
        onChange={(event) => setRelationshipType(event.target.value)} />
      <TextField select label={gettext('Start node')} value={relationshipStart}
        onChange={(event) => setRelationshipStart(event.target.value)}>
        {entities.nodes.map((node) => <MenuItem key={node.element_id}
          value={node.element_id}>{node.element_id}</MenuItem>)}
      </TextField>
      <TextField select label={gettext('End node')} value={relationshipEnd}
        onChange={(event) => setRelationshipEnd(event.target.value)}>
        {entities.nodes.map((node) => <MenuItem key={node.element_id}
          value={node.element_id}>{node.element_id}</MenuItem>)}
      </TextField>
      <TextField multiline minRows={3} label={gettext('Relationship properties JSON')}
        value={relationshipProperties}
        onChange={(event) => setRelationshipProperties(event.target.value)} />
      <Button disabled={working || !target || !relationshipStart ||
        !relationshipEnd || !admitted(relationshipDescriptor, 'insert')}
      onClick={insertRelationship}>{gettext('Create relationship')}</Button>
    </Box>
    {graphs.length === 0 && <Alert severity="info" sx={{mt: 2}}>
      {gettext('No discovered graph database is available.')}
    </Alert>}
    {page?.continuation && <Box sx={{display: 'flex', gap: 1, mt: 1}}>
      <Button disabled={working} onClick={() => load(page.continuation)}>
        {gettext('Next graph page')}</Button>
      <Button disabled={working} onClick={async () => {
        await post({action: 'visual_admin_rows_cancel', request: {
          continuation: page.continuation,
        }}); setPage({...page, continuation: null, complete: true});
      }}>{gettext('Cancel graph cursor')}</Button>
    </Box>}
  </Box>;
}

GraphDataStudio.propTypes = {
  catalog: PropTypes.object,
  resources: PropTypes.array,
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

function ChangeStreamViewer({resources, languageProfile, post, setError}) {
  const collections = (resources || []).filter(
    (item) => item.resource_kind === 'collection'
  );
  const [targetId, setTargetId] = useState(collections[0]?.resource_id || '');
  const [pipeline, setPipeline] = useState('[]');
  const [resumeToken, setResumeToken] = useState('');
  const [sessionId, setSessionId] = useState(null);
  const [occurrenceId, setOccurrenceId] = useState(null);
  const [events, setEvents] = useState([]);
  const [working, setWorking] = useState(false);
  const target = collections.find((item) => item.resource_id === targetId);

  const poll = async (id=occurrenceId) => {
    if (!id) return;
    setWorking(true);
    try {
      const response = await post({action: 'poll', occurrence_id: id});
      const records = response.rendered_result?.view_model?.records || [];
      setEvents((current) => [...current, ...records]);
      const token = response.occurrence?.result?.extensions?.mongodb?.payload?.resume_token;
      if (token) setResumeToken(JSON.stringify(token));
      if (response.occurrence?.operation?.terminal) setOccurrenceId(null);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const start = async () => {
    if (!target) return;
    setWorking(true);
    setError(null);
    try {
      let activeSession = sessionId;
      if (!activeSession) {
        const opened = await post({
          action: 'open_session', language_profile: languageProfile,
        });
        activeSession = opened.session_id;
        setSessionId(activeSession);
      }
      const native = target.extensions?.mongodb?.native || {};
      const request = {
        operation: 'watch', database: native.database,
        collection: native.collection, pipeline: JSON.parse(pipeline || '[]'),
        batch_size: 100, max_documents: 100000,
      };
      if (resumeToken.trim()) request.resume_after = JSON.parse(resumeToken);
      const occurrence = await post({
        action: 'execute', session_id: activeSession,
        source: JSON.stringify(request),
      });
      setOccurrenceId(occurrence.occurrence_id);
      setEvents([]);
      await poll(occurrence.occurrence_id);
    } catch (requestError) {
      setError(errorMessage(requestError));
      setWorking(false);
    }
  };

  const cancel = async () => {
    if (!occurrenceId) return;
    setWorking(true);
    try {
      await post({action: 'cancel', occurrence_id: occurrenceId});
      setOccurrenceId(null);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  return <Box sx={{p: 2, overflow: 'auto', flex: 1}}>
    <Box sx={{display: 'flex', gap: 1, alignItems: 'center'}}>
      <TextField select sx={{minWidth: 280}} label={gettext('Collection')}
        value={targetId} onChange={(event) => setTargetId(event.target.value)}>
        {collections.map((item) => <MenuItem key={item.resource_id}
          value={item.resource_id}>{(item.display_path || []).join('.')}</MenuItem>)}
      </TextField>
      <Button variant="contained" disabled={working || !target || Boolean(occurrenceId)}
        onClick={start}>{gettext('Open change stream')}</Button>
      <Button disabled={working || !occurrenceId} onClick={() => poll()}>
        {gettext('Poll next batch')}
      </Button>
      <Button disabled={working || !occurrenceId} onClick={cancel}>
        {gettext('Cancel stream')}</Button>
      {working && <CircularProgress size={24} />}
    </Box>
    <TextField fullWidth multiline minRows={3} sx={{mt: 2}}
      label={gettext('Change-stream pipeline')} value={pipeline}
      onChange={(event) => setPipeline(event.target.value)} />
    <TextField fullWidth multiline minRows={2} sx={{mt: 2}}
      label={gettext('Resume token (canonical Extended JSON)')}
      value={resumeToken} onChange={(event) => setResumeToken(event.target.value)} />
    <Box aria-label={gettext('Change stream events')} sx={{mt: 2}}>
      {events.map((event, index) => <Box component="pre" key={index}
        sx={{p: 1, bgcolor: 'background.default'}}>{JSON.stringify(event, null, 2)}</Box>)}
    </Box>
  </Box>;
}

ChangeStreamViewer.propTypes = {
  resources: PropTypes.array,
  languageProfile: PropTypes.string,
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

function portableId(value, fallback='item') {
  const text = String(value || fallback).trim().replace(/[^A-Za-z0-9_-]+/g, '_');
  return (/^[A-Za-z]/.test(text) ? text : `item_${text}`).slice(0, 128);
}

function emptySemanticModel(resources, analyticalProfile=null) {
  const sourceKinds = analyticalProfile?.source_kinds ||
    ['table', 'collection', 'index'];
  const resource = (resources || []).find((item) =>
    sourceKinds.includes(item.resource_kind)
  );
  const relation = resource?.display_path || [resource?.display_name || 'source'];
  return {
    contract_version: '1.0.0', name: gettext('New semantic model'),
    semantic_family: analyticalProfile?.semantic_family || 'relational',
    description: '', sources: resource ? [{
      id: 'source', resource_id: resource.resource_id,
      relation, alias: 'source', source_kind: resource.resource_kind,
      classification: analyticalProfile?.source_classifications?.[0] || 'fact',
      grain: [], provider_config: {},
    }] : [], joins: [], relationships: [], dimensions: [], measures: [],
    parameters: [], default_filters: [], materializations: [],
    security: {row_filters: [], tenant_filter: null, roles: []},
    visualizations: [], dashboards: [], schedules: [], reports: [],
    annotations: {},
  };
}

function SemanticModelWorkspace({semantic, resources, post, setError}) {
  const analyticalProfile = semantic?.capabilities?.analytical_profile || {};
  const [items, setItems] = useState(semantic?.items || []);
  const [selectedId, setSelectedId] = useState('');
  const [record, setRecord] = useState(null);
  const [definition, setDefinition] = useState(
    emptySemanticModel(resources, analyticalProfile)
  );
  const [panel, setPanel] = useState('model');
  const [working, setWorking] = useState(false);
  const [validation, setValidation] = useState(null);
  const [lineage, setLineage] = useState(null);
  const [history, setHistory] = useState([]);
  const [comparison, setComparison] = useState(null);
  const [compiled, setCompiled] = useState(null);
  const [materializationPlan, setMaterializationPlan] = useState(null);
  const [occurrenceId, setOccurrenceId] = useState(null);
  const [rendered, setRendered] = useState(null);
  const [resultHistory, setResultHistory] = useState([]);
  const [diagnostics, setDiagnostics] = useState(null);
  const [activeChartId, setActiveChartId] = useState(null);
  const [dashboardResults, setDashboardResults] = useState({});
  const [dashboardSelections, setDashboardSelections] = useState({});
  const [reportResults, setReportResults] = useState([]);
  const [detailFieldsDraft, setDetailFieldsDraft] = useState('');
  const [query, setQuery] = useState({
    axes: {rows: [], columns: [], pages: []}, measures: [], filters: [],
    totals: false, limit: 500,
    parameters: {}, cross_filters: [], drill: {
      mode: 'summary', target_level: null, detail_fields: [],
    }, time_intelligence: {},
    windows: [],
  });
  const [dimensionDraft, setDimensionDraft] = useState({
    id: 'dimension', name: 'Dimension', source_id: 'source', field: '',
    hierarchy: 'default', level: 'level', dimension_kind: 'attribute',
    time_role: 'calendar-time', timezone: 'UTC',
    fiscal_year_start_month: 1,
    time_value_type: 'datetime',
  });
  const [hierarchyDraft, setHierarchyDraft] = useState({
    dimension_id: '', id: 'hierarchy', name: 'Hierarchy',
  });
  const [levelDraft, setLevelDraft] = useState({
    dimension_id: '', hierarchy_id: '', id: 'level', name: 'Level',
    source_id: 'source', field: '',
  });
  const [measureDraft, setMeasureDraft] = useState({
    id: 'measure', name: 'Measure', source_id: 'source', field: '',
    aggregation: 'sum', format: '', measure_kind: 'aggregate',
    certification_status: 'uncertified', certification_owner: '',
  });
  const [calculationDraft, setCalculationDraft] = useState({
    id: 'calculation', name: 'Calculated measure', left: '', right: '',
    operator: 'divide', format: '',
  });
  const [joinDraft, setJoinDraft] = useState({
    left_source: '', right_source: '', left_field: '', right_field: '',
    join_type: 'inner', cardinality: 'many-to-one',
  });
  const [relationshipDraft, setRelationshipDraft] = useState({
    from_source: '', to_source: '', relationship_kind:
      analyticalProfile?.relationship_kinds?.[0] || 'native-edge',
    name: 'Relationship',
  });
  const [filterDraft, setFilterDraft] = useState({
    source_id: 'source', field: '', operator: 'eq', value: '',
  });
  const [materializationDraft, setMaterializationDraft] = useState({
    id: 'rollup', name: 'Rollup', strategy: 'provider_managed', enabled: false,
  });
  const [parameterDraft, setParameterDraft] = useState({
    id: 'parameter', name: 'Parameter', type: 'string', required: false,
    default: '',
  });
  const [securityDraft, setSecurityDraft] = useState({
    source_id: 'source', field: '', operator: 'eq', value: '',
    principal_claim: 'user_id',
  });
  const [chartDraft, setChartDraft] = useState({
    id: 'chart', name: 'Chart', chart_type: 'bar', x: '', y: '',
  });
  const [dashboardDraft, setDashboardDraft] = useState({
    id: 'dashboard', name: 'Dashboard', visualization_id: '',
  });
  const [scheduleDraft, setScheduleDraft] = useState({
    id: 'schedule', name: 'Schedule', expression: '0 8 * * *',
    timezone: 'UTC', enabled: false,
  });
  const [reportDraft, setReportDraft] = useState({
    id: 'report', name: 'Report', dashboard_id: '', schedule_id: '',
    export_formats: 'json,csv,xlsx,svg,pdf',
  });
  const [windowDraft, setWindowDraft] = useState({
    id: 'window', measure_id: '', operation: 'running_sum',
    partition_by: [], order_level: '', direction: 'asc', frame_size: 1,
  });
  const sourceResources = (resources || []).filter((item) =>
    (analyticalProfile.source_kinds || ['table', 'collection', 'index'])
      .includes(item.resource_kind)
  );
  const columnResources = (resources || []).filter((item) =>
    ['column', 'field'].includes(item.resource_kind)
  );
  const levelOptions = useMemo(() => {
    const values = [];
    (definition.dimensions || []).forEach((dimension) => {
      values.push({id: dimension.id, name: dimension.name});
      (dimension.hierarchies || []).forEach((hierarchy) =>
        (hierarchy.levels || []).forEach((level) => values.push({
          id: level.id, name: `${dimension.name} / ${hierarchy.name} / ${level.name}`,
        })));
    });
    return values;
  }, [definition.dimensions]);

  const call = async (action, request={}) => post({action, request});
  const refreshList = async () => {
    const response = await call('semantic_model_list');
    setItems(response.items || []);
  };
  const loadModel = async (modelId) => {
    if (!modelId) return;
    setWorking(true); setError(null);
    try {
      const value = await call('semantic_model_get', {model_id: modelId});
      setRecord(value); setDefinition(value.definition); setSelectedId(modelId);
      setValidation(null); setCompiled(null); setRendered(null);
      setDashboardResults({}); setDashboardSelections({});
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally { setWorking(false); }
  };
  const validate = async () => {
    setWorking(true); setError(null);
    try {
      const value = await call('semantic_model_validate', {definition});
      setValidation(value); if (value.valid) setLineage(value.lineage);
      return value.valid;
    } catch (requestError) { setError(errorMessage(requestError)); return false; }
    finally { setWorking(false); }
  };
  const save = async () => {
    setWorking(true); setError(null);
    try {
      const value = record ? await call('semantic_model_update', {
        model_id: record.model_id, expected_revision: record.revision, definition,
      }) : await call('semantic_model_create', {definition});
      setRecord(value); setDefinition(value.definition); setSelectedId(value.model_id);
      await refreshList(); setValidation({valid: true, errors: []});
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setWorking(false); }
  };
  const setStatus = async (status) => {
    if (!record) return;
    setWorking(true); setError(null);
    try {
      const value = await call('semantic_model_status', {
        model_id: record.model_id, expected_revision: record.revision, status,
      });
      setRecord(value); await refreshList();
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setWorking(false); }
  };
  const remove = async () => {
    if (!record) return;
    setWorking(true); setError(null);
    try {
      await call('semantic_model_delete', {model_id: record.model_id,
        expected_revision: record.revision});
      setRecord(null); setSelectedId('');
      setDefinition(emptySemanticModel(resources, analyticalProfile));
      setDashboardResults({}); setDashboardSelections({});
      await refreshList();
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setWorking(false); }
  };
  const clone = async () => {
    if (!record) return;
    setWorking(true); setError(null);
    try {
      const value = await call('semantic_model_clone', {
        model_id: record.model_id, name: `${record.name} copy`,
      });
      setRecord(value); setDefinition(value.definition); setSelectedId(value.model_id);
      await refreshList();
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setWorking(false); }
  };
  const inspectHistory = async () => {
    if (!record) return;
    setWorking(true);
    try {
      const value = await call('semantic_model_history', {model_id: record.model_id});
      setHistory(value.items || []); setPanel('revisions');
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setWorking(false); }
  };
  const compareLatest = async () => {
    if (history.length < 2 || !record) return;
    try {
      setComparison(await call('semantic_model_compare', {
        model_id: record.model_id, left_revision: history[1].revision,
        right_revision: history[0].revision,
      }));
    } catch (requestError) { setError(errorMessage(requestError)); }
  };
  const compile = async (execute=false, queryValue=query) => {
    setWorking(true); setError(null); setRendered(null);
    try {
      const value = await call(execute ? 'semantic_query_execute' :
        'semantic_query_compile', {
        model_id: record?.model_id, definition: record ? undefined : definition,
        query: queryValue,
      });
      setCompiled(value.compiled);
      if (execute) {
        const id = value.occurrence.occurrence_id;
        setOccurrenceId(id);
        const response = await post({action: 'poll', occurrence_id: id});
        setRendered(response.rendered_result);
        setResultHistory((current) => [...current, response.rendered_result]);
        if (response.occurrence?.operation?.terminal) setOccurrenceId(null);
      }
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setWorking(false); }
  };
  const addSource = (resource) => {
    if (!resource) return;
    const base = portableId(resource.display_name,
      `source_${definition.sources.length + 1}`);
    const admitted = new Set(definition.sources.map((item) => item.id));
    let id = base;
    let suffix = 2;
    while (admitted.has(id)) { id = `${base}_${suffix}`; suffix += 1; }
    setDefinition((current) => ({...current, sources: [...current.sources, {
      id, resource_id: resource.resource_id,
      relation: resource.display_path || [resource.display_name], alias: id,
      source_kind: resource.resource_kind,
      classification: analyticalProfile.source_classifications?.[0] || 'fact',
      grain: [], provider_config: {},
    }]}));
  };
  const addDimension = () => {
    const id = portableId(dimensionDraft.id);
    const level = portableId(`${id}_${dimensionDraft.level}`, `${id}_level`);
    setDefinition((current) => ({...current, dimensions: [...current.dimensions, {
      id, name: dimensionDraft.name,
      field: {source_id: dimensionDraft.source_id, field: dimensionDraft.field},
      dimension_kind: dimensionDraft.dimension_kind,
      time_intelligence: dimensionDraft.dimension_kind === 'time' ? {
        role: dimensionDraft.time_role, calendar: 'gregorian',
        timezone: dimensionDraft.timezone,
        value_type: dimensionDraft.time_value_type,
        fiscal_year_start_month: Number(
          dimensionDraft.fiscal_year_start_month
        ),
      } : null,
      provider_config: {},
      hierarchies: [{id: portableId(dimensionDraft.hierarchy),
        name: dimensionDraft.hierarchy, levels: [{id: level, name: level,
          field: {source_id: dimensionDraft.source_id,
            field: dimensionDraft.field}}]}],
    }]}));
  };
  const addHierarchy = () => setDefinition((current) => ({...current,
    dimensions: current.dimensions.map((dimension) =>
      dimension.id === hierarchyDraft.dimension_id ? {...dimension,
        hierarchies: [...(dimension.hierarchies || []), {
          id: portableId(hierarchyDraft.id), name: hierarchyDraft.name,
          levels: [],
        }]} : dimension),
  }));
  const addLevel = () => setDefinition((current) => ({...current,
    dimensions: current.dimensions.map((dimension) =>
      dimension.id === levelDraft.dimension_id ? {...dimension,
        hierarchies: (dimension.hierarchies || []).map((hierarchy) =>
          hierarchy.id === levelDraft.hierarchy_id ? {...hierarchy,
            levels: [...hierarchy.levels, {
              id: portableId(levelDraft.id), name: levelDraft.name,
              field: {source_id: levelDraft.source_id,
                field: levelDraft.field},
            }]} : hierarchy)} : dimension),
  }));
  const removeLevel = (dimensionId, hierarchyId, levelId) =>
    setDefinition((current) => ({...current,
      dimensions: current.dimensions.map((dimension) =>
        dimension.id === dimensionId ? {...dimension,
          hierarchies: dimension.hierarchies.map((hierarchy) =>
            hierarchy.id === hierarchyId ? {...hierarchy,
              levels: hierarchy.levels.filter((level) => level.id !== levelId),
            } : hierarchy)} : dimension),
    }));
  const addMeasure = () => setDefinition((current) => ({...current,
    measures: [...current.measures, {id: portableId(measureDraft.id),
      name: measureDraft.name, aggregation: measureDraft.aggregation,
      field: measureDraft.aggregation === 'count' && !measureDraft.field ? null :
        {source_id: measureDraft.source_id, field: measureDraft.field},
      format: measureDraft.format, measure_kind: measureDraft.measure_kind,
      certification: {status: measureDraft.certification_status,
        owner: measureDraft.certification_owner, definition: ''}}],
  }));
  const addCalculation = () => setDefinition((current) => ({...current,
    measures: [...current.measures, {
      id: portableId(calculationDraft.id), name: calculationDraft.name,
      aggregation: 'none', field: null, format: calculationDraft.format,
      measure_kind: 'calculated', certification: {
        status: 'uncertified', owner: '', definition: '',
      },
      expression: {operator: calculationDraft.operator,
        left: {measure: calculationDraft.left},
        right: {measure: calculationDraft.right}},
    }],
  }));
  const addJoin = () => setDefinition((current) => ({...current,
    joins: [...current.joins, {id: `join_${current.joins.length + 1}`,
      left_source: joinDraft.left_source, right_source: joinDraft.right_source,
      join_type: joinDraft.join_type, cardinality: joinDraft.cardinality,
      predicates: [{operator: 'eq',
        left: {source_id: joinDraft.left_source, field: joinDraft.left_field},
        right: {source_id: joinDraft.right_source, field: joinDraft.right_field}}]}],
  }));
  const addRelationship = () => setDefinition((current) => ({...current,
    relationships: [...(current.relationships || []), {
      id: `relationship_${(current.relationships || []).length + 1}`,
      name: relationshipDraft.name,
      from_source: relationshipDraft.from_source,
      to_source: relationshipDraft.to_source,
      relationship_kind: relationshipDraft.relationship_kind,
      provider_config: {},
    }],
  }));
  const addFilter = (target='filters') => {
    let value = filterDraft.value;
    try { value = JSON.parse(value); } catch { /* retain text member */ }
    setQuery((current) => ({...current, [target]: [...(current[target] || []), {
      field: {source_id: filterDraft.source_id, field: filterDraft.field},
      operator: filterDraft.operator, value,
    }]}));
  };
  const addWindow = () => setQuery((current) => ({...current,
    windows: [...(current.windows || []), {
      id: portableId(windowDraft.id), measure_id: windowDraft.measure_id,
      operation: windowDraft.operation,
      partition_by: windowDraft.partition_by,
      order_by: {level_id: windowDraft.order_level,
        direction: windowDraft.direction},
      frame_size: Number(windowDraft.frame_size),
    }],
  }));
  const addParameter = () => {
    let defaultValue = parameterDraft.default;
    if (defaultValue !== '' && ['integer', 'number', 'boolean', 'array']
      .includes(parameterDraft.type)) {
      try {
        defaultValue = JSON.parse(defaultValue);
      } catch {
        setError(gettext('The parameter default must be valid JSON for its type.'));
        return;
      }
    }
    setDefinition((current) => ({...current,
      parameters: [...(current.parameters || []), {
        id: portableId(parameterDraft.id), name: parameterDraft.name,
        type: parameterDraft.type, required: parameterDraft.required,
        ...(defaultValue === '' ? {} : {default: defaultValue}),
        allowed_values: [],
      }],
    }));
  };
  const addSecurityFilter = () => {
    let value = securityDraft.value;
    try { value = JSON.parse(value); } catch { /* retain text member */ }
    setDefinition((current) => ({...current, security: {
      ...(current.security || {}),
      row_filters: [...(current.security?.row_filters || []), {
        field: {source_id: securityDraft.source_id,
          field: securityDraft.field},
        operator: securityDraft.operator, value,
      }],
    }}));
  };
  const addChart = () => setDefinition((current) => ({...current,
    visualizations: [...(current.visualizations || []), {
      id: portableId(chartDraft.id), name: chartDraft.name,
      chart_type: chartDraft.chart_type, query: {...query},
      encodings: {x: chartDraft.x, y: chartDraft.y},
    }],
  }));
  const addDashboard = () => setDefinition((current) => ({...current,
    dashboards: [...(current.dashboards || []), {
      id: portableId(dashboardDraft.id), name: dashboardDraft.name,
      cross_filtering: true, tiles: dashboardDraft.visualization_id ? [{
        visualization_id: dashboardDraft.visualization_id,
        layout: {x: 0, y: 0, width: 6, height: 4},
      }] : [],
    }],
  }));
  const addDashboardTile = (dashboardId, visualizationId) => {
    if (!visualizationId) return;
    setDefinition((current) => ({...current,
      dashboards: current.dashboards.map((dashboard) =>
        dashboard.id === dashboardId && !dashboard.tiles.some((tile) =>
          tile.visualization_id === visualizationId) ? {...dashboard,
            tiles: [...dashboard.tiles, {
              visualization_id: visualizationId,
              layout: {x: 0, y: dashboard.tiles.length * 4,
                width: 6, height: 4},
            }],
          } : dashboard),
    }));
  };
  const addSchedule = () => setDefinition((current) => ({...current,
    schedules: [...(current.schedules || []), {
      id: portableId(scheduleDraft.id), name: scheduleDraft.name,
      expression: scheduleDraft.expression, timezone: scheduleDraft.timezone,
      enabled: scheduleDraft.enabled, delivery: {},
    }],
  }));
  const addReport = () => setDefinition((current) => ({...current,
    reports: [...(current.reports || []), {
      id: portableId(reportDraft.id), name: reportDraft.name,
      dashboard_id: reportDraft.dashboard_id || null,
      schedule_id: reportDraft.schedule_id || null,
      export_formats: reportDraft.export_formats.split(',')
        .map((item) => item.trim()).filter(Boolean),
      parameters: {},
    }],
  }));
  const runReport = async (report) => {
    const dashboard = (definition.dashboards || []).find((item) =>
      item.id === report.dashboard_id);
    const charts = (dashboard?.tiles || []).map((tile) =>
      (definition.visualizations || []).find((item) =>
        item.id === tile.visualization_id)).filter(Boolean);
    if (!charts.length) {
      setError(gettext('The report dashboard has no chart queries.'));
      return;
    }
    setWorking(true); setError(null); setReportResults([]);
    try {
      const results = [];
      for (const chart of charts) {
        const value = await call('semantic_query_execute', {
          model_id: record?.model_id,
          definition: record ? undefined : definition,
          query: {...chart.query, parameters: {
            ...(chart.query.parameters || {}), ...(report.parameters || {}),
          }},
        });
        const response = await post({action: 'poll',
          occurrence_id: value.occurrence.occurrence_id});
        results.push({report, chart, rendered: response.rendered_result});
      }
      setReportResults(results);
      setResultHistory((current) => [...current,
        ...results.map((item) => item.rendered)]);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };
  const runChart = (chart) => {
    setActiveChartId(chart.id);
    return compile(true, chart.query);
  };
  const executeDashboardChart = async (chart, crossFilters=[]) => {
    const value = await call('semantic_query_execute', {
      model_id: record?.model_id,
      definition: record ? undefined : definition,
      query: {...chart.query, cross_filters: [
        ...(chart.query.cross_filters || []), ...crossFilters,
      ]},
    });
    const response = await post({action: 'poll',
      occurrence_id: value.occurrence.occurrence_id});
    if (!response.rendered_result) throw new Error(gettext(
      'The provider did not return a dashboard result page.'
    ));
    return response.rendered_result;
  };
  const runDashboard = async (dashboard, filter=null, selection=null) => {
    const charts = dashboard.tiles.map((tile) => ({tile, chart:
      (definition.visualizations || []).find((item) =>
        item.id === tile.visualization_id)})).filter((item) => item.chart);
    if (!charts.length) {
      setError(gettext('The dashboard has no chart queries.'));
      return;
    }
    setWorking(true); setError(null);
    try {
      const results = {};
      for (const {chart} of charts) {
        results[chart.id] = await executeDashboardChart(
          chart, filter ? [filter] : []
        );
      }
      setDashboardResults((current) => ({...current,
        [dashboard.id]: results}));
      setDashboardSelections((current) => ({...current,
        [dashboard.id]: selection}));
      setResultHistory((current) => [...current, ...Object.values(results)]);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };
  const selectDashboardPoint = (dashboard, chart, selection) => {
    if (!dashboard.cross_filtering || working) return;
    const filter = semanticCrossFilter(definition, chart, selection);
    if (!filter) {
      setError(gettext(
        'The selected chart encoding is not a semantic dimension or level.'
      ));
      return;
    }
    runDashboard(dashboard, filter, {...selection, chart_id: chart.id});
  };
  const runDiagnostics = async () => {
    setWorking(true); setError(null);
    try {
      setDiagnostics(await call('semantic_query_diagnostics', {
        model_id: record?.model_id,
        definition: record ? undefined : definition, query,
      }));
      setPanel('diagnostics');
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setWorking(false); }
  };
  const planMaterialization = async (materializationId) => {
    if (!record) return;
    setWorking(true); setError(null); setMaterializationPlan(null);
    try {
      setMaterializationPlan(await call('semantic_materialization_plan', {
        model_id: record.model_id, materialization_id: materializationId,
      }));
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setWorking(false); }
  };
  const applyMaterialization = async () => {
    if (!materializationPlan) return;
    setWorking(true); setError(null);
    try {
      await post({action: 'visual_admin_apply', request: {
        plan_id: materializationPlan.plan_id,
        plan_digest: materializationPlan.plan_digest, confirmed: true,
      }});
      setMaterializationPlan(null);
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setWorking(false); }
  };

  return <Box sx={{p: 2, overflow: 'auto', flex: 1}}>
    <Box sx={{display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center'}}>
      <TextField select sx={{minWidth: 260}} label={gettext('Semantic model')}
        value={selectedId} onChange={(event) => loadModel(event.target.value)}>
        {items.map((item) => <MenuItem key={item.model_id} value={item.model_id}>
          {item.name} — {item.status} r{item.revision}</MenuItem>)}
      </TextField>
      <Button onClick={() => {setRecord(null); setSelectedId('');
        setDefinition(emptySemanticModel(resources, analyticalProfile));}}>
        {gettext('New')}</Button>
      <Button variant="contained" disabled={working || record?.status === 'published'}
        onClick={save}>{gettext('Save')}</Button>
      <Button disabled={working} onClick={validate}>{gettext('Validate')}</Button>
      <Button disabled={!record || working} onClick={() => setStatus('published')}>
        {gettext('Publish')}</Button>
      <Button disabled={!record || working} onClick={clone}>{gettext('Clone')}</Button>
      <Button disabled={!record || working} onClick={inspectHistory}>
        {gettext('Revisions')}</Button>
      <Button color="warning" disabled={!record || working} onClick={remove}>
        {gettext('Delete')}</Button>
      {working && <CircularProgress size={24} />}
    </Box>
    <Tabs value={panel} onChange={(_event, value) => setPanel(value)} sx={{mt: 1}}>
      <Tab value="model" label={gettext('Model')} />
      <Tab value="relationships" label={gettext('Relationships')} />
      <Tab value="dimensions" label={gettext('Dimensions & hierarchies')} />
      <Tab value="measures" label={gettext('Measures')} />
      <Tab value="query" label={gettext('Cube query')} />
      <Tab value="security" label={gettext('Parameters & security')} />
      <Tab value="presentation" label={gettext('Charts & dashboards')} />
      <Tab value="reports" label={gettext('Reports & schedules')} />
      <Tab value="materializations" label={gettext('Materializations')} />
      <Tab value="lineage" label={gettext('Lineage')} />
      <Tab value="diagnostics" label={gettext('Diagnostics')} />
      <Tab value="revisions" label={gettext('Revisions')} />
    </Tabs>
    {validation && <Alert severity={validation.valid ? 'success' : 'error'} sx={{mt: 1}}>
      {validation.valid ? gettext('Model is valid.') :
        (validation.errors || []).map((item) => item.message).join(' ')}</Alert>}
    {panel === 'model' && <Box sx={{display: 'grid', gap: 1, mt: 2}}>
      <Alert severity="info">
        {analyticalProfile.title || gettext('Provider analytical model')}
        {' · '}{gettext('Grain')}: {analyticalProfile.grain_vocabulary ||
          gettext('provider-defined')}
      </Alert>
      <TextField label={gettext('Model name')} value={definition.name}
        onChange={(event) => setDefinition({...definition, name: event.target.value})} />
      <TextField multiline minRows={2} label={gettext('Description')}
        value={definition.description} onChange={(event) =>
          setDefinition({...definition, description: event.target.value})} />
      <Box component="strong">{gettext('Data sources')}</Box>
      {(definition.sources || []).map((source, index) => <Box key={source.id}
        sx={{display: 'grid',
          gridTemplateColumns: '1fr 2fr 1fr 1fr 1fr 2fr auto', gap: 1}}>
        <TextField label={gettext('Source ID')} value={source.id}
          onChange={(event) => setDefinition({...definition, sources:
            definition.sources.map((item, position) => position === index ?
              {...item, id: event.target.value} : item)})} />
        <TextField label={gettext('Native relation path')}
          value={(source.relation || []).join('.')}
          onChange={(event) => setDefinition({...definition, sources:
            definition.sources.map((item, position) => position === index ?
              {...item, relation: event.target.value.split('.')} : item)})} />
        <TextField label={gettext('Alias')} value={source.alias}
          onChange={(event) => setDefinition({...definition, sources:
            definition.sources.map((item, position) => position === index ?
              {...item, alias: event.target.value} : item)})} />
        <TextField select label={gettext('Source kind')}
          value={source.source_kind || 'table'}
          onChange={(event) => setDefinition({...definition, sources:
            definition.sources.map((item, position) => position === index ?
              {...item, source_kind: event.target.value} : item)})}>
          {(analyticalProfile.source_kinds || [source.source_kind]).map((kind) =>
            <MenuItem key={kind} value={kind}>{kind}</MenuItem>)}
        </TextField>
        <TextField select label={gettext('Classification')}
          value={source.classification || 'fact'}
          onChange={(event) => setDefinition({...definition, sources:
            definition.sources.map((item, position) => position === index ?
              {...item, classification: event.target.value} : item)})}>
          {(analyticalProfile.source_classifications || ['fact']).map((kind) =>
            <MenuItem key={kind} value={kind}>{kind}</MenuItem>)}
        </TextField>
        <TextField label={gettext('Declared grain fields')}
          value={(source.grain || []).map((item) => item.field).join(',')}
          onChange={(event) => setDefinition({...definition, sources:
            definition.sources.map((item, position) => position === index ?
              {...item, grain: event.target.value.split(',').map((field) =>
                field.trim()).filter(Boolean).map((field) => ({
                source_id: source.id, field,
              }))} : item)})} />
        <Button onClick={() => setDefinition({...definition, sources:
          definition.sources.filter((_item, position) => position !== index)})}>
          {gettext('Remove')}</Button>
      </Box>)}
      <TextField select label={gettext('Add discovered source')} value=""
        onChange={(event) => addSource(sourceResources.find((item) =>
          item.resource_id === event.target.value))}>
        {sourceResources.map((item) => <MenuItem key={item.resource_id}
          value={item.resource_id}>{(item.display_path || []).join('.')}</MenuItem>)}
      </TextField>
      <Button onClick={() => setDefinition((current) => {
        const suffix = current.sources.length + 1;
        return {...current, sources: [...current.sources, {
          id: `source_${suffix}`, resource_id: `semantic:manual:${suffix}`,
          relation: ['schema', 'relation'], alias: `source_${suffix}`,
          source_kind: analyticalProfile.source_kinds?.[0] || 'table',
          classification: analyticalProfile.source_classifications?.[0] ||
            'fact', grain: [], provider_config: {},
        }]};
      })}>{gettext('Add source path manually')}</Button>
      {definition.sources.length > 1 && <>
        <Box component="strong">{gettext('Join designer')}</Box>
        <Box sx={{display: 'grid',
          gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr 1fr auto', gap: 1}}>
          {['left_source', 'right_source'].map((side) => <TextField select key={side}
            label={side.replace('_', ' ')} value={joinDraft[side]}
            onChange={(event) => setJoinDraft({...joinDraft, [side]: event.target.value})}>
            {definition.sources.map((source) => <MenuItem key={source.id}
              value={source.id}>{source.id}</MenuItem>)}</TextField>)}
          <TextField label={gettext('Left field')} value={joinDraft.left_field}
            onChange={(event) => setJoinDraft({...joinDraft, left_field: event.target.value})} />
          <TextField label={gettext('Right field')} value={joinDraft.right_field}
            onChange={(event) => setJoinDraft({...joinDraft, right_field: event.target.value})} />
          <TextField select label={gettext('Join type')} value={joinDraft.join_type}
            onChange={(event) => setJoinDraft({...joinDraft, join_type: event.target.value})}>
            {['inner', 'left', 'right', 'full'].map((item) =>
              <MenuItem key={item} value={item}>{item}</MenuItem>)}</TextField>
          <TextField select label={gettext('Cardinality')}
            value={joinDraft.cardinality} onChange={(event) =>
              setJoinDraft({...joinDraft, cardinality: event.target.value})}>
            {['one-to-one', 'one-to-many', 'many-to-one', 'many-to-many']
              .map((item) => <MenuItem key={item}
                value={item}>{item}</MenuItem>)}
          </TextField>
          <Button onClick={addJoin}>{gettext('Add join')}</Button>
        </Box>
        {(definition.joins || []).map((join) => <Box key={join.id}>
          {join.left_source} {join.join_type} {join.right_source} ({
            join.cardinality})</Box>)}
      </>}
    </Box>}
    {panel === 'relationships' && <Box sx={{mt: 2}}>
      <Alert severity="info">
        {gettext('The diagram uses declared model relationships. Native graph, document, search, vector, and temporal relationships remain provider-described metadata.')}
      </Alert>
      <Box aria-label={gettext('Semantic relationship diagram')}
        sx={{display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap',
          mt: 2, p: 2, border: 1, borderColor: 'divider'}}>
        {definition.sources.map((source) => <Box key={source.id}
          sx={{p: 1, border: 1, borderColor: 'primary.main'}}>
          <Box component="strong">{source.id}</Box>
          <Box>{source.classification} · {source.source_kind}</Box>
        </Box>)}
        {[...(definition.joins || []), ...(definition.relationships || [])]
          .map((relationship) => <Box key={relationship.id} sx={{p: 1}}>
            {(relationship.left_source || relationship.from_source)} → {
              relationship.right_source || relationship.to_source} ({
              relationship.relationship_kind || relationship.join_type})
          </Box>)}
      </Box>
      <Box component="h3">{gettext('Provider-native relationship')}</Box>
      <Box sx={{display: 'grid',
        gridTemplateColumns: '1fr 1fr 1fr 1fr auto', gap: 1}}>
        <TextField label={gettext('Name')} value={relationshipDraft.name}
          onChange={(event) => setRelationshipDraft({...relationshipDraft,
            name: event.target.value})} />
        {['from_source', 'to_source'].map((field) => <TextField select
          key={field} label={field.replace('_', ' ')}
          value={relationshipDraft[field]} onChange={(event) =>
            setRelationshipDraft({...relationshipDraft,
              [field]: event.target.value})}>
          {definition.sources.map((source) => <MenuItem key={source.id}
            value={source.id}>{source.id}</MenuItem>)}
        </TextField>)}
        <TextField select label={gettext('Relationship kind')}
          value={relationshipDraft.relationship_kind}
          onChange={(event) => setRelationshipDraft({...relationshipDraft,
            relationship_kind: event.target.value})}>
          {(analyticalProfile.relationship_kinds || ['native-edge'])
            .map((kind) => <MenuItem key={kind} value={kind}>{kind}</MenuItem>)}
        </TextField>
        <Button disabled={!relationshipDraft.from_source ||
          !relationshipDraft.to_source} onClick={addRelationship}>
          {gettext('Add relationship')}</Button>
      </Box>
    </Box>}
    {panel === 'dimensions' && <Box sx={{mt: 2}}>
      <Box sx={{display: 'grid',
        gridTemplateColumns: 'repeat(11, 1fr) auto', gap: 1}}>
        <TextField label={gettext('Dimension ID')} value={dimensionDraft.id}
          onChange={(event) => setDimensionDraft({...dimensionDraft, id: event.target.value})} />
        <TextField label={gettext('Name')} value={dimensionDraft.name}
          onChange={(event) => setDimensionDraft({...dimensionDraft, name: event.target.value})} />
        <TextField select label={gettext('Source')} value={dimensionDraft.source_id}
          onChange={(event) => setDimensionDraft({...dimensionDraft,
            source_id: event.target.value})}>{definition.sources.map((source) =>
            <MenuItem key={source.id} value={source.id}>{source.id}</MenuItem>)}</TextField>
        <TextField label={gettext('Field')} value={dimensionDraft.field}
          onChange={(event) => setDimensionDraft({...dimensionDraft, field: event.target.value})} />
        <TextField label={gettext('Hierarchy')} value={dimensionDraft.hierarchy}
          onChange={(event) => setDimensionDraft({...dimensionDraft,
            hierarchy: event.target.value})} />
        <TextField label={gettext('Level')} value={dimensionDraft.level}
          onChange={(event) => setDimensionDraft({...dimensionDraft, level: event.target.value})} />
        <TextField select label={gettext('Dimension kind')}
          value={dimensionDraft.dimension_kind} onChange={(event) =>
            setDimensionDraft({...dimensionDraft,
              dimension_kind: event.target.value})}>
          {(analyticalProfile.dimension_kinds || ['attribute']).map((kind) =>
            <MenuItem key={kind} value={kind}>{kind}</MenuItem>)}
        </TextField>
        <TextField select label={gettext('Time role')}
          disabled={dimensionDraft.dimension_kind !== 'time'}
          value={dimensionDraft.time_role} onChange={(event) =>
            setDimensionDraft({...dimensionDraft,
              time_role: event.target.value})}>
          {['calendar-time', 'fiscal-time', 'event-time', 'processing-time',
            'valid-time', 'system-time'].map((role) => <MenuItem key={role}
            value={role}>{role}</MenuItem>)}
        </TextField>
        <TextField label={gettext('Timezone')}
          disabled={dimensionDraft.dimension_kind !== 'time'}
          value={dimensionDraft.timezone} onChange={(event) =>
            setDimensionDraft({...dimensionDraft,
              timezone: event.target.value})} />
        <TextField type="number" label={gettext('Fiscal start month')}
          disabled={dimensionDraft.dimension_kind !== 'time'}
          inputProps={{min: 1, max: 12}}
          value={dimensionDraft.fiscal_year_start_month}
          onChange={(event) => setDimensionDraft({...dimensionDraft,
            fiscal_year_start_month: event.target.value})} />
        <TextField select label={gettext('Time value type')}
          disabled={dimensionDraft.dimension_kind !== 'time'}
          value={dimensionDraft.time_value_type}
          onChange={(event) => setDimensionDraft({...dimensionDraft,
            time_value_type: event.target.value})}>
          {['datetime', 'date', 'string'].map((item) => <MenuItem key={item}
            value={item}>{item}</MenuItem>)}
        </TextField>
        <Button onClick={addDimension} disabled={!dimensionDraft.field}>
          {gettext('Add')}</Button>
      </Box>
      {(definition.dimensions || []).map((dimension, index) => <Box key={dimension.id}
        sx={{p: 1, mt: 1, border: 1, borderColor: 'divider'}}>
        <Box component="strong">{dimension.name}</Box> — {
          dimension.dimension_kind || 'attribute'} — {
          dimension.field.source_id}.{dimension.field.field}
        {dimension.time_intelligence && <Box component="span" sx={{ml: 1}}>
          {dimension.time_intelligence.role} / {
            dimension.time_intelligence.timezone}
        </Box>}
        {(dimension.hierarchies || []).map((hierarchy) => <Box key={hierarchy.id}>
          <Box component="strong">{hierarchy.name}</Box>:
          {(hierarchy.levels || []).map((level) => <Box component="span"
            key={level.id} sx={{ml: 1}}>{level.name}
            <Button size="small" onClick={() => removeLevel(
              dimension.id, hierarchy.id, level.id
            )}>{gettext('Remove level')}</Button></Box>)}</Box>)}
        <Button onClick={() => setDefinition({...definition, dimensions:
          definition.dimensions.filter((_item, position) => position !== index)})}>
          {gettext('Remove dimension')}</Button>
      </Box>)}
      <Box component="strong" sx={{display: 'block', mt: 2}}>
        {gettext('Add hierarchy')}</Box>
      <Box sx={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto', gap: 1}}>
        <TextField select label={gettext('Dimension')}
          value={hierarchyDraft.dimension_id} onChange={(event) =>
            setHierarchyDraft({...hierarchyDraft,
              dimension_id: event.target.value})}>
          {definition.dimensions.map((dimension) => <MenuItem key={dimension.id}
            value={dimension.id}>{dimension.name}</MenuItem>)}</TextField>
        <TextField label={gettext('Hierarchy ID')} value={hierarchyDraft.id}
          onChange={(event) => setHierarchyDraft({...hierarchyDraft,
            id: event.target.value})} />
        <TextField label={gettext('Hierarchy name')} value={hierarchyDraft.name}
          onChange={(event) => setHierarchyDraft({...hierarchyDraft,
            name: event.target.value})} />
        <Button disabled={!hierarchyDraft.dimension_id} onClick={addHierarchy}>
          {gettext('Add hierarchy')}</Button>
      </Box>
      <Box component="strong" sx={{display: 'block', mt: 2}}>
        {gettext('Add hierarchy level')}</Box>
      <Box sx={{display: 'grid', gridTemplateColumns: 'repeat(6, 1fr) auto', gap: 1}}>
        <TextField select label={gettext('Dimension')} value={levelDraft.dimension_id}
          onChange={(event) => setLevelDraft({...levelDraft,
            dimension_id: event.target.value, hierarchy_id: ''})}>
          {definition.dimensions.map((dimension) => <MenuItem key={dimension.id}
            value={dimension.id}>{dimension.name}</MenuItem>)}</TextField>
        <TextField select label={gettext('Hierarchy')} value={levelDraft.hierarchy_id}
          onChange={(event) => setLevelDraft({...levelDraft,
            hierarchy_id: event.target.value})}>
          {(definition.dimensions.find((dimension) =>
            dimension.id === levelDraft.dimension_id)?.hierarchies || []).map(
            (hierarchy) => <MenuItem key={hierarchy.id} value={hierarchy.id}>
              {hierarchy.name}</MenuItem>)}</TextField>
        {['id', 'name', 'field'].map((name) => <TextField key={name}
          label={name} value={levelDraft[name]} onChange={(event) =>
            setLevelDraft({...levelDraft, [name]: event.target.value})} />)}
        <TextField select label={gettext('Source')} value={levelDraft.source_id}
          onChange={(event) => setLevelDraft({...levelDraft,
            source_id: event.target.value})}>{definition.sources.map((source) =>
            <MenuItem key={source.id} value={source.id}>{source.id}</MenuItem>)}</TextField>
        <Button disabled={!levelDraft.hierarchy_id || !levelDraft.field}
          onClick={addLevel}>{gettext('Add level')}</Button>
      </Box>
      {columnResources.length > 0 && <Alert severity="info" sx={{mt: 1}}>
        {gettext('Discovered fields')}: {columnResources.slice(0, 20)
          .map((item) => item.display_name).join(', ')}</Alert>}
    </Box>}
    {panel === 'measures' && <Box sx={{mt: 2}}>
      <Box sx={{display: 'grid',
        gridTemplateColumns: 'repeat(9, 1fr) auto', gap: 1}}>
        {['id', 'name', 'field', 'format'].map((name) => <TextField key={name}
          label={name} value={measureDraft[name]} onChange={(event) =>
            setMeasureDraft({...measureDraft, [name]: event.target.value})} />)}
        <TextField select label={gettext('Source')} value={measureDraft.source_id}
          onChange={(event) => setMeasureDraft({...measureDraft,
            source_id: event.target.value})}>{definition.sources.map((source) =>
            <MenuItem key={source.id} value={source.id}>{source.id}</MenuItem>)}</TextField>
        <TextField select label={gettext('Aggregation')} value={measureDraft.aggregation}
          onChange={(event) => setMeasureDraft({...measureDraft,
            aggregation: event.target.value})}>{['sum', 'count', 'count_distinct',
            'min', 'max', 'avg', 'none'].map((item) => <MenuItem key={item}
            value={item}>{item}</MenuItem>)}</TextField>
        <TextField select label={gettext('Measure kind')}
          value={measureDraft.measure_kind} onChange={(event) =>
            setMeasureDraft({...measureDraft,
              measure_kind: event.target.value})}>
          {(analyticalProfile.measure_kinds || ['aggregate']).map((kind) =>
            <MenuItem key={kind} value={kind}>{kind}</MenuItem>)}
        </TextField>
        <TextField select label={gettext('Certification')}
          value={measureDraft.certification_status} onChange={(event) =>
            setMeasureDraft({...measureDraft,
              certification_status: event.target.value})}>
          {['uncertified', 'candidate', 'certified', 'deprecated'].map((item) =>
            <MenuItem key={item} value={item}>{item}</MenuItem>)}
        </TextField>
        <TextField label={gettext('Metric owner')}
          value={measureDraft.certification_owner} onChange={(event) =>
            setMeasureDraft({...measureDraft,
              certification_owner: event.target.value})} />
        <Button onClick={addMeasure}>{gettext('Add')}</Button>
      </Box>
      <Box component="strong" sx={{display: 'block', mt: 2}}>
        {gettext('Calculated measure builder')}</Box>
      <Box sx={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr 1fr auto', gap: 1, mt: 1}}>
        {['id', 'name', 'format'].map((name) => <TextField key={name}
          label={name} value={calculationDraft[name]} onChange={(event) =>
            setCalculationDraft({...calculationDraft,
              [name]: event.target.value})} />)}
        {['left', 'right'].map((name) => <TextField select key={name}
          label={`${name} measure`} value={calculationDraft[name]}
          onChange={(event) => setCalculationDraft({...calculationDraft,
            [name]: event.target.value})}>{definition.measures.map((measure) =>
            <MenuItem key={measure.id} value={measure.id}>
              {measure.name}</MenuItem>)}</TextField>)}
        <TextField select label={gettext('Operator')}
          value={calculationDraft.operator} onChange={(event) =>
            setCalculationDraft({...calculationDraft,
              operator: event.target.value})}>
          {['add', 'subtract', 'multiply', 'divide'].map((operator) =>
            <MenuItem key={operator} value={operator}>{operator}</MenuItem>)}
        </TextField>
        <Button onClick={addCalculation} disabled={!calculationDraft.left ||
          !calculationDraft.right}>{gettext('Add calculation')}</Button>
      </Box>
      {(definition.measures || []).map((measure, index) => <Box key={measure.id}
        sx={{p: 1, mt: 1, border: 1, borderColor: 'divider'}}>
        {measure.name}: {measure.aggregation}({measure.field?.field || '*'}) {
          measure.format} — {measure.measure_kind || 'aggregate'} — {
          measure.certification?.status || 'uncertified'}
        <Button onClick={() => setDefinition({...definition, measures:
          definition.measures.filter((_item, position) => position !== index)})}>
          {gettext('Remove')}</Button></Box>)}
    </Box>}
    {panel === 'query' && <Box sx={{mt: 2}}>
      <Box sx={{display: 'grid',
        gridTemplateColumns: '1fr 1fr 2fr 2fr', gap: 1, mb: 1}}>
        <TextField select label={gettext('Drill mode')}
          value={query.drill?.mode || 'summary'} onChange={(event) =>
            setQuery({...query, drill: {...query.drill,
              mode: event.target.value}})}>
          {['summary', 'down', 'through'].map((mode) => <MenuItem key={mode}
            value={mode}>{mode}</MenuItem>)}
        </TextField>
        <TextField select label={gettext('Drill-down target')}
          value={query.drill?.target_level || ''} onChange={(event) =>
            setQuery({...query, drill: {...query.drill,
              target_level: event.target.value || null}})}>
          <MenuItem value="">{gettext('None')}</MenuItem>
          {levelOptions.map((item) => <MenuItem key={item.id}
            value={item.id}>{item.name}</MenuItem>)}
        </TextField>
        <TextField label={gettext('Parameter values (JSON)')}
          value={JSON.stringify(query.parameters || {})}
          onChange={(event) => {try {
            setQuery({...query, parameters: JSON.parse(event.target.value)});
          } catch {/* retain last valid parameter object */}}} />
        <TextField label={gettext('Drill-through fields (source.field, ...)')}
          value={detailFieldsDraft} onChange={(event) => {
            const text = event.target.value;
            setDetailFieldsDraft(text);
            setQuery({...query, drill: {...query.drill, detail_fields: text
              .split(',').map((item) => item.trim()).filter(Boolean)
              .map((item) => {
                const [source_id, ...fieldParts] = item.split('.');
                return {source_id, field: fieldParts.join('.')};
              }).filter((item) => item.source_id && item.field)}});
          }} />
      </Box>
      <Box sx={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 1}}>
        {['rows', 'columns', 'pages'].map((axis) => <TextField select key={axis}
          SelectProps={{multiple: true}} label={axis} value={query.axes[axis]}
          onChange={(event) => setQuery({...query, axes: {...query.axes,
            [axis]: event.target.value}})}>{levelOptions.map((item) =>
            <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}</TextField>)}
      </Box>
      <TextField select fullWidth sx={{mt: 1}} SelectProps={{multiple: true}}
        label={gettext('Measures')} value={query.measures}
        onChange={(event) => setQuery({...query, measures: event.target.value})}>
        {definition.measures.map((item) => <MenuItem key={item.id}
          value={item.id}>{item.name}</MenuItem>)}</TextField>
      <Box sx={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 2fr auto', gap: 1, mt: 1}}>
        <TextField select label={gettext('Slice source')} value={filterDraft.source_id}
          onChange={(event) => setFilterDraft({...filterDraft,
            source_id: event.target.value})}>{definition.sources.map((source) =>
            <MenuItem key={source.id} value={source.id}>{source.id}</MenuItem>)}</TextField>
        <TextField label={gettext('Field')} value={filterDraft.field}
          onChange={(event) => setFilterDraft({...filterDraft, field: event.target.value})} />
        <TextField select label={gettext('Operator')} value={filterDraft.operator}
          onChange={(event) => setFilterDraft({...filterDraft,
            operator: event.target.value})}>{['eq', 'ne', 'lt', 'lte', 'gt',
            'gte', 'in', 'not_in', 'between', 'is_null', 'is_not_null'].map((item) =>
            <MenuItem key={item} value={item}>{item}</MenuItem>)}</TextField>
        <TextField label={gettext('Member value (text or JSON)')} value={filterDraft.value}
          onChange={(event) => setFilterDraft({...filterDraft, value: event.target.value})} />
        <Button onClick={() => addFilter('filters')}
          disabled={!filterDraft.field}>{gettext('Add slice')}</Button>
      </Box>
      {(query.filters || []).map((item, index) => <Box key={index} sx={{mt: 1}}>
        {item.field.source_id}.{item.field.field} {item.operator} {JSON.stringify(item.value)}
        <Button onClick={() => setQuery({...query, filters: query.filters.filter(
          (_value, position) => position !== index)})}>{gettext('Remove')}</Button></Box>)}
      <Button disabled={!filterDraft.field}
        onClick={() => addFilter('cross_filters')}>
        {gettext('Add cross-filter')}</Button>
      <Box sx={{display: 'grid',
        gridTemplateColumns: 'repeat(5, 1fr)', gap: 1, mt: 1}}>
        <TextField select label={gettext('Time operation')}
          value={query.time_intelligence?.operation || ''}
          onChange={(event) => setQuery({...query, time_intelligence:
            event.target.value ? {...query.time_intelligence,
              operation: event.target.value} : {}})}>
          <MenuItem value="">{gettext('None')}</MenuItem>
          {(semantic.capabilities.time_intelligence_operations || [])
            .map((operation) => <MenuItem key={operation} value={operation}>
              {operation.replaceAll('_', ' ')}</MenuItem>)}
        </TextField>
        <TextField select label={gettext('Time dimension')}
          value={query.time_intelligence?.dimension_id || ''}
          onChange={(event) => setQuery({...query, time_intelligence: {
            ...query.time_intelligence, dimension_id: event.target.value}})}>
          <MenuItem value="">{gettext('None')}</MenuItem>
          {definition.dimensions.filter((item) => item.time_intelligence)
            .map((item) => <MenuItem key={item.id}
              value={item.id}>{item.name}</MenuItem>)}
        </TextField>
        {['as_of', 'range'].includes(
          query.time_intelligence?.operation
        ) && <TextField label={gettext('Start / as-of value')}
          value={query.time_intelligence?.start || ''}
          onChange={(event) => setQuery({...query, time_intelligence: {
            ...query.time_intelligence, start: event.target.value}})} />}
        {query.time_intelligence?.operation === 'range' && <TextField
          label={gettext('End value')}
          value={query.time_intelligence?.end || ''}
          onChange={(event) => setQuery({...query, time_intelligence: {
            ...query.time_intelligence, end: event.target.value}})} />}
        {['period_to_date', 'period_comparison'].includes(
          query.time_intelligence?.operation
        ) && <TextField select label={gettext('Period')}
          value={query.time_intelligence?.period || ''}
          onChange={(event) => setQuery({...query, time_intelligence: {
            ...query.time_intelligence, period: event.target.value}})}>
          {(semantic.capabilities.time_intelligence_periods || [])
            .map((period) => <MenuItem key={period} value={period}>
              {period.replaceAll('_', ' ')}</MenuItem>)}
        </TextField>}
        {['period_to_date', 'period_comparison'].includes(
          query.time_intelligence?.operation
        ) && <TextField label={gettext('Anchor date or datetime')}
          value={query.time_intelligence?.anchor || ''}
          onChange={(event) => setQuery({...query, time_intelligence: {
            ...query.time_intelligence, anchor: event.target.value}})} />}
      </Box>
      {(semantic.capabilities.analytical_window_operations || []).length > 0 &&
      <Box sx={{mt: 2}}>
        <Box component="h4">{gettext('Native analytical windows')}</Box>
        <Box sx={{display: 'grid', gridTemplateColumns:
          '1fr 1fr 1fr 1fr 1fr 1fr 1fr auto', gap: 1}}>
          <TextField label={gettext('Output ID')} value={windowDraft.id}
            onChange={(event) => setWindowDraft({...windowDraft,
              id: event.target.value})} />
          <TextField select label={gettext('Measure')}
            value={windowDraft.measure_id} onChange={(event) =>
              setWindowDraft({...windowDraft, measure_id: event.target.value})}>
            {definition.measures.map((item) => <MenuItem key={item.id}
              value={item.id}>{item.name}</MenuItem>)}
          </TextField>
          <TextField select label={gettext('Window operation')}
            value={windowDraft.operation} onChange={(event) =>
              setWindowDraft({...windowDraft, operation: event.target.value})}>
            {semantic.capabilities.analytical_window_operations.map((item) =>
              <MenuItem key={item} value={item}>
                {item.replaceAll('_', ' ')}</MenuItem>)}
          </TextField>
          <TextField select SelectProps={{multiple: true}}
            label={gettext('Partition levels')}
            value={windowDraft.partition_by} onChange={(event) =>
              setWindowDraft({...windowDraft,
                partition_by: event.target.value})}>
            {levelOptions.map((item) => <MenuItem key={item.id}
              value={item.id}>{item.name}</MenuItem>)}
          </TextField>
          <TextField select label={gettext('Order level')}
            value={windowDraft.order_level} onChange={(event) =>
              setWindowDraft({...windowDraft,
                order_level: event.target.value})}>
            {levelOptions.map((item) => <MenuItem key={item.id}
              value={item.id}>{item.name}</MenuItem>)}
          </TextField>
          <TextField select label={gettext('Direction')}
            value={windowDraft.direction} onChange={(event) =>
              setWindowDraft({...windowDraft, direction: event.target.value})}>
            <MenuItem value="asc">asc</MenuItem>
            <MenuItem value="desc">desc</MenuItem>
          </TextField>
          <TextField type="number" label={gettext('Frame / lag')}
            inputProps={{min: 1, max: 10000}} value={windowDraft.frame_size}
            onChange={(event) => setWindowDraft({...windowDraft,
              frame_size: event.target.value})} />
          <Button disabled={!windowDraft.measure_id ||
            !windowDraft.order_level} onClick={addWindow}>
            {gettext('Add window')}</Button>
        </Box>
        {(query.windows || []).map((window, index) => <Box key={window.id}
          sx={{mt: 1}}>{window.id}: {window.operation}({window.measure_id})
          <Button onClick={() => setQuery({...query, windows:
            query.windows.filter((_item, position) => position !== index)})}>
            {gettext('Remove')}</Button>
        </Box>)}
      </Box>}
      <Box sx={{display: 'flex', gap: 1, mt: 1, alignItems: 'center'}}>
        <TextField type="number" label={gettext('Cell limit')} value={query.limit}
          onChange={(event) => setQuery({...query, limit: Number(event.target.value)})} />
        <FormControlLabel control={<Checkbox checked={query.totals}
          onChange={(event) => setQuery({...query, totals: event.target.checked})} />}
        label={gettext('Rollup totals')} />
        <Button disabled={working || !semantic.capabilities.execution_available}
          onClick={() => compile(false)}>{gettext('Preview native query')}</Button>
        <Button variant="contained" disabled={working ||
          !semantic.capabilities.execution_available} onClick={() => compile(true)}>
          {gettext('Run cube query')}</Button>
        <Button disabled={working || !semantic.capabilities.execution_available}
          onClick={runDiagnostics}>{gettext('Query diagnostics')}</Button>
        {occurrenceId && <Button onClick={async () => {
          const response = await post({action: 'poll', occurrence_id: occurrenceId});
          setRendered(response.rendered_result);
        }}>{gettext('Poll')}</Button>}
      </Box>
      {!semantic.capabilities.execution_available && <Alert severity="warning" sx={{mt: 1}}>
        {semantic.capabilities.provider_compiler?.reason ||
          gettext('This provider has not activated semantic query execution.')}</Alert>}
      {compiled && <Box component="pre" sx={{mt: 1, p: 1,
        bgcolor: 'background.default', whiteSpace: 'pre-wrap'}}>
        {compiled.source}</Box>}
      {rendered && <ResultControls rendered={rendered} history={resultHistory}
        post={post} onRendered={setRendered} setError={setError}
        setBusy={setWorking} />}
      {rendered && <ResultView rendered={rendered} />}
    </Box>}
    {panel === 'security' && <Box sx={{mt: 2}}>
      <Box component="h3">{gettext('Query parameters')}</Box>
      <Box sx={{display: 'grid',
        gridTemplateColumns: '1fr 1fr 1fr 1fr auto auto', gap: 1}}>
        <TextField label={gettext('Parameter ID')} value={parameterDraft.id}
          onChange={(event) => setParameterDraft({...parameterDraft,
            id: event.target.value})} />
        <TextField label={gettext('Name')} value={parameterDraft.name}
          onChange={(event) => setParameterDraft({...parameterDraft,
            name: event.target.value})} />
        <TextField select label={gettext('Type')} value={parameterDraft.type}
          onChange={(event) => setParameterDraft({...parameterDraft,
            type: event.target.value})}>
          {['string', 'integer', 'number', 'boolean', 'date', 'datetime',
            'array'].map((type) => <MenuItem key={type}
            value={type}>{type}</MenuItem>)}
        </TextField>
        <TextField label={gettext('Default')} value={parameterDraft.default}
          onChange={(event) => setParameterDraft({...parameterDraft,
            default: event.target.value})} />
        <FormControlLabel control={<Checkbox checked={parameterDraft.required}
          onChange={(event) => setParameterDraft({...parameterDraft,
            required: event.target.checked})} />}
        label={gettext('Required')} />
        <Button onClick={addParameter}>{gettext('Add parameter')}</Button>
      </Box>
      {(definition.parameters || []).map((parameter, index) => <Box
        key={parameter.id} sx={{mt: 1}}>{parameter.name} ({parameter.type})
        <Button onClick={() => setDefinition({...definition,
          parameters: definition.parameters.filter(
            (_item, position) => position !== index)})}>
          {gettext('Remove')}</Button>
      </Box>)}
      <Box component="h3">{gettext('Row-level security')}</Box>
      <Alert severity="info">
        {gettext('Security filters are structured model policy and are compiled by the provider. Tenant values come only from trusted server-side principal claims.')}
      </Alert>
      <Box sx={{display: 'grid', mt: 1,
        gridTemplateColumns: '1fr 1fr 1fr 2fr auto', gap: 1}}>
        <TextField select label={gettext('Policy source')}
          value={definition.sources.some((source) =>
            source.id === securityDraft.source_id) ?
            securityDraft.source_id : ''} onChange={(event) =>
            setSecurityDraft({...securityDraft,
              source_id: event.target.value})}>
          {definition.sources.map((source) => <MenuItem key={source.id}
            value={source.id}>{source.id}</MenuItem>)}
        </TextField>
        <TextField label={gettext('Policy field')} value={securityDraft.field}
          onChange={(event) => setSecurityDraft({...securityDraft,
            field: event.target.value})} />
        <TextField select label={gettext('Policy operator')}
          value={securityDraft.operator} onChange={(event) =>
            setSecurityDraft({...securityDraft,
              operator: event.target.value})}>
          {['eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'in', 'not_in',
            'between', 'is_null', 'is_not_null'].map((item) =>
            <MenuItem key={item} value={item}>{item}</MenuItem>)}
        </TextField>
        <TextField label={gettext('Policy value (text or JSON)')}
          value={securityDraft.value} onChange={(event) =>
            setSecurityDraft({...securityDraft, value: event.target.value})} />
        <Button disabled={!securityDraft.field} onClick={addSecurityFilter}>
          {gettext('Add row policy')}</Button>
      </Box>
      <Box sx={{display: 'flex', gap: 1, mt: 1, flexWrap: 'wrap'}}>
        {(definition.security?.row_filters || []).map((item, index) =>
          <Box key={index}>{item.field.source_id}.{item.field.field} {
            item.operator} {JSON.stringify(item.value)}
          <Button onClick={() => setDefinition({...definition, security: {
            ...definition.security, row_filters:
              definition.security.row_filters.filter(
                (_value, position) => position !== index),
          }})}>{gettext('Remove')}</Button></Box>)}
      </Box>
      <Box component="h3">{gettext('Tenant filtering')}</Box>
      <Box sx={{display: 'grid',
        gridTemplateColumns: '1fr 1fr 1fr auto', gap: 1}}>
        <TextField select label={gettext('Tenant source')}
          value={definition.sources.some((source) =>
            source.id === securityDraft.source_id) ?
            securityDraft.source_id : ''} onChange={(event) =>
            setSecurityDraft({...securityDraft,
              source_id: event.target.value})}>
          {definition.sources.map((source) => <MenuItem key={source.id}
            value={source.id}>{source.id}</MenuItem>)}
        </TextField>
        <TextField label={gettext('Tenant field')} value={securityDraft.field}
          onChange={(event) => setSecurityDraft({...securityDraft,
            field: event.target.value})} />
        <TextField label={gettext('Trusted principal claim')}
          value={securityDraft.principal_claim}
          helperText={gettext('Bound server-side from the authenticated CDEadmin user')}
          InputProps={{readOnly: true}} />
        <Button disabled={!securityDraft.field} onClick={() =>
          setDefinition({...definition, security: {
            ...(definition.security || {}), tenant_filter: {
              field: {source_id: securityDraft.source_id,
                field: securityDraft.field},
              principal_claim: securityDraft.principal_claim, required: true,
            },
          }})}>{gettext('Set tenant filter')}</Button>
      </Box>
    </Box>}
    {panel === 'presentation' && <Box sx={{mt: 2}}>
      <Box component="h3">{gettext('Chart builder')}</Box>
      <Box sx={{display: 'grid',
        gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr auto', gap: 1}}>
        {['id', 'name', 'x', 'y'].map((field) => <TextField key={field}
          label={field} value={chartDraft[field]} onChange={(event) =>
            setChartDraft({...chartDraft, [field]: event.target.value})} />)}
        <TextField select label={gettext('Chart type')}
          value={chartDraft.chart_type} onChange={(event) =>
            setChartDraft({...chartDraft, chart_type: event.target.value})}>
          {['table', 'pivot', 'bar', 'line', 'area', 'scatter', 'pie',
            'metric', 'histogram', 'timeline', 'graph',
            'vector-neighbors'].map((type) => <MenuItem key={type}
            value={type}>{type}</MenuItem>)}
        </TextField>
        <Button onClick={addChart} disabled={!query.measures.length}>
          {gettext('Add chart')}</Button>
      </Box>
      <Box sx={{display: 'grid', mt: 1,
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 1}}>
        {(definition.visualizations || []).map((chart) => <Box key={chart.id}
          sx={{p: 1, border: 1, borderColor: 'divider'}}>
          <Box component="strong">{chart.name}</Box>
          <Box>{chart.chart_type} · {chart.encodings?.x || 'x'} → {
            chart.encodings?.y || 'y'}</Box>
          <Button disabled={!semantic.capabilities.execution_available || working}
            onClick={() => runChart(chart)}>{gettext('Run chart query')}</Button>
          {activeChartId === chart.id && rendered && <Box sx={{mt: 1}}>
            <SemanticChartView chart={chart} rendered={rendered} />
          </Box>}
        </Box>)}
      </Box>
      <Box component="h3">{gettext('Dashboard builder')}</Box>
      <Box sx={{display: 'grid',
        gridTemplateColumns: '1fr 1fr 1fr auto', gap: 1}}>
        <TextField label={gettext('Dashboard ID')} value={dashboardDraft.id}
          onChange={(event) => setDashboardDraft({...dashboardDraft,
            id: event.target.value})} />
        <TextField label={gettext('Dashboard name')} value={dashboardDraft.name}
          onChange={(event) => setDashboardDraft({...dashboardDraft,
            name: event.target.value})} />
        <TextField select label={gettext('Initial chart')}
          value={dashboardDraft.visualization_id} onChange={(event) =>
            setDashboardDraft({...dashboardDraft,
              visualization_id: event.target.value})}>
          <MenuItem value="">{gettext('None')}</MenuItem>
          {(definition.visualizations || []).map((chart) => <MenuItem
            key={chart.id} value={chart.id}>{chart.name}</MenuItem>)}
        </TextField>
        <Button onClick={addDashboard}>{gettext('Add dashboard')}</Button>
      </Box>
      {(definition.dashboards || []).map((dashboard) => <Box key={dashboard.id}
        sx={{p: 1, mt: 1, border: 1, borderColor: 'divider'}}>
        <Box component="strong">{dashboard.name}</Box> · {
          dashboard.cross_filtering ? gettext('cross-filtering enabled') :
            gettext('independent tiles')}
        <Box sx={{display: 'flex', gap: 1, my: 1}}>
          <Button disabled={!semantic.capabilities.execution_available || working}
            onClick={() => runDashboard(dashboard)}>
            {gettext('Run dashboard')}</Button>
          <Button disabled={!dashboardSelections[dashboard.id] || working}
            onClick={() => runDashboard(dashboard)}>
            {gettext('Clear dashboard selection')}</Button>
          {dashboardSelections[dashboard.id] && <Box role="status">
            {gettext('Filtered by')} {
              dashboardSelections[dashboard.id].label}</Box>}
        </Box>
        <Box sx={{display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 1}}>
          {dashboard.tiles.map((tile, index) => {
            const chart = (definition.visualizations || []).find((item) =>
              item.id === tile.visualization_id);
            const tileResult = dashboardResults[dashboard.id]?.[
              tile.visualization_id];
            return <Box key={tile.visualization_id}
              aria-label={gettext('Dashboard chart tile')}
              sx={{p: 1, border: 1, borderColor: 'divider'}}>
              <Box component="strong">{chart?.name || tile.visualization_id}</Box>
              <Button onClick={() => setDefinition({...definition,
                dashboards: definition.dashboards.map((item) =>
                  item.id === dashboard.id ? {...item,
                    tiles: item.tiles.filter(
                      (_tile, position) => position !== index),
                  } : item),
              })}>{gettext('Remove tile')}</Button>
              {chart && tileResult && <SemanticChartView chart={chart}
                rendered={tileResult} onSelect={(selection) =>
                  selectDashboardPoint(dashboard, chart, selection)} />}
            </Box>;
          })}
        </Box>
        <Box sx={{display: 'flex', gap: 1, mt: 1}}>
          <TextField select label={gettext('Chart to add')}
            value={dashboardDraft.visualization_id} onChange={(event) =>
              setDashboardDraft({...dashboardDraft,
                visualization_id: event.target.value})}>
            <MenuItem value="">{gettext('None')}</MenuItem>
            {(definition.visualizations || []).map((chart) => <MenuItem
              key={chart.id} value={chart.id}>{chart.name}</MenuItem>)}
          </TextField>
          <Button disabled={!dashboardDraft.visualization_id}
            onClick={() => addDashboardTile(
              dashboard.id, dashboardDraft.visualization_id
            )}>{gettext('Add tile')}</Button>
        </Box>
      </Box>)}
    </Box>}
    {panel === 'reports' && <Box sx={{mt: 2}}>
      <Box component="h3">{gettext('Report schedules')}</Box>
      {!semantic.capabilities.scheduled_report_execution && <Alert
        severity="warning">
        {gettext('Schedule definitions are stored, but this provider has not activated a report scheduler. CDEadmin will not claim or infer scheduled execution.')}
      </Alert>}
      <Box sx={{display: 'grid',
        gridTemplateColumns: '1fr 1fr 1fr 1fr auto auto', gap: 1, mt: 1}}>
        {['id', 'name', 'expression', 'timezone'].map((field) => <TextField
          key={field} label={field} value={scheduleDraft[field]}
          onChange={(event) => setScheduleDraft({...scheduleDraft,
            [field]: event.target.value})} />)}
        <FormControlLabel control={<Checkbox checked={scheduleDraft.enabled}
          onChange={(event) => setScheduleDraft({...scheduleDraft,
            enabled: event.target.checked})} />} label={gettext('Enabled')} />
        <Button onClick={addSchedule}>{gettext('Add schedule')}</Button>
      </Box>
      <Box component="h3">{gettext('Report builder')}</Box>
      <Box sx={{display: 'grid',
        gridTemplateColumns: '1fr 1fr 1fr 1fr 2fr auto', gap: 1}}>
        {['id', 'name'].map((field) => <TextField key={field} label={field}
          value={reportDraft[field]} onChange={(event) =>
            setReportDraft({...reportDraft, [field]: event.target.value})} />)}
        <TextField select label={gettext('Dashboard')}
          value={reportDraft.dashboard_id} onChange={(event) =>
            setReportDraft({...reportDraft,
              dashboard_id: event.target.value})}>
          <MenuItem value="">{gettext('None')}</MenuItem>
          {(definition.dashboards || []).map((dashboard) => <MenuItem
            key={dashboard.id} value={dashboard.id}>{dashboard.name}</MenuItem>)}
        </TextField>
        <TextField select label={gettext('Schedule')}
          value={reportDraft.schedule_id} onChange={(event) =>
            setReportDraft({...reportDraft,
              schedule_id: event.target.value})}>
          <MenuItem value="">{gettext('Manual')}</MenuItem>
          {(definition.schedules || []).map((schedule) => <MenuItem
            key={schedule.id} value={schedule.id}>{schedule.name}</MenuItem>)}
        </TextField>
        <TextField label={gettext('Export formats')}
          value={reportDraft.export_formats} onChange={(event) =>
            setReportDraft({...reportDraft,
              export_formats: event.target.value})} />
        <Button onClick={addReport}>{gettext('Add report')}</Button>
      </Box>
      {(definition.reports || []).map((report) => <Box key={report.id}
        sx={{mt: 1, p: 1, border: 1, borderColor: 'divider'}}>
        <Box component="strong">{report.name}</Box> · {
          report.export_formats.join(', ')} · {report.schedule_id ||
            gettext('manual')}
        <Button disabled={!semantic.capabilities.execution_available}
          onClick={() => runReport(report)}>{gettext('Run report')}</Button>
      </Box>)}
      {reportResults.map(({report, chart, rendered: reportResult}) => <Box
        key={chart.id} sx={{mt: 2}}>
        <Box component="h4">{chart.name}</Box>
        <ResultControls rendered={reportResult} history={resultHistory}
          post={post} onRendered={(value) => setReportResults((current) =>
            current.map((item) => item.chart.id === chart.id ? {
              ...item, rendered: value,
            } : item))} setError={setError} setBusy={setWorking}
          allowedFormats={report.export_formats}
          deliveryProfiles={semantic.delivery?.profiles || []} />
        <SemanticChartView chart={chart} rendered={reportResult} />
      </Box>)}
    </Box>}
    {panel === 'materializations' && <Box sx={{mt: 2}}>
      <Alert severity="info">{gettext('Materialization definitions are portable intent. Creation and refresh remain provider-planned operations.')}</Alert>
      <Box sx={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto auto', gap: 1, mt: 1}}>
        {['id', 'name', 'strategy'].map((name) => <TextField key={name}
          label={name} value={materializationDraft[name]} onChange={(event) =>
            setMaterializationDraft({...materializationDraft,
              [name]: event.target.value})} />)}
        <FormControlLabel control={<Checkbox checked={materializationDraft.enabled}
          onChange={(event) => setMaterializationDraft({...materializationDraft,
            enabled: event.target.checked})} />} label={gettext('Enabled')} />
        <Button onClick={() => setDefinition({...definition,
          materializations: [...definition.materializations, materializationDraft]})}>
          {gettext('Add')}</Button>
      </Box>
      {definition.materializations.map((item, index) => <Box key={item.id}
        sx={{mt: 1}}>{item.name} — {item.strategy} — {item.enabled ? 'enabled' : 'disabled'}
        <Button disabled={!record || working ||
          !semantic.capabilities.materialization?.execution_available}
        onClick={() => planMaterialization(item.id)}>
          {gettext('Preview provider plan')}</Button>
        <Button onClick={() => setDefinition({...definition,
          materializations: definition.materializations.filter(
            (_value, position) => position !== index)})}>{gettext('Remove')}</Button></Box>)}
      {!semantic.capabilities.materialization?.execution_available &&
        <Alert severity="warning" sx={{mt: 1}}>
          {gettext('This provider does not activate physical semantic materializations.')}
        </Alert>}
      {materializationPlan && <Box sx={{mt: 1}}>
        <Box component="pre" aria-label={gettext('Semantic materialization plan')}
          sx={{whiteSpace: 'pre-wrap'}}>{JSON.stringify(materializationPlan, null, 2)}</Box>
        <Button color="warning" disabled={materializationPlan.state !== 'ready' ||
          !materializationPlan.execution_available} onClick={applyMaterialization}>
          {gettext('Confirm and create materialization')}</Button>
      </Box>}
    </Box>}
    {panel === 'lineage' && <Box sx={{mt: 2}}>
      <Button onClick={async () => {try {setLineage(await call(
        'semantic_model_lineage', {definition}));} catch (requestError) {
        setError(errorMessage(requestError));}}}>{gettext('Refresh lineage')}</Button>
      {(lineage?.nodes || []).map((node) => <Box key={node.id}
        sx={{p: 0.5}}><Box component="code">{node.kind}</Box> {node.label}</Box>)}
      {(lineage?.edges || []).map((edge, index) => <Box key={index}
        sx={{ml: 2}}>{edge.from} → {edge.to} ({edge.kind})</Box>)}
    </Box>}
    {panel === 'diagnostics' && <Box sx={{mt: 2}}>
      <Button disabled={working || !semantic.capabilities.execution_available}
        onClick={runDiagnostics}>{gettext('Refresh query diagnostics')}</Button>
      {diagnostics ? <Box component="pre" sx={{p: 1,
        bgcolor: 'background.default', overflow: 'auto', maxHeight: 520,
        whiteSpace: 'pre-wrap'}}>{JSON.stringify(diagnostics, null, 2)}</Box> :
        <Alert severity="info" sx={{mt: 1}}>
          {gettext('Compile the query to obtain provider diagnostics and a reproducibility manifest.')}
        </Alert>}
    </Box>}
    {panel === 'revisions' && <Box sx={{mt: 2}}>
      <Button disabled={history.length < 2} onClick={compareLatest}>
        {gettext('Compare latest revisions')}</Button>
      {history.map((item) => <Box key={item.revision}>
        r{item.revision} — {item.status} — {item.created_at}</Box>)}
      {comparison && <Box component="pre" sx={{whiteSpace: 'pre-wrap'}}>
        {JSON.stringify(comparison, null, 2)}</Box>}
    </Box>}
  </Box>;
}

SemanticModelWorkspace.propTypes = {
  semantic: PropTypes.object.isRequired,
  resources: PropTypes.array,
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

function OperationHistory({post, setError}) {
  const [operations, setOperations] = useState([]);
  const [working, setWorking] = useState(false);

  const load = useCallback(async () => {
    setWorking(true);
    setError(null);
    try {
      const response = await post({
        action: 'visual_admin_operation_list', request: {},
      });
      setOperations(response.items || []);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  }, [post, setError]);

  useEffect(() => { load(); }, [load]);

  const action = async (operationId, suffix) => {
    setWorking(true);
    setError(null);
    try {
      await post({
        action: `visual_admin_operation_${suffix}`,
        request: {operation_id: operationId},
      });
      await load();
    } catch (requestError) {
      setError(errorMessage(requestError));
      setWorking(false);
    }
  };

  return <Box sx={{p: 2, overflow: 'auto', flex: 1}}>
    <Box sx={{display: 'flex', alignItems: 'center', gap: 1}}>
      <Button variant="contained" disabled={working} onClick={load}>
        {gettext('Refresh operation list')}
      </Button>
      {working && <CircularProgress size={24} />}
    </Box>
    <Alert severity="info" sx={{mt: 2}}>
      {gettext('Operation state, cancellation, and finality are reported by the selected provider. CDEadmin does not retry mutations automatically.')}
    </Alert>
    {operations.map((operation) => <Box key={operation.operation_id}
      sx={{mt: 2, p: 2, border: 1, borderColor: 'divider'}}>
      <Box component="h3" sx={{m: 0}}>{operation.operation_kind}</Box>
      <Box>{operation.resource_kind} — {operation.stage}</Box>
      {operation.unknown_outcome && <Alert severity="warning" sx={{mt: 1}}>
        {gettext('The provider outcome is unknown. Do not replay this mutation; refresh or validate post-state.')}
      </Alert>}
      {operation.durable_audit && !operation.live_provider_handle_available &&
        <Alert severity="info" sx={{mt: 1}}>
          {gettext('This restart-safe audit record has no live provider handle. It can be reviewed, but provider refresh and cancellation are unavailable.')}
        </Alert>}
      {operation.impact && <Box sx={{mt: 1}}>
        {gettext('Impact')}: {operation.impact.scope || 'resource'} / {
          operation.impact.availability_risk || 'provider assessed'}
      </Box>}
      {(operation.events || []).length > 0 && <Box sx={{mt: 1}}>
        <Box component="strong">{gettext('Provider event timeline')}</Box>
        {(operation.events || []).map((event) => <Box
          key={`${operation.operation_id}-${event.sequence}`}
          sx={{display: 'grid',
            gridTemplateColumns: '4em minmax(12em, 1fr) minmax(12em, auto)',
            gap: 1, fontFamily: 'monospace', fontSize: '0.85em'}}>
          <Box>#{event.sequence}</Box>
          <Box>{event.event_kind}</Box>
          <Box>{event.occurred_at}</Box>
        </Box>)}
      </Box>}
      <Box sx={{display: 'flex', gap: 1, mt: 1}}>
        <Button disabled={working || !operation.live_provider_handle_available}
          onClick={() => action(operation.operation_id, 'refresh')}>
          {gettext('Observe provider state')}
        </Button>
        <Button color="warning" disabled={working ||
          !operation.live_provider_handle_available || !operation.cancellable ||
          operation.cancel_request_dispatched}
        onClick={() => action(operation.operation_id, 'cancel')}>
          {operation.cancel_request_dispatched ? gettext('Cancel requested') :
            gettext('Request cancellation')}
        </Button>
        <Button disabled={working || !operation.live_provider_handle_available}
          onClick={() => action(operation.operation_id, 'post_state')}>
          {gettext('Validate post-state')}
        </Button>
      </Box>
      <Box component="pre" sx={{overflow: 'auto', maxHeight: 220,
        bgcolor: 'background.default', p: 1}}>
        {JSON.stringify(operation, null, 2)}
      </Box>
    </Box>)}
    {!working && operations.length === 0 && <Box sx={{mt: 2}}>
      {gettext('No provider administration operations have been recorded for this endpoint.')}
    </Box>}
  </Box>;
}

OperationHistory.propTypes = {
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

const OPERATIONAL_STATE_FIELDS = [
  'health', 'status', 'state', 'role', 'node_role', 'member_role', 'leader',
  'primary', 'region', 'zone', 'locality', 'replication_lag', 'lag',
  'progress', 'phase', 'started_at', 'updated_at', 'size', 'storage_usage',
  'latency', 'connections', 'sessions', 'locks', 'version',
];

function operationalSnapshot(resource) {
  const native = providerNative(resource);
  return Object.fromEntries(OPERATIONAL_STATE_FIELDS.filter((field) =>
    Object.prototype.hasOwnProperty.call(native, field)
  ).map((field) => [field, native[field]]));
}

function TopologyView({workspace, resources}) {
  const kinds = new Set(workspace?.topology?.resource_kinds || []);
  const nodes = (resources || []).filter((item) => kinds.has(
    item.resource_kind
  ));
  if (!workspace?.topology?.available) {
    return <Alert severity="info">
      {gettext('This provider does not declare topology resources.')}
    </Alert>;
  }
  if (!nodes.length) {
    return <Alert severity="info">
      {gettext('No topology resources were discovered. Refresh provider objects to observe current membership and placement.')}
    </Alert>;
  }
  return <Box aria-label={gettext('Provider topology visualization')}
    sx={{display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 1}}>
    {nodes.map((node) => {
      const path = node.authority_path || node.display_path || [];
      const snapshot = operationalSnapshot(node);
      return <Box key={node.resource_id} sx={{border: 1,
        borderColor: 'divider', borderLeftWidth: 4, p: 1}}>
        <Box component="strong">{node.display_name}</Box>
        <Box sx={{fontSize: '0.8em'}}>{node.resource_kind}</Box>
        {path.length > 0 && <Box sx={{fontSize: '0.8em', mt: 0.5,
          overflowWrap: 'anywhere'}}>{path.join(' → ')}</Box>}
        {Object.keys(snapshot).length > 0 && <Box component="pre"
          sx={{m: 0, mt: 1, whiteSpace: 'pre-wrap', fontSize: '0.75em'}}>
          {JSON.stringify(snapshot, null, 2)}
        </Box>}
      </Box>;
    })}
  </Box>;
}

TopologyView.propTypes = {
  workspace: PropTypes.object,
  resources: PropTypes.array,
};

function OperationalWorkspace({workspace, endpoint, catalog, resources,
  resourceGeneration, onRefresh, refreshing, post, setError}) {
  const facets = workspace?.facets || [];
  const initialFacet = facets.find((item) =>
    item.catalog_state !== 'unavailable') || facets[0];
  const [facetId, setFacetId] = useState(initialFacet?.facet_id || '');
  const [showHistory, setShowHistory] = useState(false);
  const facet = facets.find((item) => item.facet_id === facetId) ||
    initialFacet;

  useEffect(() => {
    if (!facets.some((item) => item.facet_id === facetId)) {
      setFacetId(initialFacet?.facet_id || '');
    }
  }, [facetId, facets, initialFacet]);

  const resourceKinds = useMemo(
    () => new Set(facet?.resource_kinds || []), [facet]
  );
  const facetResources = useMemo(() => (resources || []).filter((item) =>
    resourceKinds.has(item.resource_kind)), [resourceKinds, resources]);
  const operationKeys = useMemo(() => new Set((facet?.operations || []).map(
    (item) => `${item.resource_kind}:${item.operation_id}`
  )), [facet]);
  const commandCatalog = useMemo(() => {
    if (!catalog || !operationKeys.size) return null;
    const objects = (catalog.objects || []).map((objectItem) => ({
      ...objectItem,
      operations: (objectItem.operations || []).filter((operation) =>
        operationKeys.has(
          `${objectItem.resource_kind}:${operation.operation_id}`
        )),
    })).filter((objectItem) => objectItem.operations.length > 0);
    return objects.length > 0 ? {...catalog, objects} : null;
  }, [catalog, operationKeys]);
  const categories = workspace?.categories || [];

  if (!workspace) return <Alert severity="info">
    {gettext('This provider does not publish an operational workspace.')}
  </Alert>;

  return <Box sx={{display: 'grid', gridTemplateColumns:
    'minmax(220px, 300px) minmax(0, 1fr)', flex: 1, minHeight: 0}}>
    <Box sx={{borderRight: 1, borderColor: 'divider', p: 1,
      overflow: 'auto'}}>
      <Box component="h3" sx={{m: 1}}>{gettext('Operational workspaces')}</Box>
      {categories.map((category) => <Box key={category} sx={{mb: 1}}>
        <Box sx={{px: 1, py: 0.5, textTransform: 'uppercase',
          fontSize: '0.72em', color: 'text.secondary'}}>
          {category.replaceAll('-', ' ')}
        </Box>
        {facets.filter((item) => item.category === category).map((item) =>
          <Button key={item.facet_id} fullWidth
            variant={item.facet_id === facet?.facet_id ? 'contained' : 'text'}
            color={item.catalog_state === 'unavailable' ? 'inherit' : 'primary'}
            onClick={() => {setFacetId(item.facet_id); setShowHistory(false);}}
            sx={{justifyContent: 'space-between', textAlign: 'left'}}>
            <span>{item.title}</span>
            <span>{item.catalog_state === 'unavailable' ? '—' :
              (resources || []).filter((resource) =>
                item.resource_kinds.includes(resource.resource_kind)
              ).length}</span>
          </Button>)}
      </Box>)}
      <Button fullWidth variant={showHistory ? 'contained' : 'outlined'}
        onClick={() => setShowHistory(true)}>
        {gettext('Operation progress and history')}
      </Button>
    </Box>
    <Box sx={{overflow: 'auto', minWidth: 0}}>
      {showHistory ? <OperationHistory post={post} setError={setError} /> : <>
        <Box sx={{p: 2, pb: 0}}>
          <Box sx={{display: 'flex', gap: 1, alignItems: 'center',
            flexWrap: 'wrap'}}>
            <Box component="h2" sx={{m: 0}}>{facet?.title}</Box>
            <Button disabled={refreshing || !resourceGeneration}
              onClick={onRefresh}>{gettext('Refresh provider state')}</Button>
            {refreshing && <CircularProgress size={20} />}
          </Box>
          <Box sx={{color: 'text.secondary', mt: 0.5}}>{facet?.summary}</Box>
          <Box sx={{fontSize: '0.85em', mt: 0.5}}>
            {workspace.engine_id} · {endpoint?.runtime_verification_state ||
              gettext('unverified runtime')}
            {endpoint?.verified_runtime_version ?
              ` · ${endpoint.verified_runtime_version}` : ''}
          </Box>
          <Alert severity="info" sx={{mt: 1}}>
            {gettext('Resources, state, plans, progress, cancellation, and finality are provider-reported. CDEadmin does not infer success or automatically replay mutations.')}
          </Alert>
          {facet?.catalog_state === 'unavailable' && <Alert severity="warning"
            sx={{mt: 1}}>{facet.unavailable_reason}</Alert>}
          {facet?.facet_id === 'topology' && <Box sx={{mt: 2}}>
            <TopologyView workspace={workspace} resources={resources} />
          </Box>}
          {facet?.facet_id !== 'topology' && facetResources.length > 0 &&
            <Box sx={{mt: 2}}>
              <Box component="h3">{gettext('Provider observations')}</Box>
              <Box sx={{display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                gap: 1}}>
                {facetResources.map((resource) => <Box
                  key={resource.resource_id} sx={{border: 1,
                    borderColor: 'divider', p: 1}}>
                  <Box component="strong">{resource.display_name}</Box>
                  <Box sx={{fontSize: '0.8em'}}>{resource.resource_kind}</Box>
                  <Box component="pre" sx={{m: 0, mt: 1,
                    whiteSpace: 'pre-wrap', maxHeight: 150, overflow: 'auto',
                    fontSize: '0.75em'}}>
                    {JSON.stringify(operationalSnapshot(resource), null, 2)}
                  </Box>
                </Box>)}
              </Box>
            </Box>}
        </Box>
        {commandCatalog && <Box sx={{mt: 1, borderTop: 1,
          borderColor: 'divider'}}>
          <VisualAdministration catalog={commandCatalog}
            resources={resources} resourceGeneration={resourceGeneration}
            post={post} setError={setError} />
        </Box>}
      </>}
    </Box>
  </Box>;
}

OperationalWorkspace.propTypes = {
  workspace: PropTypes.object,
  endpoint: PropTypes.object,
  catalog: PropTypes.object,
  resources: PropTypes.array,
  resourceGeneration: PropTypes.string,
  onRefresh: PropTypes.func.isRequired,
  refreshing: PropTypes.bool,
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

function routeDraft(catalog, route) {
  const configuration = route?.configuration || {};
  const draft = {
    route_id: route?.route_id || null,
    priority: route?.priority ?? (catalog?.routes?.length || 0),
    host: configuration.host || '',
    port: configuration.port || catalog?.default_port || '',
    user: configuration.user || '',
    database: configuration.database || '',
  };
  (catalog?.connection_fields || []).forEach((field) => {
    let value = configuration[field.route_key];
    if (value === undefined) value = initialFieldValue(field);
    if (field.control === 'json' && typeof value !== 'string') {
      value = JSON.stringify(value, null, 2);
    }
    draft[field.field_id] = value;
  });
  return draft;
}

function ConnectionRouteWorkspace({post, setError}) {
  const [catalog, setCatalog] = useState(null);
  const [draft, setDraft] = useState(null);
  const [working, setWorking] = useState(false);

  const load = useCallback(async () => {
    setWorking(true);
    try {
      const value = await post({action: 'route_list'});
      setCatalog(value);
      setDraft((current) => routeDraft(value,
        value.routes.find((item) => item.route_id === current?.route_id) ||
        value.routes[0]));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  }, [post, setError]);

  useEffect(() => { load(); }, [load]);

  const select = (routeId) => {
    const route = catalog.routes.find((item) => item.route_id === routeId);
    setDraft(routeDraft(catalog, route));
  };

  const request = () => {
    const value = {
      route_id: draft.route_id,
      priority: Number(draft.priority),
    };
    if (catalog.supports_multiple_routes) {
      Object.assign(value, {
        host: draft.host,
        port: Number(draft.port),
        user: draft.user,
        database: draft.database,
      });
    }
    (catalog.connection_fields || []).filter((field) =>
      fieldVisible(field, draft)).forEach((field) => {
      let fieldValue = draft[field.field_id];
      if (field.control === 'number' && fieldValue !== '') {
        fieldValue = Number(fieldValue);
      } else if (field.control === 'json' && fieldValue) {
        fieldValue = JSON.parse(fieldValue);
      }
      value[`cde_route_${field.field_id}`] = fieldValue;
    });
    return value;
  };

  const save = async () => {
    setWorking(true);
    setError(null);
    try {
      const action = draft.route_id ? 'route_update' : 'route_create';
      const value = await post({action, request: request()});
      setCatalog(value);
      setDraft(routeDraft(value, value.routes.find((item) =>
        item.route_id === draft.route_id) ||
        value.routes[value.routes.length - 1]));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  const remove = async () => {
    setWorking(true);
    setError(null);
    try {
      const value = await post({action: 'route_delete', request: {
        route_id: draft.route_id,
      }});
      setCatalog(value);
      setDraft(routeDraft(value, value.routes[0]));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setWorking(false);
    }
  };

  if (!catalog || !draft) return <Box p={3}><CircularProgress /></Box>;
  return <Box sx={{p: 2, overflow: 'auto', flex: 1}}>
    <Alert severity="info" sx={{mb: 2}}>
      {gettext('Routes share the endpoint credential profile. CDEadmin only fails over before a provider session exists and never replays mutations.')}
    </Alert>
    <Box sx={{display: 'flex', gap: 1, mb: 2}}>
      <TextField select label={gettext('Route')} value={draft.route_id || ''}
        sx={{minWidth: 320}} onChange={(event) => select(event.target.value)}>
        {catalog.routes.map((route) => <MenuItem key={route.route_id}
          value={route.route_id}>{`${route.priority}: ${route.configuration.host || route.configuration.database}`}</MenuItem>)}
      </TextField>
      <Button disabled={working || !catalog.supports_multiple_routes}
        onClick={() => setDraft(routeDraft(catalog))}>
        {gettext('New route')}
      </Button>
      <Button color="error" disabled={working || !draft.route_id ||
        catalog.routes.length < 2} onClick={remove}>{gettext('Delete')}</Button>
      <Button variant="contained" disabled={working} onClick={save}>
        {gettext('Save route')}
      </Button>
      <Button disabled={working} onClick={load}>{gettext('Refresh')}</Button>
    </Box>
    <Box sx={{display: 'grid', gridTemplateColumns: 'repeat(2, minmax(260px, 1fr))', gap: 2}}>
      {(catalog.supports_multiple_routes ?
        ['host', 'port', 'user', 'database', 'priority'] :
        ['priority']).map((name) =>
        <TextField key={name} label={gettext(name)} value={draft[name]}
          type={['port', 'priority'].includes(name) ? 'number' : 'text'}
          onChange={(event) => setDraft({...draft,
            [name]: event.target.value})} />)}
      {(catalog.connection_fields || []).filter((field) =>
        fieldVisible(field, draft)).map((field) =>
        <VisualAdminField key={field.field_id} field={field}
          value={draft[field.field_id]} onChange={(value) => setDraft({
            ...draft, [field.field_id]: value,
          })} />)}
    </Box>
    {draft.route_id && <Box component="pre" sx={{mt: 2, maxHeight: 160,
      overflow: 'auto'}}>{JSON.stringify(catalog.routes.find((item) =>
        item.route_id === draft.route_id)?.health || {}, null, 2)}</Box>}
  </Box>;
}

ConnectionRouteWorkspace.propTypes = {
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

function parseCsv(source) {
  const rows = [];
  let row = [];
  let value = '';
  let quoted = false;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (character === '"' && quoted && source[index + 1] === '"') {
      value += '"'; index += 1;
    } else if (character === '"') quoted = !quoted;
    else if (character === ',' && !quoted) { row.push(value); value = ''; }
    else if ((character === '\n' || character === '\r') && !quoted) {
      if (character === '\r' && source[index + 1] === '\n') index += 1;
      row.push(value); value = '';
      if (row.some((item) => item !== '')) rows.push(row);
      row = [];
    } else value += character;
  }
  row.push(value);
  if (row.some((item) => item !== '')) rows.push(row);
  if (quoted || rows.length < 2) throw new Error(gettext('CSV input is invalid.'));
  const headers = rows[0];
  if (new Set(headers).size !== headers.length || headers.some((item) => !item)) {
    throw new Error(gettext('CSV headers must be unique and non-empty.'));
  }
  return rows.slice(1).map((values) => Object.fromEntries(headers.map(
    (name, index) => [name, values[index] ?? '']
  )));
}

function DataMovementWorkspace({catalog, resources, post, setError}) {
  const choices = useMemo(() => (catalog?.objects || []).flatMap((object) =>
    (object.operations || []).filter((operation) =>
      operation.execution_available && operation.mutation_class !== 'read'
    ).map((operation) => ({object, operation,
      id: `${object.resource_kind}\u0000${operation.operation_id}`}))), [catalog]);
  const [choiceId, setChoiceId] = useState(choices[0]?.id || '');
  const [targetId, setTargetId] = useState('');
  const [format, setFormat] = useState('json');
  const [source, setSource] = useState('[]');
  const [plan, setPlan] = useState(null);
  const [result, setResult] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [working, setWorking] = useState(false);
  const choice = choices.find((item) => item.id === choiceId) || choices[0];
  const targets = useMemo(() => {
    const targetKinds = choice?.operation.target_resource_kinds ||
      [choice?.object.resource_kind];
    return (resources || []).filter((resource) =>
      targetKinds.includes(resource.resource_kind));
  }, [choice, resources]);

  useEffect(() => {
    if (!choices.some((item) => item.id === choiceId)) {
      setChoiceId(choices[0]?.id || '');
    }
  }, [choiceId, choices]);
  useEffect(() => {
    if (!targets.some((item) => item.resource_id === targetId)) {
      setTargetId(targets[0]?.resource_id || '');
    }
  }, [targetId, targets]);

  const records = () => {
    let parsed;
    if (format === 'json') parsed = JSON.parse(source || '[]');
    else if (format === 'jsonl') parsed = (source || '').split(/\r?\n/)
      .filter((line) => line.trim()).map((line) => JSON.parse(line));
    else parsed = parseCsv(source || '');
    if (!Array.isArray(parsed) || parsed.length < 1 || parsed.length > 500 ||
      parsed.some((item) => !item || Array.isArray(item) ||
        typeof item !== 'object')) {
      throw new Error(gettext('Import must contain 1 to 500 object records.'));
    }
    return parsed;
  };
  const draftFor = (record) => {
    const fields = choice.operation.form?.fields || [];
    const fieldNames = new Set(fields.map((field) => field.field_id));
    if (Object.keys(record).every((name) => fieldNames.has(name))) return record;
    const container = fields.find((field) =>
      ['values', 'document', 'record', 'properties'].includes(field.field_id));
    if (container) return {[container.field_id]: record};
    return record;
  };
  const preview = async () => {
    setWorking(true); setError(null); setResult(null); setConfirmed(false);
    try {
      if (!choice) throw new Error(gettext('No bulk operation is available.'));
      const target = targets.find((item) => item.resource_id === targetId) || null;
      const items = records().map((record) => ({
        resource_kind: choice.object.resource_kind,
        operation_id: choice.operation.operation_id,
        target_resource: target,
        draft: draftFor(record),
      }));
      setPlan(await post({action: 'visual_admin_bulk_plan', request: {items}}));
    } catch (requestError) {
      setPlan(null); setError(errorMessage(requestError));
    } finally { setWorking(false); }
  };
  const apply = async () => {
    setWorking(true); setError(null);
    try {
      setResult(await post({action: 'visual_admin_bulk_apply', request: {
        confirmed,
        plans: plan.plans.map(({plan: item}) => ({
          plan_id: item.plan_id, plan_digest: item.plan_digest,
        })),
      }}));
      setPlan(null); setConfirmed(false);
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setWorking(false); }
  };
  return <Box sx={{p: 2, overflow: 'auto', flex: 1}}>
    <Alert severity="info" sx={{mb: 2}}>
      {gettext('Import and bulk edit use provider forms and native plans. Batches are ordered and are not claimed to be atomic; automatic mutation retry is disabled.')}
    </Alert>
    <Box sx={{display: 'grid', gridTemplateColumns:
      'minmax(220px, 2fr) minmax(140px, 1fr)', gap: 2}}>
      <TextField select label={gettext('Provider bulk operation')}
        value={choice?.id || ''} onChange={(event) => {
          setChoiceId(event.target.value); setPlan(null);
        }}>
        {choices.map((item) => <MenuItem key={item.id} value={item.id}>
          {item.object.title} — {item.operation.title}
        </MenuItem>)}
      </TextField>
      <TextField select label={gettext('Import format')} value={format}
        onChange={(event) => {setFormat(event.target.value); setPlan(null);}}>
        <MenuItem value="json">JSON array</MenuItem>
        <MenuItem value="jsonl">JSON Lines</MenuItem>
        <MenuItem value="csv">CSV with header</MenuItem>
      </TextField>
    </Box>
    {choice?.operation.target_required && <TextField select fullWidth sx={{mt: 2}}
      label={gettext('Target resource')} value={targetId}
      onChange={(event) => {setTargetId(event.target.value); setPlan(null);}}>
      {targets.map((item) => <MenuItem key={item.resource_id}
        value={item.resource_id}>{item.display_name}</MenuItem>)}
    </TextField>}
    <TextField fullWidth multiline minRows={8} maxRows={20} sx={{mt: 2}}
      label={gettext('Import records / provider form drafts')} value={source}
      onChange={(event) => {setSource(event.target.value); setPlan(null);}} />
    <Box sx={{display: 'flex', gap: 1, mt: 2}}>
      <Button component="label">{gettext('Choose import file')}
        <input hidden type="file" accept=".json,.jsonl,.ndjson,.csv"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (!file || file.size > 4 * 1024 * 1024) {
              setError(gettext('Import files must not exceed 4 MiB.'));
              return;
            }
            const extension = file.name.split('.').pop()?.toLowerCase();
            setFormat(extension === 'csv' ? 'csv' :
              ['jsonl', 'ndjson'].includes(extension) ? 'jsonl' : 'json');
            file.text().then((value) => {setSource(value); setPlan(null);})
              .catch((requestError) => setError(errorMessage(requestError)));
          }} />
      </Button>
      <Button variant="contained" disabled={working || !choice ||
        (choice.operation.target_required && !targetId)} onClick={preview}>
        {gettext('Validate and preview batch')}</Button>
      <Button color="warning" disabled={working || !plan?.ready || !confirmed}
        onClick={apply}>{gettext('Apply confirmed batch')}</Button>
      {working && <CircularProgress size={24} />}
    </Box>
    {plan && <><FormControlLabel control={<Checkbox checked={confirmed}
      onChange={(event) => setConfirmed(event.target.checked)} />}
    label={gettext('I confirm every provider-planned mutation in this non-atomic batch.')} />
    <Box component="pre" aria-label={gettext('Bulk operation preview')}
      sx={{p: 1, maxHeight: 320, overflow: 'auto', bgcolor: 'background.default'}}>
      {JSON.stringify(plan, null, 2)}
    </Box></>}
    {result && <Box component="pre" aria-label={gettext('Bulk operation result')}
      sx={{p: 1, maxHeight: 320, overflow: 'auto', bgcolor: 'background.default'}}>
      {JSON.stringify(result, null, 2)}
    </Box>}
  </Box>;
}

DataMovementWorkspace.propTypes = {
  catalog: PropTypes.object,
  resources: PropTypes.array,
  post: PropTypes.func.isRequired,
  setError: PropTypes.func.isRequired,
};

export default function ProviderWorkspaceContent({
  closeModal, endpointUrl, initialTab='resources',
}) {
  const api = useMemo(() => getApiInstance(), []);
  const [tab, setTab] = useState(initialTab);
  const [workspace, setWorkspace] = useState(null);
  const [source, setSource] = useState('SELECT 1');
  const [languageProfile, setLanguageProfile] = useState('');
  const [parameterSource, setParameterSource] = useState('{}');
  const [sessionId, setSessionId] = useState(null);
  const [occurrenceId, setOccurrenceId] = useState(null);
  const [rendered, setRendered] = useState(null);
  const [resultHistory, setResultHistory] = useState([]);
  const [resultPresentation, setResultPresentation] = useState('native');
  const [transaction, setTransaction] = useState(null);
  const [selectedResource, setSelectedResource] = useState(null);
  const [resourcePage, setResourcePage] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.get(endpointUrl).then((response) => {
      setWorkspace(response.data.data);
      setResourcePage(response.data.data?.resource_page || null);
      const profile = response.data.data?.languages?.[0]?.language_profile || '';
      setLanguageProfile(profile);
      setSource(defaultSource(profile));
      setBusy(false);
    }).catch((requestError) => {
      setError(errorMessage(requestError));
      setBusy(false);
    });
  }, [api, endpointUrl]);

  const post = useCallback((payload) => api.post(endpointUrl, payload)
    .then((response) => response.data.data), [api, endpointUrl]);

  const acceptRendered = useCallback((value) => {
    setRendered(value);
    const resultId = value?.descriptor?.result_id;
    if (!resultId) return;
    setResultHistory((current) => [
      ...current.filter((item) => item.descriptor?.result_id !== resultId),
      value,
    ].slice(-10));
  }, []);

  useEffect(() => {
    if (selectedResource && !resourcePage?.items?.some((item) =>
      item.resource_id === selectedResource.resource_id)) {
      setSelectedResource(null);
    }
  }, [resourcePage, selectedResource]);

  const loadMoreResources = async () => {
    if (!resourcePage?.next_cursor) return;
    setLoadingMore(true);
    setError(null);
    try {
      const next = await post({
        action: 'resource_page', request: {
          continuation: resourcePage.next_cursor,
          generation: resourcePage.generation,
        },
      });
      if (next.generation !== resourcePage.generation) {
        throw new Error(gettext(
          'Provider objects changed while paging. Reopen the workspace.'
        ));
      }
      setResourcePage({
        ...next,
        items: [...(resourcePage.items || []), ...(next.items || [])],
      });
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setLoadingMore(false);
    }
  };

  const refreshResources = async () => {
    if (!resourcePage?.generation) return;
    setLoadingMore(true);
    setError(null);
    try {
      const refreshed = await post({
        action: 'resource_refresh', request: {
          generation: resourcePage.generation,
        },
      });
      setResourcePage(refreshed);
      setSelectedResource(null);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setLoadingMore(false);
    }
  };

  const ensureSession = useCallback(async () => {
    if (sessionId) return sessionId;
    const language = languageProfile;
    if (!language) throw new Error(gettext('No query language is available.'));
    const opened = await post({
      action: 'open_session', language_profile: language,
    });
    setSessionId(opened.session_id);
    return opened.session_id;
  }, [languageProfile, post, sessionId]);

  const poll = useCallback(async (id) => {
    setBusy(true);
    setError(null);
    try {
      const response = await post({action: 'poll', occurrence_id: id});
      acceptRendered(response.rendered_result);
      setOccurrenceId(response.occurrence?.operation?.terminal ? null : id);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }, [acceptRendered, post]);

  const execute = async () => {
    setBusy(true);
    setError(null);
    setRendered(null);
    setResultPresentation('native');
    try {
      const activeSession = await ensureSession();
      const occurrence = await post({
        action: 'execute', session_id: activeSession, source,
        parameters: JSON.parse(parameterSource || '{}'),
      });
      setOccurrenceId(occurrence.occurrence_id);
      await poll(occurrence.occurrence_id);
    } catch (requestError) {
      setError(errorMessage(requestError));
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!occurrenceId) return;
    setBusy(true);
    try {
      await post({action: 'cancel', occurrence_id: occurrenceId});
      await poll(occurrenceId);
    } catch (requestError) {
      setError(errorMessage(requestError));
      setBusy(false);
    }
  };

  const refreshTransaction = async () => {
    setBusy(true);
    try {
      const activeSession = await ensureSession();
      setTransaction(await post({
        action: 'transaction', session_id: activeSession,
      }));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  };

  const controlTransaction = async (action) => {
    setBusy(true); setError(null);
    try {
      const activeSession = await ensureSession();
      setTransaction(await post({
        action: 'transaction_action', session_id: activeSession,
        transaction_action: action,
      }));
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally { setBusy(false); }
  };

  const selectLanguage = (profile) => {
    setLanguageProfile(profile);
    setSource(defaultSource(profile));
    setSessionId(null);
    setOccurrenceId(null);
    setRendered(null);
    setTransaction(null);
  };

  const activeLanguage = workspace?.languages?.find((item) =>
    item.language_profile === languageProfile) || workspace?.languages?.[0];

  return (
    <ModalContent>
      <Tabs value={tab} onChange={(_event, value) => setTab(value)}>
        <Tab value="resources" label={gettext('Resource Explorer')} />
        <Tab value="studio" label={gettext('Data Studio')} />
        <Tab value="data" label={gettext('Edit Data')} />
        <Tab value="administration" label={gettext('Administration')} />
        <Tab value="operations" label={gettext('Operations & Health')} />
        <Tab value="semantic" label={gettext('Cubes & Semantic Models')} />
        <Tab value="movement" label={gettext('Import & Bulk')} />
        {workspace?.endpoint?.route_management_available &&
          <Tab value="connections" label={gettext('Connections')} />}
        {workspace?.visual_admin?.model_family === 'document' &&
          <Tab value="streams" label={gettext('Change streams')} />}
      </Tabs>
      {error && <Alert severity="error">{error}</Alert>}
      {busy && !workspace && <Box p={3}><CircularProgress /></Box>}
      {workspace && tab === 'resources' &&
        <ResourceExplorer catalog={workspace.visual_admin}
          page={resourcePage}
          selectedResourceId={selectedResource?.resource_id}
          onSelect={setSelectedResource}
          onOpenAdministration={() => setTab('administration')}
          onOpenData={() => setTab('data')}
          onLoadMore={loadMoreResources} onRefresh={refreshResources}
          loadingMore={loadingMore} />}
      {workspace && tab === 'studio' && <Box sx={{p: 2, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0}}>
        <TextField select size="small" sx={{mb: 1, maxWidth: 360}}
          label={gettext('Provider language')} value={languageProfile}
          onChange={(event) => selectLanguage(event.target.value)}>
          {(workspace.languages || []).map((language) => <MenuItem
            key={language.language_profile} value={language.language_profile}>
            {language.title}
          </MenuItem>)}
        </TextField>
        <Box sx={{display: 'flex', gap: 1, mb: 1, flexWrap: 'wrap'}}
          aria-label={gettext('Provider language examples')}>
          {sourcePresets(languageProfile).map(([label, example]) =>
            <Button size="small" key={label} onClick={() => setSource(example)}>
              {label}</Button>)}
        </Box>
        <TextField multiline minRows={7} maxRows={14} value={source}
          onChange={(event) => setSource(event.target.value)}
          inputProps={{'aria-label': gettext('Query source')}} />
        <TextField multiline minRows={2} maxRows={6} value={parameterSource}
          sx={{mt: 1}} label={gettext('Query parameters (JSON)')}
          onChange={(event) => setParameterSource(event.target.value)} />
        <Box sx={{display: 'flex', gap: 1, mt: 1}}>
          <Button variant="contained" disabled={busy || !source.trim()} onClick={execute}>{gettext('Run')}</Button>
          <Button disabled={busy || !occurrenceId} onClick={() => poll(occurrenceId)}>{gettext('Poll')}</Button>
          <Button disabled={busy || !occurrenceId} onClick={cancel}>{gettext('Cancel request')}</Button>
          <Button disabled={busy} onClick={refreshTransaction}>{gettext('Provider transaction state')}</Button>
          {(activeLanguage?.transaction_actions || []).map((action) =>
            <Button key={action} color={action === 'rollback' ? 'warning' : 'primary'}
              disabled={busy} onClick={() => controlTransaction(action)}>
              {gettext(action)}</Button>)}
          {busy && <CircularProgress size={24} />}
        </Box>
        {transaction && <Box component="pre" sx={{overflow: 'auto', maxHeight: 120}}>{JSON.stringify(transaction, null, 2)}</Box>}
        {rendered && <ResultControls rendered={rendered} history={resultHistory}
          post={post} onRendered={acceptRendered} setError={setError}
          setBusy={setBusy} />}
        {rendered && <Box sx={{display: 'flex', gap: 1, mt: 1}}>
          <Button variant={resultPresentation === 'native' ? 'contained' : 'text'}
            onClick={() => setResultPresentation('native')}>
            {gettext('Native result view')}</Button>
          <Button variant={resultPresentation === 'plan' ? 'contained' : 'text'}
            onClick={() => setResultPresentation('plan')}>
            {gettext('Explain / query plan view')}</Button>
        </Box>}
        {rendered && resultPresentation === 'native' &&
          <ResultView rendered={rendered} />}
        {rendered && resultPresentation === 'plan' &&
          <QueryPlanView rendered={rendered} />}
      </Box>}
      {workspace && tab === 'administration' &&
        <VisualAdministration catalog={workspace.visual_admin}
          resources={resourcePage?.items || []}
          selectedResource={selectedResource}
          resourceGeneration={resourcePage?.generation}
          post={post} setError={setError} />}
      {workspace && tab === 'operations' &&
        <OperationalWorkspace workspace={workspace.operational_workspace}
          endpoint={workspace.endpoint}
          catalog={workspace.visual_admin}
          resources={resourcePage?.items || []}
          resourceGeneration={resourcePage?.generation}
          onRefresh={refreshResources} refreshing={loadingMore}
          post={post} setError={setError} />}
      {workspace && tab === 'semantic' &&
        <SemanticModelWorkspace semantic={workspace.semantic_models}
          resources={resourcePage?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'movement' &&
        <DataMovementWorkspace catalog={workspace.visual_admin}
          resources={resourcePage?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'connections' &&
        <ConnectionRouteWorkspace post={post} setError={setError} />}
      {workspace && tab === 'data' && workspace.visual_admin?.model_family === 'document' &&
        <DocumentDataGrid catalog={workspace.visual_admin}
          resources={resourcePage?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'data' && workspace.visual_admin?.model_family === 'graph' &&
        <GraphDataStudio catalog={workspace.visual_admin}
          resources={resourcePage?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'data' && workspace.visual_admin?.model_family ===
        'data-structure-key-value' &&
        <KeyValueDataGrid catalog={workspace.visual_admin}
          resources={resourcePage?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'data' && [
        'time-series-analytic', 'vector-analytic', 'search-analytic',
        'search-document-analytic', 'columnar-analytic', 'wide-column',
        'bitemporal-document-relational',
      ].includes(workspace.visual_admin?.model_family) &&
        <AnalyticDataBrowser
          modelFamily={workspace.visual_admin.model_family}
          resources={resourcePage?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'data' && ![
        'document', 'graph', 'data-structure-key-value',
        'time-series-analytic', 'vector-analytic', 'search-analytic',
        'search-document-analytic', 'columnar-analytic', 'wide-column',
        'bitemporal-document-relational',
      ].includes(
        workspace.visual_admin?.model_family
      ) &&
        <StructuredDataGrid catalog={workspace.visual_admin}
          resources={resourcePage?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'streams' && workspace.visual_admin?.model_family === 'document' &&
        <ChangeStreamViewer resources={resourcePage?.items || []}
          languageProfile={workspace.languages?.[0]?.language_profile}
          post={post} setError={setError} />}
      <ModalFooter><Button onClick={closeModal}>{gettext('Close')}</Button></ModalFooter>
    </ModalContent>
  );
}

ProviderWorkspaceContent.propTypes = {
  closeModal: PropTypes.func,
  endpointUrl: PropTypes.string.isRequired,
  initialTab: PropTypes.oneOf([
    'resources', 'studio', 'data', 'administration', 'operations',
    'semantic', 'movement', 'streams', 'connections',
  ]),
};
