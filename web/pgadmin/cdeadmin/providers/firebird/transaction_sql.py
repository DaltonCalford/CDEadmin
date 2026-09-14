"""Recognize only complete native Firebird finality statements.

Firebird parse.y admits COMMIT/ROLLBACK [WORK] [RETAIN [SNAPSHOT]]. Savepoint
rollback and every other statement must still be parsed by the engine.
"""

import re


# Firebird dsql/chars.h marks TAB, LF, FF, CR and SPACE as whitespace.
# In particular VT is NOT whitespace; accepting it here could finalize work
# that the native parser would have rejected.
_COMMAND = re.compile(
    r'[ \t\r\n\f]*(COMMIT|ROLLBACK)'
    r'(?:[ \t\r\n\f]+WORK)?'
    r'(?:[ \t\r\n\f]+(RETAIN)'
    r'(?:[ \t\r\n\f]+SNAPSHOT)?)?'
    r'[ \t\r\n\f]*;?[ \t\r\n\f]*', re.IGNORECASE | re.ASCII)


def starts_transaction(source):
    """Recognize two leading keywords; Firebird parses every native option."""
    if not isinstance(source, str):
        return False
    index = 0
    for keyword in ('SET', 'TRANSACTION'):
        while index < len(source):
            if source[index] in ' \t\r\n\f':
                index += 1
            elif source.startswith('--', index):
                index += 2
                while index < len(source) and source[index] not in '\r\n':
                    index += 1
            elif source.startswith('/*', index):
                end = source.find('*/', index + 2)
                if end < 0:
                    return False
                index = end + 2
            else:
                break
        word = source[index:index + len(keyword)]
        if not word.isascii() or word.upper() != keyword:
            return False
        index += len(keyword)
        if index < len(source) and (
                source[index].isalnum() or source[index] in '_$'):
            return False
    return True


def start_native_transaction(connection, source):
    """Execute SET TRANSACTION with a null transaction input, as the API needs.

    Driver 1.10.11's Attachment.execute wrapper assumes a preexisting Python
    transaction and writes the returned pointer into it. Use that same native
    method with a null input and adopt its result in a new native wrapper.
    """
    from firebird.driver.interfaces import iTransaction

    attachment = connection._att
    encoded = source.encode(attachment.encoding)
    result = attachment.vtable.execute(
        attachment, attachment.status, None, len(encoded), encoded,
        connection.sql_dialect, None, None, None, None)
    attachment._check()
    if not result:
        raise RuntimeError('Firebird did not return a transaction interface')
    return iTransaction(result)


def transaction_command(source):
    """Return (action, retaining) without executing or repairing SQL."""
    if not isinstance(source, str):
        return None
    significant = []
    index = 0
    while index < len(source):
        if source.startswith('--', index):
            index += 2
            while index < len(source) and source[index] not in '\r\n':
                index += 1
            significant.append(' ')
        elif source.startswith('/*', index):
            # Firebird 5 comments are not nested (Parser::yylexSkipSpaces).
            end = source.find('*/', index + 2)
            if end < 0:
                return None
            index = end + 2
            significant.append(' ')
        elif source[index] in '\'"':
            return None  # No recognized finality command contains a literal.
        else:
            significant.append(source[index])
            index += 1
    matched = _COMMAND.fullmatch(''.join(significant))
    if matched is None:
        return None
    return matched[1].lower(), matched[2] is not None
