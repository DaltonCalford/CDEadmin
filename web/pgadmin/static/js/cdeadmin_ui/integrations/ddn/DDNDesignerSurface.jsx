/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useEffect, useRef, useState} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {Button} from '../../primitives/Button';
import {Select} from '../../primitives/Choice';
import {TextField} from '../../primitives/Field';
import {ProgressOverlay} from '../../feedback/ProgressOverlay';
import {StatusBadge} from '../../status/StatusBadge';
import {Dialog} from '../../overlays/Dialog';
import {DDNSessionController, DDN_PERSISTENCE_STATES} from './DDNSessionController';

const SAVE_LABELS = Object.freeze({
  [DDN_PERSISTENCE_STATES.CLEAN]: 'Saved',
  [DDN_PERSISTENCE_STATES.DIRTY]: 'Unsaved changes',
  [DDN_PERSISTENCE_STATES.SAVING]: 'Saving',
  [DDN_PERSISTENCE_STATES.SAVED_NEWER_EDITS]: 'Saved; newer changes remain',
  [DDN_PERSISTENCE_STATES.SAVE_FAILED]: 'Save failed',
  [DDN_PERSISTENCE_STATES.EXTERNAL_CONFLICT]: 'External conflict',
});

function saveStatus(state) {
  if(state === DDN_PERSISTENCE_STATES.CLEAN) return 'success';
  if(state === DDN_PERSISTENCE_STATES.SAVE_FAILED ||
      state === DDN_PERSISTENCE_STATES.EXTERNAL_CONFLICT) return 'error';
  return 'warning';
}

