"""Per-statement native dialect; never change the attachment's default."""
from collections.abc import Mapping
import weakref

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def requested_dialect(request):
    policy = request.get('output_policy')
    if policy is None:
        return None
    if not isinstance(policy, Mapping):
        raise RelationalClientError('Firebird output policy must be an object')
    value = policy.get('client_sql_dialect')
    if value is None:
        return None
    if type(value) is not int or value not in (1, 2, 3):
        raise RelationalClientError(
            'Firebird client SQL dialect must be integer 1, 2 or 3')
    return value


class DialectCursor:
    """Own a native prepared statement as well as its DB-API result cursor.

    firebird-driver exposes Statement execution but Connection._prepare uses
    an immutable attachment default. Prepare through IAttachment with the
    selected dialect, leaving that default and all other cursors untouched.
    """

    def __init__(self, connection, dialect):
        self.connection = connection
        self.dialect = dialect
        self.cursor = connection.cursor()
        # The driver also uses the cursor-local dialect for parameter range
        # diagnostics. Match preparation without touching connection state.
        self.cursor._dialect = dialect
        self.statement = None

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def execute(self, source, parameters=None):
        from firebird.driver.core import Statement

        transaction = self.connection.main_transaction
        if not transaction.is_active():
            transaction.begin()
        # Retain the partially initialized wrapper if metadata acquisition
        # fails, so the caller's guarded cleanup can release all native owners.
        statement = Statement.__new__(Statement)
        statement._in_meta = statement._out_meta = statement._istmt = None
        self.statement = statement
        # Match the driver's own prepared-statement ownership registration.
        # Explicit attachment cleanup must also see this external statement.
        self.connection._statements.append(weakref.ref(
            statement, self.connection._Connection__stmt_deleted))
        native = self.connection._att.prepare(
            transaction._tra, source, self.dialect)
        statement._istmt = native
        Statement.__init__(statement, self.connection, native, source,
                           self.dialect)
        self.cursor.execute(statement, parameters)
        return self

    def close(self):
        # A failed result close must not free the statement underneath it.
        # The provider quarantines the attachment and retains this owner.
        self.cursor.close()
        if self.statement is not None:
            self.statement.free()
            self.statement = None
