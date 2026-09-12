/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  CommandError, CommandRegistry, MACRO_SCHEMA,
} from 'sources/cdeadmin_ui/commands/CommandRegistry';

describe('CDEadmin command registry', () => {
  it('resolves user customization without allowing it to bypass policy', () => {
    const registry = new CommandRegistry();
    registry.register({
      id: 'object.table.drop',
      label: 'Drop table',
      permission: 'object_drop',
      allowedSecurityGroups: ['database-admin'],
      enabledWhen: ({selection})=>selection?.canDrop,
      execute: jest.fn(),
    });
    const context = {
      currentUser: {
        permissions: ['object_drop'],
        security_groups: ['database-admin'],
      },
      selection: {canDrop: false},
      commandCustomizations: {
        'object.table.drop': {label: 'Remove table', enabled: true},
      },
    };

    expect(registry.resolve('object.table.drop', context)).toEqual(
      expect.objectContaining({label: 'Remove table', visible: true, enabled: false})
    );
  });

  it('rechecks permission when a command is invoked directly', async () => {
    const handler = jest.fn();
    const registry = new CommandRegistry();
    registry.register({
      id: 'connection.profile.edit',
      permission: 'profile_edit',
      execute: handler,
    });

    await expect(registry.execute('connection.profile.edit', {}, {
      currentUser: {permissions: []},
    })).rejects.toMatchObject({
      code: 'permission_denied',
      commandId: 'connection.profile.edit',
    });
    expect(handler).not.toHaveBeenCalled();
  });

  it('runs deterministic macro steps and rejects secret-bearing arguments', async () => {
    const handler = jest.fn(async ({visible})=>visible);
    const registry = new CommandRegistry();
    registry.register({
      id: 'connector.visibility.firebird.set',
      validateArguments: (args)=>typeof args.visible === 'boolean' || 'bad value',
      execute: handler,
    });

    await expect(registry.executeMacro({
      schema: MACRO_SCHEMA,
      steps: [{
        commandId: 'connector.visibility.firebird.set',
        arguments: {visible: true},
      }],
    })).resolves.toEqual([true]);
    await expect(registry.execute(
      'connector.visibility.firebird.set', {password: 'not-recordable'}
    )).rejects.toBeInstanceOf(CommandError);
  });

  it('does not expose commands to denied security groups', () => {
    const registry = new CommandRegistry();
    registry.register({
      id: 'security.certificate.rotate',
      deniedSecurityGroups: ['read-only'],
      execute: jest.fn(),
    });
    expect(registry.resolve('security.certificate.rotate', {
      currentUser: {security_groups: ['read-only']},
    }).visible).toBe(false);
  });

  it('normalizes the complete command policy and automation descriptor', () => {
    const registry = new CommandRegistry();
    registry.register({id: 'schema_compare.plan.apply', label: 'Apply',
      iconKey: 'tool.schema-compare', requiresConfirmation: true,
      confirmationIntent: 'review_then_explicit_confirm', macroCallable: false,
      aiEligible: false, auditCategory: 'schema_change',
      createsTask: 'schema_compare.plan.apply', disabledReason: 'Validate first.',
      enabledWhen: () => false, execute: jest.fn()});
    expect(registry.resolve('schema_compare.plan.apply')).toEqual(
      expect.objectContaining({version: 1, iconKey: 'tool.schema-compare',
        confirmationIntent: 'review_then_explicit_confirm', macroCallable: false,
        aiEligible: false, auditCategory: 'schema_change',
        createsTask: 'schema_compare.plan.apply', disabledReason: 'Validate first.'})
    );
  });
});
