##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

from flask_babel import gettext


class AllPermissionTypes:
    object_register_server = 'object_register_server'
    tools_erd_tool = 'tools_erd_tool'
    tools_query_tool = 'tools_query_tool'
    tools_debugger = 'tools_debugger'
    tools_psql_tool = 'tools_psql_tool'
    tools_backup = 'tools_backup'
    tools_restore = 'tools_restore'
    tools_import_export_data = 'tools_import_export_data'
    tools_import_export_servers = 'tools_import_export_servers'
    tools_search_objects = 'tools_search_objects'
    tools_maintenance = 'tools_maintenance'
    tools_schema_diff = 'tools_schema_diff'
    tools_grant_wizard = 'tools_grant_wizard'
    tools_ai = 'tools_ai'
    storage_add_folder = 'storage_add_folder'
    storage_remove_folder = 'storage_remove_folder'
    change_password = 'change_password'
    project_asset_edit = 'project.asset.edit'
    schema_compare_view = 'schema_compare.view'
    schema_compare_edit_mapping = 'schema_compare.edit_mapping'
    schema_compare_generate_plan = 'schema_compare.generate_plan'
    schema_compare_apply = 'schema_compare.apply'
    schema_compare_export = 'schema_compare.export'
    schema_compare_admin = 'schema_compare.admin'
    lineage_view = 'lineage.view'
    lineage_scan = 'lineage.scan'
    lineage_curate = 'lineage.curate'
    lineage_export = 'lineage.export'
    lineage_admin = 'lineage.admin'
    quality_view = 'quality.view'
    quality_edit = 'quality.edit'
    quality_execute = 'quality.execute'
    quality_view_samples = 'quality.view_samples'
    quality_export_samples = 'quality.export_samples'
    quality_admin = 'quality.admin'
    contract_view = 'contract.view'
    contract_edit = 'contract.edit'
    contract_activate = 'contract.activate'
    contract_compliance = 'contract.compliance'
    contract_import_export = 'contract.import_export'
    contract_admin = 'contract.admin'
    etl_view = 'etl.view'
    etl_edit = 'etl.edit'
    etl_preview = 'etl.preview'
    etl_execute = 'etl.execute'
    etl_deploy = 'etl.deploy'
    etl_view_samples = 'etl.view_samples'
    etl_admin = 'etl.admin'
    cdc_view = 'cdc.view'
    cdc_edit = 'cdc.edit'
    cdc_execute = 'cdc.execute'
    cdc_replay = 'cdc.replay'
    cdc_view_payloads = 'cdc.view_payloads'
    cdc_admin = 'cdc.admin'
    replication_view = 'replication.view'
    replication_control = 'replication.control'
    replication_plan_failover = 'replication.plan_failover'
    replication_execute_failover = 'replication.execute_failover'
    replication_admin = 'replication.admin'
    trace_view = 'trace.view'
    trace_view_sensitive = 'trace.view_sensitive'
    trace_configure_source = 'trace.configure_source'
    trace_export = 'trace.export'
    trace_admin = 'trace.admin'
    migration_view = 'migration.view'
    migration_edit = 'migration.edit'
    migration_execute = 'migration.execute'
    migration_cutover = 'migration.cutover'
    migration_rollback = 'migration.rollback'
    migration_admin = 'migration.admin'

    @staticmethod
    def list():
        return filter(lambda x: not x.startswith('_'),
                      AllPermissionTypes.__dict__.keys())


class AllPermissionCategories:
    object_explorer = gettext('Object Explorer')
    tools = gettext('Tools')
    storage_manager = gettext('Storage Manager')
    miscellaneous = gettext('Miscellaneous')
    projects = gettext('Projects')
    schema_comparison = gettext('Schema Comparison')
    data_lineage = gettext('Data Lineage')
    data_quality = gettext('Data Quality')
    data_contracts = gettext('Data Contracts')
    etl_designer = gettext('ETL Designer')
    cdc_designer = gettext('CDC Designer')
    replication_topology = gettext('Replication Topology')
    distributed_tracing = gettext('Distributed Tracing')
    migration_planning = gettext('Migration Planning')


