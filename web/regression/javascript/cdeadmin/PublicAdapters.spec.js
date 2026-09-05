/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

jest.mock('sources/components/JsonEditor', () => ({
  __esModule: true,
  default: function MockDocumentEditor() {
    return null;
  },
}));

import CodeEditor from 'sources/cdeadmin_ui/editors/CodeEditor';
import {DataTable, Table} from 'sources/cdeadmin_ui/data/DataTable';
import {
  DialogContent,
  DialogFooter,
} from 'sources/cdeadmin_ui/overlays/DialogLayout';
import DocumentEditor from 'sources/cdeadmin_ui/editors/DocumentEditor';
import {
  Menu,
  MenuDivider,
  MenuItem,
  SubMenu,
  useMenuGroup,
} from 'sources/cdeadmin_ui/navigation/Menu';
import TabPanel from 'sources/cdeadmin_ui/navigation/TabPanel';
import PgTable, {Table as LegacyTable} from 'sources/components/PgTable';
import LegacyCodeEditor from 'sources/components/ReactCodeMirror';
import LegacyDocumentEditor from 'sources/components/JsonEditor';
import {
  PgMenu,
  PgMenuDivider,
  PgMenuItem,
  PgSubMenu,
  usePgMenuGroup,
} from 'sources/components/Menu';
import LegacyTabPanel from 'sources/components/TabPanel';
import {
  ModalContent,
  ModalFooter,
} from 'sources/components/ModalContent';

describe('CDEadmin public compatibility adapters', () => {
  it('keeps data and editor renderers behind stable exports', () => {
    expect(DataTable).toBe(PgTable);
    expect(Table).toBe(LegacyTable);
    expect(CodeEditor).toBe(LegacyCodeEditor);
    expect(DocumentEditor).toBe(LegacyDocumentEditor);
  });

  it('keeps menu and tab implementations behind stable exports', () => {
    expect(Menu).toBe(PgMenu);
    expect(MenuItem).toBe(PgMenuItem);
    expect(MenuDivider).toBe(PgMenuDivider);
    expect(SubMenu).toBe(PgSubMenu);
    expect(useMenuGroup).toBe(usePgMenuGroup);
    expect(TabPanel).toBe(LegacyTabPanel);
  });

  it('keeps compatibility dialog layout behind stable exports', () => {
    expect(DialogContent).toBe(ModalContent);
    expect(DialogFooter).toBe(ModalFooter);
  });
});
