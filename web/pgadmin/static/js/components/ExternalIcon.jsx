/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import QueryToolSvg from '../../assets/cdeadmin/controls/fonticon/query_tool.svg?svgr';
import ViewDataSvg from '../../assets/cdeadmin/controls/fonticon/view_data.svg?svgr';
import SaveDataSvg from '../../assets/cdeadmin/controls/fonticon/save_data_changes.svg?svgr';
import PasteSvg from '../../assets/cdeadmin/controls/content_paste.svg?svgr';
import FilterSvg from '../../assets/cdeadmin/controls/filter_alt_black.svg?svgr';
import ClearSvg from '../../assets/cdeadmin/controls/cleaning_services_black.svg?svgr';
import CommitSvg from '../../assets/cdeadmin/controls/fonticon/commit.svg?svgr';
import RollbackSvg from '../../assets/cdeadmin/controls/fonticon/rollback.svg?svgr';
import ConnectedSvg from '../../assets/cdeadmin/controls/fonticon/connected.svg?svgr';
import DisconnectedSvg from '../../assets/cdeadmin/controls/fonticon/disconnected.svg?svgr';
import RegexSvg from '../../assets/cdeadmin/controls/fonticon/regex.svg?svgr';
import FormatCaseSvg from '../../assets/cdeadmin/controls/fonticon/format_case.svg?svgr';
import PropTypes from 'prop-types';
import Expand from '../../assets/cdeadmin/controls/fonticon/open_in_full.svg?svgr';
import Collapse from '../../assets/cdeadmin/controls/fonticon/close_fullscreen.svg?svgr';
import AWS from '../../assets/cdeadmin/controls/aws.svg?svgr';
import Azure from '../../assets/cdeadmin/controls/azure.svg?svgr';
import SQLFileSvg from '../../assets/cdeadmin/controls/sql_file.svg?svgr';
import SQLQuerySvg from '../../assets/cdeadmin/controls/sql_query.svg?svgr';
import ExecuteQuerySvg from '../../assets/cdeadmin/controls/execute_query.svg?svgr';
import MagicSvg from '../../assets/cdeadmin/controls/magic.svg?svgr';
import MsAzure from '../../assets/cdeadmin/controls/ms_azure.svg?svgr';
import GoogleCloud from '../../assets/cdeadmin/controls/google-cloud-1.svg?svgr';
import RowFilterSvg from '../../assets/cdeadmin/controls/fonticon/row_filter.svg?svgr';
import SvgIcon from '@mui/material/SvgIcon';
import SchemaDiffSvg from '../../assets/cdeadmin/controls/fonticon/compare.svg?svgr';

export default function ExternalIcon({Icon, ...props}) {
  return <SvgIcon component={Icon} inheritViewBox {...props}/>;
}

ExternalIcon.propTypes = {
  Icon: PropTypes.elementType.isRequired,
};

export const QueryToolIcon = ({style})=><ExternalIcon Icon={QueryToolSvg} style={{height: '1rem', ...style}} data-label="QueryToolIcon" />;
QueryToolIcon.propTypes = {style: PropTypes.object};

export const ViewDataIcon = ({style})=><ExternalIcon Icon={ViewDataSvg} style={{height: '0.85rem', ...style}} data-label="ViewDataIcon" />;
ViewDataIcon.propTypes = {style: PropTypes.object};

export const SaveDataIcon = ({style})=><ExternalIcon Icon={SaveDataSvg} style={{height: '1rem', ...style}} data-label="SaveDataIcon" />;
SaveDataIcon.propTypes = {style: PropTypes.object};

export const PasteIcon = ({style})=><ExternalIcon Icon={PasteSvg} style={style} data-label="PasteIcon" />;
PasteIcon.propTypes = {style: PropTypes.object};

export const FilterIcon = ({style})=><ExternalIcon Icon={FilterSvg} style={style} data-label="FilterIcon" />;
FilterIcon.propTypes = {style: PropTypes.object};

export const CommitIcon = ({style})=><ExternalIcon Icon={CommitSvg} style={{height: '1.2rem', ...style}} data-label="CommitIcon" />;
CommitIcon.propTypes = {style: PropTypes.object};

