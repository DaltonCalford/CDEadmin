##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-driven visual administration contracts and catalog loader."""

from .catalog import (
    PORTFOLIO_ENGINE_IDS,
    VisualAdminCatalogError,
    catalog_for_engine,
    portfolio_summary,
)
from .provider import (
    ProviderVisualAdministration,
    VisualAdminAccessError,
    VisualAdminError,
    VisualAdminExecutionError,
    VisualAdminValidationError,
)
from .experience import EXPERIENCE_SCHEMA, enrich_engine_experience
from .requirements import (
    COVERAGE_SCHEMA, ENGINE_EXPERIENCE_FAMILIES,
    EXPERIENCE_REQUIREMENTS, concept_coverage_for_engine,
)
from .live_evidence import (
    LIVE_EVIDENCE_SCHEMA,
    LiveEvidenceError,
    apply_live_evidence,
    load_live_evidence,
)
from .control_plane import (
    CONTROL_PLANE_PERMISSIONS,
    ControlPlaneCatalog,
    ControlPlaneCatalogError,
    ControlPlaneOperation,
    field as control_plane_field,
)


__all__ = (
    'PORTFOLIO_ENGINE_IDS',
    'ProviderVisualAdministration',
    'VisualAdminAccessError',
    'VisualAdminCatalogError',
    'VisualAdminError',
    'VisualAdminExecutionError',
    'VisualAdminValidationError',
    'CONTROL_PLANE_PERMISSIONS',
    'ControlPlaneCatalog',
    'ControlPlaneCatalogError',
    'ControlPlaneOperation',
    'control_plane_field',
    'EXPERIENCE_SCHEMA',
    'COVERAGE_SCHEMA',
    'ENGINE_EXPERIENCE_FAMILIES',
    'EXPERIENCE_REQUIREMENTS',
    'LIVE_EVIDENCE_SCHEMA',
    'LiveEvidenceError',
    'apply_live_evidence',
    'load_live_evidence',
    'enrich_engine_experience',
    'concept_coverage_for_engine',
    'catalog_for_engine',
    'portfolio_summary',
)
