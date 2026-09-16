/////////////////////////////////////////////////////////////
// CDEadmin - Multi-engine Database Administration
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//////////////////////////////////////////////////////////////

import {useState} from 'react';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {VisualAdministration} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';

jest.mock('../../../pgadmin/static/js/api_instance');

describe('completed DROP task receipt', () => {
  it.each([[false, false], [false, true], [true, false], [true, true]])(
    'retains the explicitly labelled response after removal (object editor=%s, neighbour=%s)', async (objectEditor, neighbour) => {
      const original = {resource_id: 'table:OWNED', resource_kind: 'table', display_name: 'OWNED'};
      const other = {...original, resource_id: 'table:KEEP', display_name: 'KEEP'};
      const catalog = {objects: [{resource_kind: 'table', title: 'Table', operations: [{
        operation_id: 'drop', title: 'Drop', target_required: true,
        confirmation_required: true, form: {fields: []},
      }]}]};
      const response = {provider_result: {accepted: true, statement_results: [],
        commit_requested: true, driver_observation_only: true,
        transaction_finality_interpreted_by_common_code: false}};
      const post = jest.fn(async ({action}) => {
        if (action === 'resource_inspect') return original;
        if (action === 'visual_admin_validate') return {valid: true};
        if (action === 'visual_admin_plan') return {plan_id: 'owned-plan', plan_digest: 'owned-digest',
          state: 'ready', execution_available: true};
        return response;
      });
      const error = jest.fn();
      const refreshed = jest.fn();
      function Task({transport}) {
        const [removed, setRemoved] = useState(false);
        return <VisualAdministration post={transport} setError={error} catalog={catalog}
          resources={[...(removed ? [] : [original]), ...(neighbour ? [other] : [])]}
          selectedResource={removed ? null : original} resourceGeneration={removed ? 'g2' : 'g1'}
          initialResourceKind="table" initialOperationId="drop" objectEditor={objectEditor}
          focused={!objectEditor} onMutationApplied={() => { refreshed(); setRemoved(true); }} />;
      }
      const {rerender} = render(<Task transport={post} />);
      await waitFor(() => expect(screen.getByRole('button', {name: 'Validate and preview'})).toBeEnabled());
      await act(async () => { fireEvent.click(screen.getByRole('button', {name: 'Validate and preview'})); });
      fireEvent.click(screen.getByRole('checkbox', {name: 'I confirm this provider-planned operation.'}));
      await act(async () => { fireEvent.click(screen.getByRole('button', {name: 'Apply provider plan'})); });
      expect(refreshed).toHaveBeenCalledTimes(1);
      expect(error.mock.calls.filter(([value]) => value)).toHaveLength(0);
      expect(screen.getByLabelText('Provider operation result')).toHaveTextContent('"accepted": true');
      expect(screen.getByLabelText('Provider response target')).toHaveTextContent('OWNED');
      expect(screen.getByLabelText('Provider response target')).toHaveTextContent('table:OWNED');
      expect(screen.getByLabelText('Provider response target')).not.toHaveTextContent('KEEP');
      expect(screen.getByLabelText('Completed object task')).toHaveTextContent('not an editable object');
      expect(screen.queryByLabelText('Provider plan preview')).not.toBeInTheDocument();
      const apply = screen.queryByRole('button', {name: 'Apply provider plan'});
      if (apply) expect(apply).toBeDisabled();
      expect(post.mock.calls.filter(([body]) => body.action === 'visual_admin_apply')).toHaveLength(1);
      rerender(<Task transport={jest.fn()} />);
      expect(screen.queryByLabelText('Provider operation result')).not.toBeInTheDocument();
      expect(screen.queryByLabelText('Provider response target')).not.toBeInTheDocument();
    });
});