export const RollbackIcon = ({style})=><ExternalIcon Icon={RollbackSvg} style={{height: '1.2rem', ...style}} data-label="RollbackIcon" />;
RollbackIcon.propTypes = {style: PropTypes.object};

export const ClearIcon = ({style})=><ExternalIcon Icon={ClearSvg} style={style} data-label="ClearIcon" />;
ClearIcon.propTypes = {style: PropTypes.object};

export const ConnectedIcon = ({style})=><ExternalIcon Icon={ConnectedSvg} style={{height: '1rem', ...style}} data-label="ConnectedIcon" />;
ConnectedIcon.propTypes = {style: PropTypes.object};

export const DisconnectedIcon = ({style})=><ExternalIcon Icon={DisconnectedSvg} style={{height: '1rem', ...style}} data-label="DisconnectedIcon" />;
DisconnectedIcon.propTypes = {style: PropTypes.object};

export const RegexIcon = ({style})=><ExternalIcon Icon={RegexSvg} style={style} data-label="RegexIcon" />;
RegexIcon.propTypes = {style: PropTypes.object};

export const FormatCaseIcon = ({style})=><ExternalIcon Icon={FormatCaseSvg} style={style} data-label="FormatCaseIcon" />;
FormatCaseIcon.propTypes = {style: PropTypes.object};

export const ExpandDialogIcon = ({style})=><ExternalIcon Icon={Expand} style={{height: '1.2rem', ...style}} data-label="ExpandDialogIcon" />;
ExpandDialogIcon.propTypes = {style: PropTypes.object};

export const MinimizeDialogIcon = ({style})=><ExternalIcon Icon={Collapse} style={{height: '1.4rem', ...style}} data-label="MinimizeDialogIcon" />;
MinimizeDialogIcon.propTypes = {style: PropTypes.object};

export const RowFilterIcon = ({style})=><ExternalIcon Icon={RowFilterSvg} style={{height: '1rem', ...style}} data-label="RowFilterIcon" />;
RowFilterIcon.propTypes = {style: PropTypes.object};

export const AWSIcon = ({style})=><ExternalIcon Icon={AWS} style={{height: '2.2rem',width: '3.2rem', ...style}} data-label="AWSIcon" />;
AWSIcon.propTypes = {style: PropTypes.object};

export const AzureIcon = ({style})=><ExternalIcon Icon={Azure} style={{height: '2.2rem', width: '3.2rem', ...style}} data-label="AzureIcon" />;
AzureIcon.propTypes = {style: PropTypes.object};

export const GoogleCloudIcon = ({style})=><ExternalIcon Icon={GoogleCloud} style={{height: '2.2rem', width: '3.2rem', ...style}} data-label="GoogleCloudIcon" />;
GoogleCloudIcon.propTypes = {style: PropTypes.object};

export const SQLFileIcon = ({style})=><ExternalIcon Icon={SQLFileSvg} style={{height: '1rem', ...style}} data-label="SQLFileIcon" />;
SQLFileIcon.propTypes = {style: PropTypes.object};

export const SQLQueryIcon = ({style})=><ExternalIcon Icon={SQLQuerySvg} style={{height: '2rem', ...style}} data-label="SQLQueryIcon" />;
SQLQueryIcon.propTypes = {style: PropTypes.object};

export const ExecuteQueryIcon = ({style})=><ExternalIcon Icon={ExecuteQuerySvg} style={style} data-label="ExecuteQueryIcon" />;
ExecuteQueryIcon.propTypes = {style: PropTypes.object};

export const MagicIcon = ({style})=><ExternalIcon Icon={MagicSvg} style={{height: '1rem', ...style}} data-label="MagicIcon" />;
MagicIcon.propTypes = {style: PropTypes.object};

export const MSAzureIcon = ({style})=><ExternalIcon Icon={MsAzure} style={{height: '6rem', width: '7rem', ...style}} data-label="MSAzureIcon" />;
MSAzureIcon.propTypes = {style: PropTypes.object};

export const SchemaDiffIcon = ({style})=><ExternalIcon Icon={SchemaDiffSvg} style={{height: '2rem', ...style}} data-label="SchemaDiffIcon" />;
SchemaDiffIcon.propTypes = {style: PropTypes.object};
