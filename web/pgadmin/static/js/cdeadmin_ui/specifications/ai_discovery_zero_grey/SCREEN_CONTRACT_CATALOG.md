# Screen Contract Catalogue

Total screens: **64**.

Every JSON screen contract validates against the existing CDEadmin Zero-Grey `screen-spec.schema.json`.


## cdeadmin.ai_interface

- `cdeadmin_ai_interface_action_approval.screen.json` — `cdeadmin.ai_interface.action_approval` — Approve a revision-bound consequential action with explicit target and identity.
- `cdeadmin_ai_interface_agent_profile_editor.screen.json` — `cdeadmin.ai_interface.agent_profile_editor` — Edit model, connectors, tool/data/approval/budget/retention policies.
- `cdeadmin_ai_interface_agent_profiles.screen.json` — `cdeadmin.ai_interface.agent_profiles` — List and manage governed AI AgentProfiles.
- `cdeadmin_ai_interface_ai_admin.screen.json` — `cdeadmin.ai_interface.ai_admin` — Globally restrict AI, revoke writes/approvals, and invalidate connectors.
- `cdeadmin_ai_interface_ai_workbench.screen.json` — `cdeadmin.ai_interface.ai_workbench` — Primary conversation, planning, evidence and execution surface.
- `cdeadmin_ai_interface_approval_policy.screen.json` — `cdeadmin.ai_interface.approval_policy` — Map R0-R6 action classes to approvals and expiry.
- `cdeadmin_ai_interface_audit.screen.json` — `cdeadmin.ai_interface.audit` — Search and inspect sessions, tools, plans, approvals and backend outcomes.
- `cdeadmin_ai_interface_budget_policy.screen.json` — `cdeadmin.ai_interface.budget_policy` — Set exact model/tool/query/time/cost budgets.
- `cdeadmin_ai_interface_cdeadmin_tool_catalog.screen.json` — `cdeadmin.ai_interface.cdeadmin_tool_catalog` — Inspect every AI-exposed CDEadmin service/command and effective policy.
- `cdeadmin_ai_interface_connector_health.screen.json` — `cdeadmin.ai_interface.connector_health` — Monitor model/database/MCP/CDEadmin connector health dimensions.
- `cdeadmin_ai_interface_context_manager.screen.json` — `cdeadmin.ai_interface.context_manager` — See exactly what the model may receive and at what exposure level.
- `cdeadmin_ai_interface_data_egress.screen.json` — `cdeadmin.ai_interface.data_egress` — Control metadata/schema/stats/sample/content exposure to models.
- `cdeadmin_ai_interface_database_connector_wizard.screen.json` — `cdeadmin.ai_interface.database_connector_wizard` — Create an AI connection without inheriting the human interactive session.
- `cdeadmin_ai_interface_database_connectors.screen.json` — `cdeadmin.ai_interface.database_connectors` — Manage dedicated AI-owned database connections and principals.
- `cdeadmin_ai_interface_mcp_connectors.screen.json` — `cdeadmin.ai_interface.mcp_connectors` — Manage approved remote/local MCP services with versioned profiles.
- `cdeadmin_ai_interface_mcp_tool_browser.screen.json` — `cdeadmin.ai_interface.mcp_tool_browser` — Inspect discovered remote tools, schemas, risk and allow/deny status.
- `cdeadmin_ai_interface_model_provider_editor.screen.json` — `cdeadmin.ai_interface.model_provider_editor` — Configure model runtime, credential reference and data policy metadata.
- `cdeadmin_ai_interface_model_providers.screen.json` — `cdeadmin.ai_interface.model_providers` — Manage external/local model runtime profiles.
- `cdeadmin_ai_interface_plan_review.screen.json` — `cdeadmin.ai_interface.plan_review` — Review deterministic validated AI plan steps before live execution.
- `cdeadmin_ai_interface_query_review.screen.json` — `cdeadmin.ai_interface.query_review` — Review dialect, canonical resource resolution, budgets and result exposure.
- `cdeadmin_ai_interface_retention_policy.screen.json` — `cdeadmin.ai_interface.retention_policy` — Set conversation/tool/audit retention independently.
- `cdeadmin_ai_interface_run_monitor.screen.json` — `cdeadmin.ai_interface.run_monitor` — Monitor AI orchestration runs and continuing CDEadmin/database tasks.
- `cdeadmin_ai_interface_scratchbird_access.screen.json` — `cdeadmin.ai_interface.scratchbird_access` — Configure SBsql vs compatibility parser vs MCP with sandbox/cross-surface rules.
- `cdeadmin_ai_interface_session_history.screen.json` — `cdeadmin.ai_interface.session_history` — Browse, reopen, archive and inspect saved AI sessions.
- `cdeadmin_ai_interface_tool_policy.screen.json` — `cdeadmin.ai_interface.tool_policy` — Allow/deny CDEadmin commands by module, risk and profile.
- `cdeadmin_ai_interface_usage_cost.screen.json` — `cdeadmin.ai_interface.usage_cost` — Show model tokens, tool calls, query use and known/unknown cost.