class PgAdminPermissions:
    _all_permissions = []

    def __init__(self):
        self.add_permission(
            AllPermissionCategories.object_explorer,
            AllPermissionTypes.object_register_server,
            gettext("Manage Server")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_query_tool,
            gettext("Query Tool")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_debugger,
            gettext("Debugger")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_psql_tool,
            gettext("PSQL Tool")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_backup,
            gettext("Backup Tool (including server and globals)")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_restore,
            gettext("Restore Tool")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_import_export_data,
            gettext("Import/Export Data")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_import_export_servers,
            gettext("Import/Export Servers")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_search_objects,
            gettext("Search Objects")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_maintenance,
            gettext("Maintenance")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_schema_diff,
            gettext("Schema Diff")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_grant_wizard,
            gettext("Grant Wizard")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_erd_tool,
            gettext("ERD Tool")
        )
        self.add_permission(
            AllPermissionCategories.tools,
            AllPermissionTypes.tools_ai,
            gettext("AI Reports")
        )
        self.add_permission(
            AllPermissionCategories.storage_manager,
            AllPermissionTypes.storage_add_folder,
            gettext("Add Folder")
        )
        self.add_permission(
            AllPermissionCategories.storage_manager,
            AllPermissionTypes.storage_remove_folder,
            gettext("Delete File/Folder")
        )
        self.add_permission(
            AllPermissionCategories.miscellaneous,
            AllPermissionTypes.change_password,
            gettext("Change Password")
        )
        self.add_permission(
            AllPermissionCategories.projects,
            AllPermissionTypes.project_asset_edit,
            gettext("Edit project assets")
        )
        for permission, label in (
            (AllPermissionTypes.schema_compare_view,
             gettext("View schema comparisons")),
            (AllPermissionTypes.schema_compare_edit_mapping,
             gettext("Edit schema comparison mappings")),
            (AllPermissionTypes.schema_compare_generate_plan,
             gettext("Generate and validate schema change plans")),
            (AllPermissionTypes.schema_compare_apply,
             gettext("Apply schema change plans")),
            (AllPermissionTypes.schema_compare_export,
             gettext("Export schema comparisons and plans")),
            (AllPermissionTypes.schema_compare_admin,
             gettext("Administer Schema Comparison")),
        ):
            self.add_permission(
                AllPermissionCategories.schema_comparison,
                permission,
                label,
            )
        for permission, label in (
            (AllPermissionTypes.replication_view,
             gettext("View replication topology and provider evidence")),
            (AllPermissionTypes.replication_control,
             gettext("Pause and resume provider replication links")),
            (AllPermissionTypes.replication_plan_failover,
             gettext("Create and validate failover plans")),
            (AllPermissionTypes.replication_execute_failover,
             gettext("Arm and execute validated failover plans")),
            (AllPermissionTypes.replication_admin,
             gettext("Administer replication provider integrations")),
        ):
            self.add_permission(
                AllPermissionCategories.replication_topology,
                permission,
                label,
            )
        for permission, label in (
            (AllPermissionTypes.trace_view,
             gettext("View distributed traces and provider evidence")),
            (AllPermissionTypes.trace_view_sensitive,
             gettext("View policy-authorized sensitive trace attributes")),
            (AllPermissionTypes.trace_configure_source,
             gettext("Configure trace ingestion sources")),
            (AllPermissionTypes.trace_export,
             gettext("Export distributed traces")),
            (AllPermissionTypes.trace_admin,
             gettext("Administer tracing sampling and retention policies")),
        ):
            self.add_permission(
                AllPermissionCategories.distributed_tracing,
                permission,
                label,
            )
        for permission, label in (
            (AllPermissionTypes.migration_view,
             gettext("View migration plans and runtime evidence")),
            (AllPermissionTypes.migration_edit,
             gettext("Create and edit migration plans and mappings")),
            (AllPermissionTypes.migration_execute,
             gettext("Assess, dry-run, copy and verify migrations")),
            (AllPermissionTypes.migration_cutover,
             gettext("Arm and execute migration cutovers")),
            (AllPermissionTypes.migration_rollback,
             gettext("Execute reviewed migration rollback plans")),
            (AllPermissionTypes.migration_admin,
             gettext("Administer migration provider integrations")),
        ):
            self.add_permission(
                AllPermissionCategories.migration_planning,
                permission,
                label,
            )
        for permission, label in (
            (AllPermissionTypes.lineage_view,
             gettext("View data lineage")),
            (AllPermissionTypes.lineage_scan,
             gettext("Scan and reconcile data lineage")),
            (AllPermissionTypes.lineage_curate,
             gettext("Curate data lineage")),
            (AllPermissionTypes.lineage_export,
             gettext("Export data lineage")),
            (AllPermissionTypes.lineage_admin,
             gettext("Administer Data Lineage")),
        ):
            self.add_permission(
                AllPermissionCategories.data_lineage,
                permission,
                label,
            )
        for permission, label in (
            (AllPermissionTypes.quality_view,
             gettext("View Data Quality assets and results")),
            (AllPermissionTypes.quality_edit,
             gettext("Edit Data Quality definitions")),
            (AllPermissionTypes.quality_execute,
             gettext("Execute Data Quality validations")),
            (AllPermissionTypes.quality_view_samples,
             gettext("View protected Data Quality violation samples")),
            (AllPermissionTypes.quality_export_samples,
             gettext("Export protected Data Quality violation samples")),
            (AllPermissionTypes.quality_admin,
             gettext("Administer Data Quality integrations")),
        ):
            self.add_permission(
                AllPermissionCategories.data_quality,
                permission,
                label,
            )
        for permission, label in (
            (AllPermissionTypes.contract_view,
             gettext("View Data Contract assets and compliance")),
            (AllPermissionTypes.contract_edit,
             gettext("Edit Data Contract definitions")),
            (AllPermissionTypes.contract_activate,
             gettext("Activate and change Data Contract lifecycle status")),
            (AllPermissionTypes.contract_compliance,
             gettext("Run Data Contract compliance")),
            (AllPermissionTypes.contract_import_export,
             gettext("Import and export Data Contracts")),
            (AllPermissionTypes.contract_admin,
             gettext("Administer Data Contract integrations")),
        ):
            self.add_permission(
                AllPermissionCategories.data_contracts,
                permission,
                label,
            )
        for permission, label in (
            (AllPermissionTypes.etl_view,
             gettext("View ETL pipelines and runtime summaries")),
            (AllPermissionTypes.etl_edit,
             gettext("Edit ETL pipeline definitions")),
            (AllPermissionTypes.etl_preview,
             gettext("Run bounded ETL previews")),
            (AllPermissionTypes.etl_execute,
             gettext("Execute and recover ETL pipeline runs")),
            (AllPermissionTypes.etl_deploy,
             gettext("Validate ETL deployment bindings and plans")),
            (AllPermissionTypes.etl_view_samples,
             gettext("View protected ETL preview samples")),
            (AllPermissionTypes.etl_admin,
             gettext("Administer ETL provider integrations")),
        ):
            self.add_permission(
                AllPermissionCategories.etl_designer,
                permission,
                label,
            )
        for permission, label in (
            (AllPermissionTypes.cdc_view,
             gettext("View CDC definitions and runtime summaries")),
            (AllPermissionTypes.cdc_edit,
             gettext("Edit CDC definitions and schema-change policies")),
            (AllPermissionTypes.cdc_execute,
             gettext("Start, pause, resume and stop CDC runs")),
            (AllPermissionTypes.cdc_replay,
             gettext("Prepare and execute bounded CDC replays")),
            (AllPermissionTypes.cdc_view_payloads,
             gettext("View protected CDC event payload samples")),
            (AllPermissionTypes.cdc_admin,
             gettext("Administer CDC provider integrations")),
        ):
            self.add_permission(
                AllPermissionCategories.cdc_designer,
                permission,
                label,
            )

    def add_permission(self, category: str, permission: str, label: str):
        self._all_permissions.append({
            "category": category,
            "name": permission,
            "label": label,
        })

    @property
    def all_permissions(self):
        return sorted(
            self._all_permissions,
            key=lambda x: (
                x['category'],
                x['label']))