export function DDNDesignerSurface({files, snapshot, assetRef, readOnly=false,
  validation='strict', uiTheme='light', saveAsset, executeCommand, onChange, onExport,
  onReady, onError, onStateChange}) {
  const hostRef = useRef(null);
  const designerRef = useRef(null);
  const controllerRef = useRef(null);
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [createDraft, setCreateDraft] = useState(null);

  useEffect(() => {
    let active = true;
    let unsubscribe = null;
    const start = async () => {
      setLoading(true);
      setError(null);
      try {
        const controller = await DDNSessionController.create({
          files, snapshot, assetRef, readOnly, validation,
        });
        if(!active) {
          controller.destroy();
          return;
        }
        controllerRef.current = controller;
        setState(controller.state());
        onStateChange?.(controller.state());
        unsubscribe = controller.subscribe((nextState, event) => {
          setState(nextState);
          onStateChange?.(nextState);
          onChange?.(controller.snapshot(), event);
        });
        const mounted = controller.libraries.designer.mount(hostRef.current, {
          session: controller.session,
          uiTheme,
        });
        designerRef.current = mounted;
        await mounted.ready;
        if(active) {
          setLoading(false);
          const kinds = controller.libraries.designer.catalogue.kinds.map((kind) => ({
            id: kind.id, label: kind.label, group: kind.group,
            payload: {type: 'create', kind: kind.id, kindLabel: kind.label},
          }));
          onReady?.(controller, {
            items: kinds,
            insert: (payload) => {
              if(readOnly) return;
              const targets = controller.inspect().writeTargets;
              setCreateDraft({
                kind: payload.kind,
                kindLabel: payload.kindLabel ?? payload.kind,
                id: '', name: '', writeTo: targets[0]?.id ?? '', targets,
              });
            },
          });
        }
      } catch(startError) {
        if(!active) return;
        setError(startError);
        setLoading(false);
        onError?.(startError);
      }
    };
    start();
    return () => {
      active = false;
      unsubscribe?.();
      designerRef.current?.destroy();
      controllerRef.current?.destroy();
      designerRef.current = null;
      controllerRef.current = null;
      if(hostRef.current) hostRef.current.replaceChildren();
    };
  }, [files, snapshot, assetRef, readOnly, validation, uiTheme,
    onChange, onReady, onError]);

  const save = async () => {
    setError(null);
    try {
      await controllerRef.current.save(saveAsset);
      setState(controllerRef.current.state());
      onStateChange?.(controllerRef.current.state());
    } catch(saveError) {
      setError(saveError);
      setState(controllerRef.current.state());
      onStateChange?.(controllerRef.current.state());
      onError?.(saveError);
    }
  };

  const undo = () => {
    controllerRef.current.undo();
    setState(controllerRef.current.state());
    onStateChange?.(controllerRef.current.state());
  };
  const redo = () => {
    controllerRef.current.redo();
    setState(controllerRef.current.state());
    onStateChange?.(controllerRef.current.state());
  };
  const exportSVG = () => {
    const svg = controllerRef.current.exportSVG();
    onExport?.(svg, controllerRef.current.snapshot());
  };
  const runCommand = (id, invoke, enabled=true) => {
    try {
      const result = executeCommand ? executeCommand(id, {invoke, enabled}) : invoke();
      Promise.resolve(result).catch((commandError) => onError?.(commandError));
    } catch(commandError) {
      onError?.(commandError);
    }
  };
  const createDefinition = () => {
    const invoke = () => controllerRef.current.execute({
      type: 'create', kind: createDraft.kind, id: createDraft.id,
      name: createDraft.name || createDraft.id, writeTo: createDraft.writeTo,
    });
    try {
      const result = executeCommand ? executeCommand('ddn.designer.create', {
        invoke, enabled: Boolean(createDraft.id && createDraft.writeTo),
      }) : invoke();
      Promise.resolve(result).then(() => setCreateDraft(null)).catch((commandError) => {
        setError(commandError);
        onError?.(commandError);
      });
    } catch(commandError) {
      setError(commandError);
      onError?.(commandError);
    }
  };

  return <Box
    data-cde-surface="diagram.ddn.designer"
    sx={{display: 'flex', flexDirection: 'column', height: '100%',
      minHeight: 0, bgcolor: 'background.default', color: 'text.primary'}}
  >
    <Box component="header" aria-label="DDN Designer commands"
      sx={{minHeight: 'var(--cde-toolbar-height, 34px)', display: 'flex',
        alignItems: 'center', gap: 1, px: 1, borderBottom: '1px solid',
        borderColor: 'divider'}}>
      <Button onClick={() => runCommand('ddn.designer.save', save,
        !readOnly && Boolean(saveAsset) && Boolean(assetRef) &&
        state?.persistence !== DDN_PERSISTENCE_STATES.CLEAN)} intent="primary"
      loading={state?.persistence === DDN_PERSISTENCE_STATES.SAVING}
      disabled={readOnly || !saveAsset || !assetRef ||
          state?.persistence === DDN_PERSISTENCE_STATES.CLEAN}>
        Save
      </Button>
      <Button onClick={() => runCommand('ddn.designer.undo', undo,
        !readOnly && Boolean(state?.history.canUndo))}
      disabled={readOnly || !state?.history.canUndo}>
        Undo
      </Button>
      <Button onClick={() => runCommand('ddn.designer.redo', redo,
        !readOnly && Boolean(state?.history.canRedo))}
      disabled={readOnly || !state?.history.canRedo}>
        Redo
      </Button>
      <Button onClick={() => runCommand('ddn.designer.export', exportSVG,
        !loading && !error)} disabled={loading || Boolean(error)}>
        Export SVG
      </Button>
      <Box sx={{ml: 'auto'}}>
        <StatusBadge live status={saveStatus(state?.persistence)}
          label={SAVE_LABELS[state?.persistence] ?? 'Opening Designer'} />
      </Box>
    </Box>
    {error && <Box role="alert" sx={{p: 1, color: 'error.main',
      borderBottom: '1px solid', borderColor: 'error.main'}}>{error.message}</Box>}
    <Box sx={{position: 'relative', flex: 1, minHeight: 0, overflow: 'hidden'}}>
      <Box ref={hostRef} aria-label="DDN Designer" sx={{height: '100%'}} />
      <ProgressOverlay message={loading ? 'Opening DDN Designer' : ''} />
    </Box>
    <Dialog open={Boolean(createDraft)} title={createDraft ?
      `Create ${createDraft.kindLabel}` : 'Create DDN definition'}
    onClose={() => setCreateDraft(null)} actions={<>
      <Button onClick={() => setCreateDraft(null)}>Cancel</Button>
      <Button intent="primary" disabled={!createDraft?.id || !createDraft?.writeTo}
        onClick={createDefinition}>Create definition</Button>
    </>}>
      {createDraft && <Box sx={{display: 'grid', gap: 1}}>
        <TextField label="Definition identifier" value={createDraft.id}
          onChange={(event) => setCreateDraft((current) => ({
            ...current, id: event.target.value,
          }))} />
        <TextField label="Display name" value={createDraft.name}
          onChange={(event) => setCreateDraft((current) => ({
            ...current, name: event.target.value,
          }))} />
        <Select label="DDN data target" value={createDraft.writeTo}
          options={createDraft.targets.map((target) => ({
            value: target.id, label: `${target.name} — ${target.file}`,
          }))} onChange={(writeTo) => setCreateDraft((current) => ({
            ...current, writeTo,
          }))} />
      </Box>}
    </Dialog>
  </Box>;
}

DDNDesignerSurface.propTypes = {
  files: PropTypes.object,
  snapshot: PropTypes.object,
  assetRef: PropTypes.object,
  readOnly: PropTypes.bool,
  validation: PropTypes.oneOf(['draft', 'strict']),
  uiTheme: PropTypes.oneOf(['light', 'night']),
  saveAsset: PropTypes.func,
  executeCommand: PropTypes.func,
  onChange: PropTypes.func,
  onExport: PropTypes.func,
  onReady: PropTypes.func,
  onError: PropTypes.func,
  onStateChange: PropTypes.func,
};