## cdeadmin.discovery_intelligence

- `cdeadmin_discovery_intelligence_access_inbox.screen.json` — `cdeadmin.discovery_intelligence.access_inbox` — Review submitted requests and provisioning status.
- `cdeadmin_discovery_intelligence_access_request.screen.json` — `cdeadmin.discovery_intelligence.access_request` — Request explicit discover/schema/preview/query/export rights.
- `cdeadmin_discovery_intelligence_access_surfaces.screen.json` — `cdeadmin.discovery_intelligence.access_surfaces` — Show canonical ScratchBird object plus visible SBsql/compatibility surfaces without duplication.
- `cdeadmin_discovery_intelligence_advanced_search.screen.json` — `cdeadmin.discovery_intelligence.advanced_search` — Structured facets/boolean groups/ranking profile.
- `cdeadmin_discovery_intelligence_certification_review.screen.json` — `cdeadmin.discovery_intelligence.certification_review` — Bind certification decision to exact revision/evidence.
- `cdeadmin_discovery_intelligence_certifications.screen.json` — `cdeadmin.discovery_intelligence.certifications` — Review certification status/requests/expirations.
- `cdeadmin_discovery_intelligence_collections.screen.json` — `cdeadmin.discovery_intelligence.collections` — Organize refs to useful products/resources/terms without copying metadata.
- `cdeadmin_discovery_intelligence_curation_queue.screen.json` — `cdeadmin.discovery_intelligence.curation_queue` — Resolve missing owners/descriptions/terms, stale certification and quality issues.
- `cdeadmin_discovery_intelligence_data_360.screen.json` — `cdeadmin.discovery_intelligence.data_360` — Complete contextual view of one canonical data resource/asset.
- `cdeadmin_discovery_intelligence_discovery_admin.screen.json` — `cdeadmin.discovery_intelligence.discovery_admin` — Configure defaults, recommendation/usage privacy, system-surface visibility and backend.
- `cdeadmin_discovery_intelligence_discovery_home.screen.json` — `cdeadmin.discovery_intelligence.discovery_home` — Search/browse data, terms, products and recent/recommended governed assets.
- `cdeadmin_discovery_intelligence_domains.screen.json` — `cdeadmin.discovery_intelligence.domains` — Browse business domains and ownership.
- `cdeadmin_discovery_intelligence_duplicate_review.screen.json` — `cdeadmin.discovery_intelligence.duplicate_review` — Record equivalence/replacement without merging independent provider identities.
- `cdeadmin_discovery_intelligence_enrichment_review.screen.json` — `cdeadmin.discovery_intelligence.enrichment_review` — Review AI/imported suggestions before governed metadata changes.
- `cdeadmin_discovery_intelligence_field_360.screen.json` — `cdeadmin.discovery_intelligence.field_360` — Context for one field/property including native semantics and field lineage.
- `cdeadmin_discovery_intelligence_glossary.screen.json` — `cdeadmin.discovery_intelligence.glossary` — Browse governed terms, synonyms, acronyms and mappings.
- `cdeadmin_discovery_intelligence_index_health.screen.json` — `cdeadmin.discovery_intelligence.index_health` — Monitor revisions, queues, errors and source freshness.
- `cdeadmin_discovery_intelligence_index_sources.screen.json` — `cdeadmin.discovery_intelligence.index_sources` — Configure and inspect all index/enrichment sources.
- `cdeadmin_discovery_intelligence_marketplace.screen.json` — `cdeadmin.discovery_intelligence.marketplace` — Business-focused dense list of governed data products.
- `cdeadmin_discovery_intelligence_metric_360.screen.json` — `cdeadmin.discovery_intelligence.metric_360` — Business semantic identity, formula, implementations, quality and usage.
- `cdeadmin_discovery_intelligence_metric_editor.screen.json` — `cdeadmin.discovery_intelligence.metric_editor` — Author metric semantics independently of physical implementation.
- `cdeadmin_discovery_intelligence_product_detail.screen.json` — `cdeadmin.discovery_intelligence.product_detail` — Consume a curated product with members, trust, access and usage guidance.
- `cdeadmin_discovery_intelligence_product_editor.screen.json` — `cdeadmin.discovery_intelligence.product_editor` — Author governed product membership and release metadata.
- `cdeadmin_discovery_intelligence_profile_stats.screen.json` — `cdeadmin.discovery_intelligence.profile_stats` — View exact/estimated/sample/provider-reported statistics.
- `cdeadmin_discovery_intelligence_ranking_profiles.screen.json` — `cdeadmin.discovery_intelligence.ranking_profiles` — List draft/published/retired ranking configurations.
- `cdeadmin_discovery_intelligence_ranking_tuner.screen.json` — `cdeadmin.discovery_intelligence.ranking_tuner` — Edit exact weights/penalties and compare before/after results.
- `cdeadmin_discovery_intelligence_related_graph.screen.json` — `cdeadmin.discovery_intelligence.related_graph` — Explore related/lineage/business/usage connections from a canonical entity.
- `cdeadmin_discovery_intelligence_safe_preview.screen.json` — `cdeadmin.discovery_intelligence.safe_preview` — Bounded provider-authorized sample without bypassing row/column security.
- `cdeadmin_discovery_intelligence_saved_searches.screen.json` — `cdeadmin.discovery_intelligence.saved_searches` — Manage saved query/filter/ranking definitions.
- `cdeadmin_discovery_intelligence_search_analytics.screen.json` — `cdeadmin.discovery_intelligence.search_analytics` — Measure whether users find useful data and identify metadata gaps.
- `cdeadmin_discovery_intelligence_search_results.screen.json` — `cdeadmin.discovery_intelligence.search_results` — Ranked security-trimmed canonical results with explainable relevance.
- `cdeadmin_discovery_intelligence_search_to_analysis.screen.json` — `cdeadmin.discovery_intelligence.search_to_analysis` — Choose how to use selected assets: query, SBsql, BI, AI, ETL, dashboard.
- `cdeadmin_discovery_intelligence_semantic_index.screen.json` — `cdeadmin.discovery_intelligence.semantic_index` — Configure embedding backend/model/content/egress.
- `cdeadmin_discovery_intelligence_synonyms.screen.json` — `cdeadmin.discovery_intelligence.synonyms` — Govern synonyms/acronyms used by search expansion.
- `cdeadmin_discovery_intelligence_term_editor.screen.json` — `cdeadmin.discovery_intelligence.term_editor` — Edit definition, synonyms, domain and mappings.
- `cdeadmin_discovery_intelligence_usage_popularity.screen.json` — `cdeadmin.discovery_intelligence.usage_popularity` — Explain canonical usage and per-surface usage over time.
- `cdeadmin_discovery_intelligence_visibility_test.screen.json` — `cdeadmin.discovery_intelligence.visibility_test` — Reproduce security trimming for an authorized principal.
- `cdeadmin_discovery_intelligence_zero_result_terms.screen.json` — `cdeadmin.discovery_intelligence.zero_result_terms` — Curate searches that found nothing without leaking hidden data.