##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Apply exact, provider-generated live object-operation evidence."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Mapping

from .requirements import EXPERIENCE_REQUIREMENTS


LIVE_EVIDENCE_SCHEMA = 'cdeadmin.provider-object-live-evidence.v1'


class LiveEvidenceError(ValueError):
    """A live-evidence artifact cannot be admitted safely."""


def _strings(value, label):
    if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value):
        raise LiveEvidenceError(f'{label} must be an array of strings')
    return list(value)


def load_live_evidence(path):
    """Load one immutable evidence document and bind it to its digest."""
    artifact = Path(path).resolve()
    try:
        raw = artifact.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise LiveEvidenceError(
            f'live evidence is unreadable ({type(exc).__name__})'
        ) from None
    if not isinstance(document, dict):
        raise LiveEvidenceError('live evidence must be an object')
    return document, {
        'path': str(artifact),
        'sha256': hashlib.sha256(raw).hexdigest(),
    }


def apply_live_evidence(catalog, evidence, artifact=None):
    """Merge only passed, exact-profile operation evidence into a catalog."""
    if not isinstance(catalog, Mapping) or not isinstance(evidence, Mapping):
        raise LiveEvidenceError('catalog and live evidence must be objects')
    engine_id = catalog.get('engine_id')
    if evidence.get('schema') != LIVE_EVIDENCE_SCHEMA:
        raise LiveEvidenceError('live evidence schema is unsupported')
    if evidence.get('engine_id') != engine_id:
        raise LiveEvidenceError('live evidence engine does not match catalog')
    if evidence.get('exact_profile') != catalog.get('reference_profile'):
        raise LiveEvidenceError(
            'live evidence profile does not match the reference profile'
        )
    concepts = evidence.get('concepts')
    if not isinstance(concepts, Mapping):
        raise LiveEvidenceError('live evidence concepts must be an object')

    value = copy.deepcopy(dict(catalog))
    declarations = value.get('concept_declarations')
    if not isinstance(declarations, dict):
        raise LiveEvidenceError('catalog has no concept declarations')
    artifact = dict(artifact or {})
    if artifact:
        if not isinstance(artifact.get('path'), str) or not isinstance(
                artifact.get('sha256'), str):
            raise LiveEvidenceError('artifact identity is incomplete')
        evidence_token = (
            f"live:{artifact['path']}#sha256={artifact['sha256']}"
        )
    else:
        run_id = evidence.get('run_id')
        if not isinstance(run_id, str) or not run_id:
            raise LiveEvidenceError(
                'in-memory live evidence requires a non-empty run_id'
            )
        evidence_token = f'live:run:{run_id}'

    for family_id, family_evidence in concepts.items():
        if family_id not in EXPERIENCE_REQUIREMENTS:
            raise LiveEvidenceError(
                f'unknown experience family in evidence: {family_id}'
            )
        if not isinstance(family_evidence, Mapping):
            raise LiveEvidenceError('family evidence must be an object')
        family_declarations = declarations.get(family_id)
        if not isinstance(family_declarations, dict):
            raise LiveEvidenceError(
                f'catalog does not declare evidence family: {family_id}'
            )
        for concept_id, result in family_evidence.items():
            if concept_id not in EXPERIENCE_REQUIREMENTS[family_id]:
                raise LiveEvidenceError(
                    f'unknown concept in evidence: {family_id}.{concept_id}'
                )
            declaration = family_declarations.get(concept_id)
            if not isinstance(declaration, dict):
                raise LiveEvidenceError(
                    f'catalog concept cannot receive live evidence: '
                    f'{family_id}.{concept_id}'
                )
            if not isinstance(result, Mapping) or result.get(
                    'status') != 'passed':
                raise LiveEvidenceError(
                    f'live concept is not passed: {family_id}.{concept_id}'
                )
            external_surface = declaration.get('external_surface')
            if declaration.get('external_surface_digest_required') is True:
                if evidence.get('surface_id') != external_surface:
                    raise LiveEvidenceError(
                        'live evidence surface does not match declaration'
                    )
                surface_sha256 = evidence.get('surface_sha256')
                if not isinstance(surface_sha256, str) or len(
                        surface_sha256) != 64 or any(
                            character not in '0123456789abcdef'
                            for character in surface_sha256.lower()):
                    raise LiveEvidenceError(
                        'external-surface evidence has no valid digest'
                    )
            operations = result.get('operations', {})
            if not isinstance(operations, Mapping):
                raise LiveEvidenceError('live operations must be an object')
            obligations = declaration.get('operation_obligations', {})
            if not isinstance(obligations, Mapping):
                obligations = {}
            admitted = {}
            for resource_kind, operation_ids in operations.items():
                operation_ids = _strings(
                    operation_ids,
                    f'{family_id}.{concept_id}.{resource_kind}',
                )
                unexpected = set(operation_ids).difference(
                    obligations.get(resource_kind, [])
                )
                if unexpected:
                    raise LiveEvidenceError(
                        'live evidence claims undeclared operations for '
                        f'{family_id}.{concept_id}.{resource_kind}'
                    )
                admitted[resource_kind] = sorted(set(operation_ids))
            current = declaration.setdefault('live_operations', {})
            for resource_kind, operation_ids in admitted.items():
                current[resource_kind] = sorted(set(
                    current.get(resource_kind, [])
                ).union(operation_ids))
            tokens = declaration.setdefault('evidence', [])
            token = evidence_token + f'#{family_id}/{concept_id}'
            if token not in tokens:
                tokens.append(token)
    return value


__all__ = (
    'LIVE_EVIDENCE_SCHEMA', 'LiveEvidenceError',
    'apply_live_evidence', 'load_live_evidence',
)
