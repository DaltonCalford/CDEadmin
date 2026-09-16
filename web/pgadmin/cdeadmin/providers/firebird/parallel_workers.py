"""Initial attachment requests, not measurements of executing task workers."""

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def requested_workers(route):
    policy = route.get('parallel_workers_policy')
    if policy is None or policy == 'NATIVE_DEFAULT':
        return None
    if not isinstance(policy, str) or policy != 'CUSTOM':
        raise RelationalClientError(
            'Firebird parallel-worker policy is invalid')
    value = route.get('parallel_workers')
    if type(value) is not int or not 0 <= value <= 32767:
        raise RelationalClientError(
            'Firebird parallel workers must be an integer from 0 to 32767')
    return value
