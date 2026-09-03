/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import { useCallback, useEffect, useMemo, useState } from 'react';
import PropTypes from 'prop-types';
import gettext from 'sources/gettext';
import {
  Alert, Box, Button, Checkbox, CircularProgress, FormControlLabel,
  MenuItem, Tab, Tabs, TextField,
} from '@mui/material';
import getApiInstance from '../api_instance';
import { ModalContent, ModalFooter } from '../components/ModalContent';

function errorMessage(error) {
  return error?.response?.data?.errormsg || error?.message ||
    gettext('The provider workspace request failed.');
}

function ResourceExplorer({page, selectedResourceId, onSelect}) {
  const items = page?.items || [];
  return (
    <Box sx={{overflow: 'auto', flex: 1, p: 2}}>
      <Box component="table" sx={{width: '100%', borderCollapse: 'collapse'}}>
        <thead><tr>
          <th align="left">{gettext('Type')}</th>
          <th align="left">{gettext('Name')}</th>
          <th align="left">{gettext('Authority path')}</th>
        </tr></thead>
        <tbody>{items.map((item) => (
          <tr key={item.resource_id}
            aria-selected={selectedResourceId === item.resource_id}
            onClick={() => onSelect(item)}
            style={{cursor: 'pointer', background: selectedResourceId === item.resource_id ? 'rgba(0, 120, 212, 0.12)' : undefined}}>
            <td>{item.resource_kind}</td>
            <td>{item.display_name}</td>
            <td>{(item.authority_path || []).join(' / ')}</td>
          </tr>
        ))}</tbody>
      </Box>
      {items.length === 0 && <Box>{gettext('No resources returned.')}</Box>}
    </Box>
  );
}

