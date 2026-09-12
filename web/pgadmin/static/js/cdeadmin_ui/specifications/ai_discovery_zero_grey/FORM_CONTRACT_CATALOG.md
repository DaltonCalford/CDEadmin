# Form Contract Catalogue

Total forms: **53**.

Every form declares exact zero-grey component types, defaults, validation, visibility, enablement, actions, states, keyboard behavior and security notes.


## cdeadmin.ai_interface

- `ai_agent_profile.form.json` — `ai.agent_profile` — Define the model, connector set, permissions and autonomy boundary for an AI agent.
- `ai_approval_policy.form.json` — `ai.approval_policy` — Map risk classes to approval and confirmation requirements.
- `ai_audit_export.form.json` — `ai.audit_export` — Export redacted audit metadata for a bounded scope.
- `ai_background_run.form.json` — `ai.background_run` — Start a bounded multi-step AI run without creating an unbounded autonomous agent.
- `ai_budget_policy.form.json` — `ai.budget_policy` — Bound model/tool/database/cost use.
- `ai_cdeadmin_capability_connector.form.json` — `ai.cdeadmin_capability_connector` — Expose selected CDEadmin services/commands to AI without screen automation.
- `ai_connector_test.form.json` — `ai.connector_test` — Run and display non-destructive connector validation.
- `ai_context_exposure.form.json` — `ai.context_exposure` — Choose an explicit resource/asset and exposure level.
- `ai_data_egress_policy.form.json` — `ai.data_egress_policy` — Control exactly what database/project information may be sent to a model provider.
- `ai_database_connector.form.json` — `ai.database_connector` — Create a dedicated AI-owned database connection profile.
- `ai_emergency_controls.form.json` — `ai.emergency_controls` — Immediately restrict AI access without deleting audit history.
- `ai_high_risk_approval.form.json` — `ai.high_risk_approval` — Confirm an R5/R6 action without allowing the model to self-authorize.
- `ai_instruction_asset.form.json` — `ai.instruction_asset` — Edit organization/project instructions supplied to a model without overriding hard policy.
- `ai_mcp_connector.form.json` — `ai.mcp_connector` — Configure an approved MCP server as an external AI tool connector.
- `ai_model_provider.form.json` — `ai.model_provider` — Configure a local/self-hosted/remote model provider without database authority.
- `ai_new_session.form.json` — `ai.new_session` — Start a bounded AI session with explicit profile and connectors.
- `ai_plan_review.form.json` — `ai.plan_review` — Review validated steps and required approvals before execution.
- `ai_principal_binding.form.json` — `ai.principal_binding` — Bind an Agent/Connector to a dedicated database/CDEadmin identity without impersonating the user.
- `ai_query_policy.form.json` — `ai.query_policy` — Define exact database operation limits for one or more AI connectors.
- `ai_query_review.form.json` — `ai.query_review` — Review a compiled query before model or user executes it.
- `ai_resource_scope.form.json` — `ai.resource_scope` — Constrain a connector/profile to explicitly allowed/blocked database resources.
- `ai_retention_policy.form.json` — `ai.retention_policy` — Control conversation, tool-result and audit retention independently.
- `ai_tool_policy.form.json` — `ai.tool_policy` — Choose which CDEadmin commands/services the AI can see, draft or execute.

## cdeadmin.discovery_intelligence

- `discovery_access_approval.form.json` — `discovery.access_approval` — Approve or deny a request with provider-grant preview.
- `discovery_access_request.form.json` — `discovery.access_request` — Request governed access without asking the user to find an administrator.
- `discovery_advanced_search.form.json` — `discovery.advanced_search` — Build explicit structured filters and boolean groups.
- `discovery_business_term.form.json` — `discovery.business_term` — Define a governed term and map it to data/metrics/assets.
- `discovery_certification_profile.form.json` — `discovery.certification_profile` — Define evidence required to certify a product/resource/metric.
- `discovery_certification_request.form.json` — `discovery.certification_request` — Submit a resource/product/metric for governed certification review.
- `discovery_certification_review.form.json` — `discovery.certification_review` — Review evidence and bind a certification decision to an exact revision.
- `discovery_collection.form.json` — `discovery.collection` — Create/edit a collection of references without copying metadata.
- `discovery_curation_resolution.form.json` — `discovery.curation_resolution` — Resolve a specific discovery curation issue with evidence.
- `discovery_data_product.form.json` — `discovery.data_product` — Create or edit a curated governed data product.
- `discovery_domain_editor.form.json` — `discovery.domain_editor` — Define a business/data domain used for discovery organization and context.
- `discovery_duplicate_review.form.json` — `discovery.duplicate_review` — Record equivalence/replacement relation without merging independent provider identities.
- `discovery_embedding_config.form.json` — `discovery.embedding_config` — Configure semantic retrieval without making generative AI a required dependency.
- `discovery_enrichment_review.form.json` — `discovery.enrichment_review` — Review AI/imported enrichment before it becomes governed metadata.
- `discovery_external_catalog_source.form.json` — `discovery.external_catalog_source` — Import metadata from an external catalog/BI platform as evidence, not canonical provider identity.
- `discovery_index_backend.form.json` — `discovery.index_backend` — Configure lexical/vector/facet index storage without binding DDI to one database engine.
- `discovery_index_source.form.json` — `discovery.index_source` — Configure metadata/asset/usage ingestion into DiscoveryIndexService.
- `discovery_metric.form.json` — `discovery.metric` — Define a business metric separately from any one physical query.
- `discovery_owner_steward.form.json` — `discovery.owner_steward` — Assign accountable business/technical roles to discoverable entities.
- `discovery_preview_policy.form.json` — `discovery.preview_policy` — Set bounded sample/profile behavior by classification/environment.
- `discovery_quick_search.form.json` — `discovery.quick_search` — Run governed enterprise discovery search.
- `discovery_ranking_profile.form.json` — `discovery.ranking_profile` — Configure exact ranking weights, boosts and penalties with before/after testing.
- `discovery_recommendation_policy.form.json` — `discovery.recommendation_policy` — Enable/disable explainable recommendation families and freshness limits.
- `discovery_saved_search.form.json` — `discovery.saved_search` — Persist query/filter/ranking settings, not result copies.
- `discovery_search_feedback.form.json` — `discovery.search_feedback` — Record explicit relevance/metadata feedback without directly mutating ranking or metadata.
- `discovery_synonyms.form.json` — `discovery.synonyms` — Manage governed search synonyms/acronyms without hidden ranking magic.
- `discovery_system_surface_settings.form.json` — `discovery.system_surface_settings` — Control technical visibility of compatibility catalogs without promoting them to business datasets.
- `discovery_usage_policy.form.json` — `discovery.usage_policy` — Control how usage signals contribute to ranking/recommendations without exposing individual behavior.
- `discovery_visibility_test.form.json` — `discovery.visibility_test` — Diagnose security-trimmed search for an authorized test principal.
- `discovery_zero_result_resolution.form.json` — `discovery.zero_result_resolution` — Convert a repeated no-result query into a governed synonym/term/data-gap action.