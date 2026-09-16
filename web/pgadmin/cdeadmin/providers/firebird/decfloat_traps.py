"""Initial attachment trap preferences, not stored database configuration."""

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


TRAP_FIELDS = {
    'trap_division_by_zero': 'DIVISION_BY_ZERO',
    'trap_inexact': 'INEXACT',
    'trap_invalid_operation': 'INVALID_OPERATION',
    'trap_overflow': 'OVERFLOW',
    'trap_underflow': 'UNDERFLOW',
}


def requested_traps(route):
    """A native empty DPB means default, not 'disable all traps'."""
    policy = route.get('decfloat_traps_policy')
    if policy is None or policy == 'NATIVE_DEFAULT':
        return None
    if not isinstance(policy, str) or policy != 'CUSTOM':
        raise RelationalClientError('Firebird DECFLOAT trap policy is invalid')
    selected = []
    for field, name in TRAP_FIELDS.items():
        value = route.get(field, False)
        if type(value) is not bool:
            raise RelationalClientError(
                'Firebird DECFLOAT trap selections must be true or false')
        if value:
            selected.append(name)
    if not selected:
        raise RelationalClientError(
            'Select at least one initial DECFLOAT trap. An empty attachment '
            'trap list uses native defaults; it cannot disable all traps.')
    return tuple(selected)
