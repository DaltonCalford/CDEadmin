"""Application fetch bounds, not SQL rewrites or transaction decisions."""

from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


MAX_FETCH_ROWS = 1_000_000  # Application admission bound, not an engine limit.


def query_row_limit(request):
    policy = request.get('output_policy')
    if policy is None:
        return None
    if not isinstance(policy, Mapping):
        raise RelationalClientError('Firebird output policy must be an object')
    value = policy.get('max_rows')
    if value is None:
        return None  # Existing programmatic callers explicitly remain unbound.
    if type(value) is not int or not 1 <= value <= MAX_FETCH_ROWS:
        raise RelationalClientError(
            'Firebird maximum fetched rows must be an integer from '
            '1 to 1000000, or null for unbounded fetching')
    return value
