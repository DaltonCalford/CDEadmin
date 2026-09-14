"""Native attachment settings with explicit units and isolated control."""

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


TIMEOUT_SETTINGS = (
    ('statement_timeout_ms', 'SET STATEMENT TIMEOUT', 'MILLISECOND'),
    ('session_idle_timeout_seconds', 'SET SESSION IDLE TIMEOUT', 'SECOND'),
)


def initialize_timeouts(connection, route, module):
    statements = []
    for key, prefix, unit in TIMEOUT_SETTINGS:
        value = route.get(key)
        if value is None:
            continue
        if type(value) is not int or not 0 <= value <= 2147483647:
            raise RelationalClientError(
                f'Firebird {key} must be an integer from 0 through 2147483647')
        statements.append(f'{prefix} {value} {unit}')
    if not statements:
        return
    # SET affects the attachment immediately, independently of transaction
    # finality. Use a separate manager to avoid touching caller transactions.
    # Do not use setStatementTimeout: the 5.0.4 remote client emits unitless
    # SQL (seconds), despite the API documenting milliseconds.
    manager = connection.transaction_manager(
        default_action=module.DefaultAction.ROLLBACK)
    try:
        for statement in statements:
            manager.execute_immediate(statement)
    finally:
        try:
            if manager.is_active():
                manager.rollback()
        finally:
            manager.close()
