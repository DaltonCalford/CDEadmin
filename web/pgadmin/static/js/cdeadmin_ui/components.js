/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {Button, IconButton} from './primitives/Button';
import {NumberField, SecretField, TextArea, TextField} from './primitives/Field';
import {Checkbox, Select, Switch as ToggleSwitch} from './primitives/Choice';
import {Divider, Link} from './primitives/Layout';
import {
  ComboBox, DateField, DurationField, FilePickerButton, MultiSelect,
  PasswordStrength, Radio, SearchField, SegmentedControl, Slider, SplitButton,
  TimeField, UnitNumberField,
} from './primitives/AdvancedControls';
import {Dialog} from './overlays/Dialog';
import {
  AboutDialog, ColorPicker, CredentialsDialog, DestructiveConfirmationDialog,
  FontPicker, PreferencesSurface, SimpleInputDialog, UnsavedChangesDialog, Wizard,
} from './overlays/StandardSurfaces';
import {Menu} from './navigation/Menu';
import {
  AssetPicker, ColumnPicker, CommandPalette, ConnectionSelector,
  KeyboardShortcutHint, ListRow, Pagination, Popover, ResourcePicker,
  Tooltip, TreeRow,
} from './navigation/AdvancedNavigation';
import {Breadcrumbs, Tab} from './navigation/TabsAndBreadcrumbs';
import DataGrid from './data/DataGrid';
import CodeEditor from './editors/CodeEditor';
import GraphSurface from './visualization/GraphSurface';
import {EmptyState} from './feedback/EmptyState';
import {
  Badge, Banner, EnvironmentIndicator, ProgressBar, Skeleton, StatusDot,
  ValidationMessage,
} from './feedback/Indicators';
import {Toast} from './feedback/Toast';
import {
  Drawer, DropZone, FormSection, InspectorSection, Splitter, StatusBar,
  Toolbar, ToolboxItem,
} from './layout/WorkbenchChrome';

export const DESIGN_SYSTEM_IMPLEMENTATIONS = Object.freeze({
  AboutDialog, AssetPicker, Badge, Banner, Breadcrumbs, Button, Checkbox,
  CodeEditor, ColorPicker, ColumnPicker, ComboBox, CommandPalette,
  ConnectionSelector, DataGrid, DateField, Dialog, Divider, Drawer, DropZone,
  DurationField, EmptyState, EnvironmentIndicator, FilePickerButton, FontPicker,
  FormSection, IconButton, InspectorSection, KeyboardShortcutHint, Link, ListRow,
  Menu, MultiSelect, NumberField, Pagination, PasswordStrength, Popover,
  PreferencesSurface, ProgressBar, Radio, ResourcePicker, SearchField,
  SecretField, SegmentedControl, Select, Skeleton, Slider, SplitButton, Splitter,
  StatusBar, StatusDot, Tab, TextArea, TextField, TimeField, Toast, ToggleSwitch,
  Toolbar, ToolboxItem, Tooltip, TreeRow, UnitNumberField, ValidationMessage,
  Wizard,
});

export function designSystemComponent(name) {
  const component = DESIGN_SYSTEM_IMPLEMENTATIONS[name];
  if(!component) throw new TypeError(`Unknown design-system component: ${name}`);
  return component;
}

export {
  AboutDialog, AssetPicker, Badge, Banner, Breadcrumbs, Button, Checkbox,
  CodeEditor, ColorPicker, ColumnPicker, ComboBox, CommandPalette,
  ConnectionSelector, CredentialsDialog, DataGrid, DateField,
  DestructiveConfirmationDialog, Dialog, Divider, Drawer, DropZone,
  DurationField, EmptyState, EnvironmentIndicator, FilePickerButton, FontPicker,
  FormSection, GraphSurface, IconButton, InspectorSection, KeyboardShortcutHint,
  Link, ListRow,
  Menu, MultiSelect, NumberField, Pagination, PasswordStrength, Popover,
  PreferencesSurface, ProgressBar, Radio, ResourcePicker, SearchField,
  SecretField, SegmentedControl, Select, Skeleton, Slider, SplitButton, Splitter,
  SimpleInputDialog, StatusBar, StatusDot, Tab, TextArea, TextField, TimeField,
  Toast, ToggleSwitch, Toolbar, ToolboxItem, Tooltip, TreeRow,
  UnitNumberField, UnsavedChangesDialog, ValidationMessage, Wizard,
};
