"""Firebird driver bindings are positional, not dictionary-based."""

from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def normalize_parameters(parameters):
    if parameters is None or (isinstance(parameters, Mapping) and
                              not parameters):
        return ()
    if not isinstance(parameters, (list, tuple)):
        raise RelationalClientError(
            'Firebird query parameters must be an ordered array matching '
            'the ? placeholders; named parameter objects are not supported')
    return tuple(parameters)
