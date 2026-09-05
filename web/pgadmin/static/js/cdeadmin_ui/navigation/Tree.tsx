/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {useTheme} from '@mui/material/styles';
import {FileTreeX, TreeModelX} from '../../components/PgTree';
import {IFileTreeXProps} from '../../components/PgTree/types';

export function Tree(props: IFileTreeXProps) {
  const theme = useTheme();
  return <FileTreeX
    {...props}
    itemHeight={theme.cdeadminPresentation?.treeRowHeight}
  />;
}

export {TreeModelX as TreeModel};
export type {
  IFileTreeXHandle as TreeHandle,
  IFileTreeXProps as TreeProps,
  IFileTreeXTriggerEvents as TreeTriggerEvents,
} from '../../components/PgTree/types';