ResourceExplorer.propTypes = {
  page: PropTypes.object,
  selectedResourceId: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
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

function VisualAdministration({catalog, resources, post, setError}) {
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

  const load = async () => {
    if (!target) return;
    setWorking(true);
    setError(null);
    try {
      const nextPage = await post({
        action: 'visual_admin_rows',
        request: {target_resource: target, limit: 200},
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
      if (modelFamily !== 'time-series-analytic') {
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
    {modelFamily !== 'time-series-analytic' && <TextField fullWidth multiline
      minRows={2} sx={{mt: 2}} label={gettext('Native filter (JSON)')}
      value={filterSource}
      onChange={(event) => setFilterSource(event.target.value)} />}
    {containers.length === 0 && <Alert severity="info" sx={{mt: 2}}>
      {gettext('No discovered analytic data container is available.')}
    </Alert>}
    {page && <Alert severity="info" sx={{mt: 2}}>
      {gettext('Use the Administration tab for provider-validated inserts, updates, deletes, schema changes, indexes, retention, and security operations.')}
    </Alert>}
    {records.map((record, index) => <Box component="pre" key={index}
      sx={{p: 1, mt: 1, bgcolor: 'background.default', whiteSpace: 'pre-wrap'}}>
      {JSON.stringify(record, null, 2)}
    </Box>)}
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
  return <Box aria-label={gettext('Vector results')}
    sx={{overflow: 'auto', mt: 1, flex: 1}}>
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
  const [working, setWorking] = useState(false);
  const target = graphs.find((item) => item.resource_id === targetId);

  const load = async () => {
    if (!target) return;
    setWorking(true); setError(null);
    try {
      setPage(await post({action: 'visual_admin_rows', request: {
        target_resource: target, limit: 200, filter: {},
      }}));
      setEdits({});
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
        onClick={() => deleteEntity('node', node)}>{gettext('Delete node')}</Button>
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
        {gettext('Delete relationship')}</Button>
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

function emptySemanticModel(resources) {
  const resource = (resources || []).find((item) =>
    ['table', 'collection', 'index'].includes(item.resource_kind)
  );
  const relation = resource?.display_path || [resource?.display_name || 'source'];
  return {
    contract_version: '1.0.0', name: gettext('New semantic model'),
    description: '', sources: resource ? [{
      id: 'source', resource_id: resource.resource_id,
      relation, alias: 'source',
    }] : [], joins: [], dimensions: [], measures: [], default_filters: [],
    materializations: [], security: {}, annotations: {},
  };
}

function SemanticModelWorkspace({semantic, resources, post, setError}) {
  const [items, setItems] = useState(semantic?.items || []);
  const [selectedId, setSelectedId] = useState('');
  const [record, setRecord] = useState(null);
  const [definition, setDefinition] = useState(emptySemanticModel(resources));
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
  const [query, setQuery] = useState({
    axes: {rows: [], columns: [], pages: []}, measures: [], filters: [],
    totals: false, limit: 500,
  });
  const [dimensionDraft, setDimensionDraft] = useState({
    id: 'dimension', name: 'Dimension', source_id: 'source', field: '',
    hierarchy: 'default', level: 'level',
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
    aggregation: 'sum', format: '',
  });
  const [calculationDraft, setCalculationDraft] = useState({
    id: 'calculation', name: 'Calculated measure', left: '', right: '',
    operator: 'divide', format: '',
  });
  const [joinDraft, setJoinDraft] = useState({
    left_source: '', right_source: '', left_field: '', right_field: '',
    join_type: 'inner',
  });
  const [filterDraft, setFilterDraft] = useState({
    source_id: 'source', field: '', operator: 'eq', value: '',
  });
  const [materializationDraft, setMaterializationDraft] = useState({
    id: 'rollup', name: 'Rollup', strategy: 'provider_managed', enabled: false,
  });
  const sourceResources = (resources || []).filter((item) =>
    ['table', 'collection', 'index'].includes(item.resource_kind)
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
      setRecord(null); setSelectedId(''); setDefinition(emptySemanticModel(resources));
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
  const compile = async (execute=false) => {
    setWorking(true); setError(null); setRendered(null);
    try {
      const value = await call(execute ? 'semantic_query_execute' :
        'semantic_query_compile', {
        model_id: record?.model_id, definition: record ? undefined : definition,
        query,
      });
      setCompiled(value.compiled);
      if (execute) {
        const id = value.occurrence.occurrence_id;
        setOccurrenceId(id);
        const response = await post({action: 'poll', occurrence_id: id});
        setRendered(response.rendered_result);
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
    }]}));
  };
  const addDimension = () => {
    const id = portableId(dimensionDraft.id);
    const level = portableId(`${id}_${dimensionDraft.level}`, `${id}_level`);
    setDefinition((current) => ({...current, dimensions: [...current.dimensions, {
      id, name: dimensionDraft.name,
      field: {source_id: dimensionDraft.source_id, field: dimensionDraft.field},
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
      format: measureDraft.format}],
  }));
  const addCalculation = () => setDefinition((current) => ({...current,
    measures: [...current.measures, {
      id: portableId(calculationDraft.id), name: calculationDraft.name,
      aggregation: 'none', field: null, format: calculationDraft.format,
      expression: {operator: calculationDraft.operator,
        left: {measure: calculationDraft.left},
        right: {measure: calculationDraft.right}},
    }],
  }));
  const addJoin = () => setDefinition((current) => ({...current,
    joins: [...current.joins, {id: `join_${current.joins.length + 1}`,
      left_source: joinDraft.left_source, right_source: joinDraft.right_source,
      join_type: joinDraft.join_type, predicates: [{operator: 'eq',
        left: {source_id: joinDraft.left_source, field: joinDraft.left_field},
        right: {source_id: joinDraft.right_source, field: joinDraft.right_field}}]}],
  }));
  const addFilter = () => {
    let value = filterDraft.value;
    try { value = JSON.parse(value); } catch { /* retain text member */ }
    setQuery((current) => ({...current, filters: [...current.filters, {
      field: {source_id: filterDraft.source_id, field: filterDraft.field},
      operator: filterDraft.operator, value,
    }]}));
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
        setDefinition(emptySemanticModel(resources));}}>{gettext('New')}</Button>
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
      <Tab value="dimensions" label={gettext('Dimensions & hierarchies')} />
      <Tab value="measures" label={gettext('Measures')} />
      <Tab value="query" label={gettext('Cube query')} />
      <Tab value="materializations" label={gettext('Materializations')} />
      <Tab value="lineage" label={gettext('Lineage')} />
      <Tab value="revisions" label={gettext('Revisions')} />
    </Tabs>
    {validation && <Alert severity={validation.valid ? 'success' : 'error'} sx={{mt: 1}}>
      {validation.valid ? gettext('Model is valid.') :
        (validation.errors || []).map((item) => item.message).join(' ')}</Alert>}
    {panel === 'model' && <Box sx={{display: 'grid', gap: 1, mt: 2}}>
      <TextField label={gettext('Model name')} value={definition.name}
        onChange={(event) => setDefinition({...definition, name: event.target.value})} />
      <TextField multiline minRows={2} label={gettext('Description')}
        value={definition.description} onChange={(event) =>
          setDefinition({...definition, description: event.target.value})} />
      <Box component="strong">{gettext('Data sources')}</Box>
      {(definition.sources || []).map((source, index) => <Box key={source.id}
        sx={{display: 'grid', gridTemplateColumns: '1fr 2fr 1fr auto', gap: 1}}>
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
        }]};
      })}>{gettext('Add source path manually')}</Button>
      {definition.sources.length > 1 && <>
        <Box component="strong">{gettext('Join designer')}</Box>
        <Box sx={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr auto', gap: 1}}>
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
          <Button onClick={addJoin}>{gettext('Add join')}</Button>
        </Box>
        {(definition.joins || []).map((join) => <Box key={join.id}>
          {join.left_source} {join.join_type} {join.right_source}</Box>)}
      </>}
    </Box>}
    {panel === 'dimensions' && <Box sx={{mt: 2}}>
      <Box sx={{display: 'grid', gridTemplateColumns: 'repeat(6, 1fr) auto', gap: 1}}>
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
        <Button onClick={addDimension} disabled={!dimensionDraft.field}>
          {gettext('Add')}</Button>
      </Box>
      {(definition.dimensions || []).map((dimension, index) => <Box key={dimension.id}
        sx={{p: 1, mt: 1, border: 1, borderColor: 'divider'}}>
        <Box component="strong">{dimension.name}</Box> — {dimension.field.source_id}.{dimension.field.field}
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
      <Box sx={{display: 'grid', gridTemplateColumns: 'repeat(6, 1fr) auto', gap: 1}}>
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
        {measure.name}: {measure.aggregation}({measure.field?.field || '*'}) {measure.format}
        <Button onClick={() => setDefinition({...definition, measures:
          definition.measures.filter((_item, position) => position !== index)})}>
          {gettext('Remove')}</Button></Box>)}
    </Box>}
    {panel === 'query' && <Box sx={{mt: 2}}>
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
        <Button onClick={addFilter} disabled={!filterDraft.field}>{gettext('Add slice')}</Button>
      </Box>
      {(query.filters || []).map((item, index) => <Box key={index} sx={{mt: 1}}>
        {item.field.source_id}.{item.field.field} {item.operator} {JSON.stringify(item.value)}
        <Button onClick={() => setQuery({...query, filters: query.filters.filter(
          (_value, position) => position !== index)})}>{gettext('Remove')}</Button></Box>)}
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
      {rendered && <ResultView rendered={rendered} />}
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

function ControlPlaneOperations({post, setError}) {
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

ControlPlaneOperations.propTypes = {
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
  const [parameterSource, setParameterSource] = useState('{}');
  const [sessionId, setSessionId] = useState(null);
  const [occurrenceId, setOccurrenceId] = useState(null);
  const [rendered, setRendered] = useState(null);
  const [transaction, setTransaction] = useState(null);
  const [selectedResource, setSelectedResource] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.get(endpointUrl).then((response) => {
      setWorkspace(response.data.data);
      if (response.data.data?.languages?.[0]?.language_profile === 'cypher') {
        setSource('MATCH (n) OPTIONAL MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 100');
      } else if (response.data.data?.languages?.[0]?.language_profile ===
        'redis-resp3-command') {
        setSource('PING');
      } else if (response.data.data?.languages?.[0]?.language_profile ===
        'opensearch-query-dsl') {
        setSource('{"query":{"match_all":{}},"size":100}');
      } else if (response.data.data?.languages?.[0]?.language_profile ===
        'milvus-query-search-api') {
        setSource('{"operation":"query","collection_name":"collection","filter":"","output_fields":["*"],"limit":100}');
      } else if (response.data.data?.languages?.[0]?.language_profile ===
        'influxdb3-sql-influxql') {
        setSource('SELECT * FROM measurement LIMIT 100');
      } else if (response.data.data?.languages?.[0]?.language_profile ===
        'opensearch-sql-ppl') {
        setSource('SELECT * FROM index_name LIMIT 100');
      }
      setBusy(false);
    }).catch((requestError) => {
      setError(errorMessage(requestError));
      setBusy(false);
    });
  }, [api, endpointUrl]);

  const post = useCallback((payload) => api.post(endpointUrl, payload)
    .then((response) => response.data.data), [api, endpointUrl]);

  const ensureSession = useCallback(async () => {
    if (sessionId) return sessionId;
    const language = workspace?.languages?.[0]?.language_profile;
    if (!language) throw new Error(gettext('No query language is available.'));
    const opened = await post({
      action: 'open_session', language_profile: language,
    });
    setSessionId(opened.session_id);
    return opened.session_id;
  }, [post, sessionId, workspace]);

  const poll = useCallback(async (id) => {
    setBusy(true);
    setError(null);
    try {
      const response = await post({action: 'poll', occurrence_id: id});
      setRendered(response.rendered_result);
      setOccurrenceId(response.occurrence?.operation?.terminal ? null : id);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }, [post]);

  const execute = async () => {
    setBusy(true);
    setError(null);
    setRendered(null);
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

  return (
    <ModalContent>
      <Tabs value={tab} onChange={(_event, value) => setTab(value)}>
        <Tab value="resources" label={gettext('Resource Explorer')} />
        <Tab value="studio" label={gettext('Data Studio')} />
        <Tab value="data" label={gettext('Edit Data')} />
        <Tab value="administration" label={gettext('Administration')} />
        <Tab value="operations" label={gettext('Operations')} />
        <Tab value="semantic" label={gettext('Cubes & Semantic Models')} />
        {workspace?.visual_admin?.model_family === 'document' &&
          <Tab value="streams" label={gettext('Change streams')} />}
      </Tabs>
      {error && <Alert severity="error">{error}</Alert>}
      {busy && !workspace && <Box p={3}><CircularProgress /></Box>}
      {workspace && tab === 'resources' &&
        <ResourceExplorer page={workspace.resource_page}
          selectedResourceId={selectedResource?.resource_id}
          onSelect={setSelectedResource} />}
      {workspace && tab === 'studio' && <Box sx={{p: 2, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0}}>
        <Box>{workspace.languages?.[0]?.title || gettext('Provider query language')}</Box>
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
          {busy && <CircularProgress size={24} />}
        </Box>
        {transaction && <Box component="pre" sx={{overflow: 'auto', maxHeight: 120}}>{JSON.stringify(transaction, null, 2)}</Box>}
        {rendered && <ResultView rendered={rendered} />}
      </Box>}
      {workspace && tab === 'administration' &&
        <VisualAdministration catalog={workspace.visual_admin}
          resources={workspace.resource_page?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'operations' &&
        <ControlPlaneOperations post={post} setError={setError} />}
      {workspace && tab === 'semantic' &&
        <SemanticModelWorkspace semantic={workspace.semantic_models}
          resources={workspace.resource_page?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'data' && workspace.visual_admin?.model_family === 'document' &&
        <DocumentDataGrid catalog={workspace.visual_admin}
          resources={workspace.resource_page?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'data' && workspace.visual_admin?.model_family === 'graph' &&
        <GraphDataStudio catalog={workspace.visual_admin}
          resources={workspace.resource_page?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'data' && workspace.visual_admin?.model_family ===
        'data-structure-key-value' &&
        <KeyValueDataGrid catalog={workspace.visual_admin}
          resources={workspace.resource_page?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'data' && [
        'time-series-analytic', 'vector-analytic', 'search-analytic',
      ].includes(workspace.visual_admin?.model_family) &&
        <AnalyticDataBrowser
          modelFamily={workspace.visual_admin.model_family}
          resources={workspace.resource_page?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'data' && ![
        'document', 'graph', 'data-structure-key-value',
        'time-series-analytic', 'vector-analytic', 'search-analytic',
      ].includes(
        workspace.visual_admin?.model_family
      ) &&
        <StructuredDataGrid catalog={workspace.visual_admin}
          resources={workspace.resource_page?.items || []}
          post={post} setError={setError} />}
      {workspace && tab === 'streams' && workspace.visual_admin?.model_family === 'document' &&
        <ChangeStreamViewer resources={workspace.resource_page?.items || []}
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
    'semantic', 'streams',
  ]),
};
