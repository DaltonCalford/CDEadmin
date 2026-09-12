/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useEffect, useMemo, useRef, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {Select} from '../../primitives/Choice';
import {EmptyState} from '../../feedback/EmptyState';
import {ProgressOverlay} from '../../feedback/ProgressOverlay';
import {StatusBadge} from '../../status/StatusBadge';
import {loadDDNLibraries} from './library';
import {validateDDNFiles, validateDDNSnapshot} from './contracts';

function firstView(workspace, requestedEntry, requestedView) {
  const entries = workspace.entries().filter((entry) => entry.views.length);
  if(!entries.length) throw new Error('The DDN workspace has no displayable views.');
  const entry = entries.find((item) => item.file === requestedEntry) ?? entries[0];
  const view = entry.views.find((item) => item.id === requestedView) ?? entry.views[0];
  return {entry: entry.file, view: view.id};
}

export function DDNViewerSurface({files, snapshot, entry, view,
  presentation=false, executeCommand, onExport, onReady, onError}) {
  const hostRef = useRef(null);
  const mountedRef = useRef(null);
  const workspaceRef = useRef(null);
  const [libraries, setLibraries] = useState(null);
  const [selection, setSelection] = useState({entry: entry ?? '', view: view ?? ''});
  const [entries, setEntries] = useState([]);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const sourceFiles = useMemo(() => snapshot ?
    validateDDNSnapshot(snapshot).files : validateDDNFiles(files),
  [files, snapshot]);

  useEffect(() => {
    let active = true;
    loadDDNLibraries().then((loaded) => {
      if(active) setLibraries(loaded);
    }).catch((loadError) => {
      if(!active) return;
      setError(loadError);
      setLoading(false);
      onError?.(loadError);
    });
    return () => { active = false; };
  }, [onError]);

  useEffect(() => {
    if(!libraries || !hostRef.current) return undefined;
    let active = true;
    setLoading(true);
    setError(null);
    const workspace = libraries.viewer.createWorkspace(sourceFiles);
    workspaceRef.current = workspace;
    const requested = snapshot ? validateDDNSnapshot(snapshot) : {entry, view};
    let chosen;
    try {
      chosen = firstView(workspace, requested.entry, requested.view);
      setEntries(workspace.entries());
      setSelection(chosen);
      const mounted = libraries.viewer.mount(hostRef.current, {
        workspace,
        ...chosen,
        overrides: snapshot?.overrides,
        layoutState: snapshot?.layoutState,
      });
      mountedRef.current = mounted;
      mounted.ready.then((rendered) => {
        if(!active || rendered?.superseded) return;
        setResult(rendered);
        setLoading(false);
        onReady?.(rendered);
      }).catch((renderError) => {
        if(!active) return;
        setError(renderError);
        setLoading(false);
        onError?.(renderError);
      });
    } catch(renderError) {
      setError(renderError);
      setLoading(false);
      onError?.(renderError);
    }
    return () => {
      active = false;
      mountedRef.current?.destroy();
      workspace.destroy();
      mountedRef.current = null;
      workspaceRef.current = null;
      if(hostRef.current) hostRef.current.replaceChildren();
    };
  }, [libraries, sourceFiles, entry, view, snapshot, onReady, onError]);

  const chooseView = async (value) => {
    const [nextEntry, nextView] = value.split('\u0000');
    setSelection({entry: nextEntry, view: nextView});
    setLoading(true);
    setError(null);
    try {
      mountedRef.current?.destroy();
      hostRef.current?.replaceChildren();
      const mounted = libraries.viewer.mount(hostRef.current, {
        workspace: workspaceRef.current,
        entry: nextEntry,
        view: nextView,
      });
      mountedRef.current = mounted;
      const rendered = await mounted.ready;
      if(!rendered?.superseded) {
        setResult(rendered);
        onReady?.(rendered);
      }
    } catch(renderError) {
      setError(renderError);
      onError?.(renderError);
    } finally {
      setLoading(false);
    }
  };

  const exportDiagram = () => {
    const svg = mountedRef.current?.exportSVG();
    if(svg) onExport?.(svg, selection);
  };
  const runCommand = (id, invoke, enabled=true) => {
    try {
      const result = executeCommand ? executeCommand(id, {invoke, enabled}) : invoke();
      Promise.resolve(result).catch((commandError) => onError?.(commandError));
    } catch(commandError) {
      onError?.(commandError);
    }
  };

  const options = entries.flatMap((item) => item.views.map((itemView) => ({
    value: `${item.file}\u0000${itemView.id}`,
    label: `${item.file} / ${itemView.name || itemView.id}`,
  })));
  const status = error ? 'error' : result?.diagnostics?.length ?
    'warning' : 'success';

  return <Box
    data-cde-surface="diagram.ddn.viewer"
    sx={{display: 'flex', flexDirection: 'column', height: '100%',
      minHeight: 0, bgcolor: 'background.default', color: 'text.primary'}}
  >
    {!presentation && <Box component="header" sx={{height: 'var(--cde-toolbar-height, 34px)',
      display: 'flex', alignItems: 'center', gap: 1, px: 1,
      borderBottom: '1px solid', borderColor: 'divider'}}>
      <Select
        aria-label="DDN view"
        size="small"
        value={selection.entry ? `${selection.entry}\u0000${selection.view}` : ''}
        options={options}
        onChange={chooseView}
        sx={{minWidth: 260}}
      />
      <Button onClick={() => runCommand('ddn.viewer.fit',
        () => mountedRef.current?.setOptions({page: 'content'}), Boolean(result))}
      disabled={!result}>
        Fit
      </Button>
      <Button onClick={() => runCommand('ddn.viewer.export', exportDiagram,
        Boolean(result))} disabled={!result}>Export SVG</Button>
      <Box sx={{ml: 'auto'}}>
        <StatusBadge live status={status} label={error ? 'Render failed' :
          `${result?.diagnostics?.length ?? 0} diagnostics`} />
      </Box>
    </Box>}
    <Box sx={{position: 'relative', flex: 1, minHeight: 0, overflow: 'auto'}}>
      {error && <EmptyState role="alert" message={error.message} />}
      <Box ref={hostRef} aria-label="DDN diagram" sx={{height: '100%',
        display: error ? 'none' : 'block'}} />
      <ProgressOverlay message={loading ? 'Rendering DDN view' : ''} />
    </Box>
  </Box>;
}

DDNViewerSurface.propTypes = {
  files: PropTypes.object,
  snapshot: PropTypes.object,
  entry: PropTypes.string,
  view: PropTypes.string,
  presentation: PropTypes.bool,
  executeCommand: PropTypes.func,
  onExport: PropTypes.func,
  onReady: PropTypes.func,
  onError: PropTypes.func,
};
