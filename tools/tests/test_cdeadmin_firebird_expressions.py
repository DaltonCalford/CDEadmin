"""Boundary checks must preserve native expressions, not guess SQL grammar."""

import sys
import unittest
from pathlib import Path
from types import ModuleType

WEB = Path(__file__).resolve().parents[2] / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.firebird_expressions import (  # noqa: E402
    index_expression,
)
from pgadmin.cdeadmin.sdk.relational import (  # noqa: E402
    RelationalClientError,
)


class FirebirdExpressionTests(unittest.TestCase):
    def test_native_tokens_preserved(self):
        for expression in (
                'UPPER("Odd$Column") || \';)/*\'',
                "'Robin''s'", '"a""b"',
                'RDB$GET_CONTEXT(\'USER_SESSION\', \'name\')',
                'ARR[1:2]', 'A != B', 'A !< B', 'A ^> B',
                'A SIMILAR TO \'[a-z]+\'',
                'CASE WHEN A IS NULL THEN 0 ELSE A END',
                'V /* ) ; nested-looking /* */ + 1',
                'ID > 0 -- ) ;\r\n AND ID < 100',
                'V -- trailing comment', "_UTF8 'é'", "X'01AB'",
                'ID ' + ' ' * 10000 + '+ 1'):
            with self.subTest(expression=expression[:100]):
                self.assertEqual(expression,
                                 index_expression(expression, 'test'))

    def test_alternative_quotes(self):
        for start, end in (('{', '}'), ('(', ')'), ('[', ']'), ('<', '>'),
                           ('!', '!'), ('/', '/')):
            for prefix in ('q', 'Q'):
                text = prefix + "'" + start + "Robin's ); -- /*" + end + "'"
                self.assertEqual(text, index_expression(text, 'test'))

    def test_boundary_attacks_and_incomplete_input_rejected(self):
        for text in (None, 1, '', ' ', '\x00', '-- only comment',
                     '/* only comment */', '(V', 'V)', 'ARR[1)',
                     "'unclosed", '"unclosed', "q'[unclosed'",
                     'V /* unclosed', 'V; DROP TABLE T',
                     'V) WHERE (ID > 0', 'V) --',
                     'V /* outer /* inner */ ; DROP TABLE T */',
                     "ABCq'[a'; DROP TABLE T", "V || 'ok';"):
            with self.subTest(text=text):
                with self.assertRaises(RelationalClientError):
                    index_expression(text, 'test')

    def test_native_semantics_are_not_invented(self):
        self.assertEqual('NONEXISTENT_FUNCTION(V)', index_expression(
            'NONEXISTENT_FUNCTION(V)', 'test'))


if __name__ == '__main__':
    unittest.main()
