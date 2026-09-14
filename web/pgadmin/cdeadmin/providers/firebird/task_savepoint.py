##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Firebird-native task boundaries inside a caller-owned transaction."""

import uuid


class NativeTaskSavepoint:
    """Never commits or rolls back the enclosing transaction."""

    def __init__(self, cursor):
        self.cursor = cursor
        # ASCII-only generated names need no quoting, including SQL dialect 1.
        self.name = 'CDEADMIN_TASK_' + uuid.uuid4().hex.upper()
        self.active = False

    def begin(self):
        if self.active:
            raise RuntimeError('The native task savepoint is already active')
        self.cursor.execute('SAVEPOINT ' + self.name)
        self.active = True

    def release(self):
        if self.active:
            self.cursor.execute('RELEASE SAVEPOINT ' + self.name + ' ONLY')
            self.active = False

    def rollback(self):
        if not self.active:
            return False
        self.cursor.execute('ROLLBACK TO SAVEPOINT ' + self.name)
        self.release()
        return True
