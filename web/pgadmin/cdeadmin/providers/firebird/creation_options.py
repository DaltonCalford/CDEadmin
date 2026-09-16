"""Native Firebird creation settings that are not attachment preferences."""

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


MAX_SWEEP_INTERVAL = 2147483647


def creation_sweep_interval(options):
    value = options.get('sweep_interval')
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_SWEEP_INTERVAL:
        raise RelationalClientError(
            'Firebird sweep interval must be an integer from zero to '
            f'{MAX_SWEEP_INTERVAL}')
    return value
