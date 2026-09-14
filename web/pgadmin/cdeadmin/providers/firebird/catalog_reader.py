##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Distinguish empty native catalog results from failed observations."""

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .error_diagnostics import status_codes


class CatalogReader:
    def __init__(self, cursor, materialize):
        self.cursor = cursor
        self.materialize = materialize
        self.observations = []

    def rows(self, source, section, *, required=True):
        observation = {
            'section': section, 'required_for_structural_catalog': required,
            'visibility': 'current_attachment',
        }
        try:
            self.cursor.execute(source)
            rows = [tuple(self.materialize(value) for value in row)
                    for row in self.cursor.fetchall()]
        except Exception as error:
            codes = status_codes(error)
            message = f'Firebird {section} catalog observation failed'
            observation.update(available=False, row_count=None,
                               error_type=type(error).__name__,
                               native_status_codes=list(codes),
                               message=message)
            self.observations.append(observation)
            if required:
                suffix = ('; Firebird status codes: ' + ', '.join(
                    str(code) for code in codes)) if codes else ''
                raise RelationalClientError(message + suffix) from None
            return []
        observation.update(available=True, row_count=len(rows))
        self.observations.append(observation)
        return rows

    @property
    def warnings(self):
        return [item['message'] + '. Missing entries are unknown, not absent.'
                for item in self.observations if not item['available']]
