"""Firebird expression boundaries, not a substitute for native parsing."""

from ..sdk.relational import RelationalClientError


def index_expression(value, label):
    """Admit one balanced expression for a provider-owned parenthesized slot.

    Follow Firebird 5 lexer quoting/comment boundaries. Native prepare checks
    grammar, types, determinism and index restrictions. Preserve input text;
    the caller must append a newline before its closing parenthesis so a final
    line comment cannot swallow provider-owned syntax.
    """
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise RelationalClientError(f'{label} must contain expression text')
    value = value.strip()
    pos, stack, content = 0, [], False

    def invalid():
        raise RelationalClientError(
            f'{label} has an unsafe expression boundary')

    while pos < len(value):
        char = value[pos]
        if char.isspace():
            pos += 1
            continue
        if value.startswith('--', pos):
            pos += 2
            while pos < len(value) and value[pos] not in '\r\n':
                pos += 1
            continue
        if value.startswith('/*', pos):
            # Firebird 5 block comments end at the first */ (not nested).
            end = value.find('*/', pos + 2)
            if end < 0:
                invalid()
            pos = end + 2
            continue
        content = True
        if char in 'qQ' and value[pos:pos + 2].lower() == "q'":
            if pos + 2 >= len(value):
                invalid()
            start = value[pos + 2]
            end_char = {'{': '}', '(': ')', '[': ']', '<': '>'}.get(
                start, start)
            end = value.find(end_char + "'", pos + 3)
            if end < 0:
                invalid()
            pos = end + 2
            continue
        if char in '\'"':
            pos += 1
            while pos < len(value):
                if value[pos] == char:
                    pos += 1
                    if pos < len(value) and value[pos] == char:
                        pos += 1
                        continue
                    break
                pos += 1
            else:
                invalid()
            continue
        if char.isalnum() or char in '_$':
            pos += 1
            while pos < len(value) and (
                    value[pos].isalnum() or value[pos] in '_$'):
                pos += 1
            continue
        if char in '([':
            stack.append(char)
        elif char in ')]':
            if not stack or stack.pop() != {')': '(', ']': '['}[char]:
                invalid()
        elif char == ';':
            invalid()
        pos += 1
    if stack or not content:
        invalid()
    return value
