"""Inert ScratchBird adapter fixture: it cannot connect or execute."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from tools.cdeadmin_scratchbird_consumer import FixtureAdapterRefusal


MANIFEST = Path(__file__).with_name('handoff_manifest.json')


class FixtureScratchBirdAdapter:
    """Structurally complete adapter whose operations always fail closed."""

    def __init__(self):
        self.calls = []

    def adapter_manifest(self) -> Mapping[str, Any]:
        return copy.deepcopy(json.loads(MANIFEST.read_text(encoding='utf-8')))

    def _refuse(self, method: str, request: Mapping[str, Any]):
        self.calls.append((method, request.get('request_id')))
        raise FixtureAdapterRefusal(
            f'CDE_SB_FIXTURE_{method.upper()}_REFUSED',
            'non-production fixture has no driver or runtime binding',
        )

    def validate_configuration(self, request):
        return self._refuse('validate_configuration', request)

    def connect(self, request):
        return self._refuse('connect', request)

    def authenticate(self, request):
        return self._refuse('authenticate', request)

    def close_connection(self, request):
        return self._refuse('close_connection', request)

    def capabilities(self, request):
        return self._refuse('capabilities', request)

    def navigate(self, request):
        return self._refuse('navigate', request)

    def execute(self, request):
        return self._refuse('execute', request)

    def open_cursor(self, request):
        return self._refuse('open_cursor', request)

    def close_cursor(self, request):
        return self._refuse('close_cursor', request)

    def read_results(self, request):
        return self._refuse('read_results', request)

    def diagnostics(self, request):
        return self._refuse('diagnostics', request)

    def cancel(self, request):
        return self._refuse('cancel', request)

    def transaction_presentation(self, request):
        return self._refuse('transaction_presentation', request)
