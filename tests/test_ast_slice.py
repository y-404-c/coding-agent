from codingagent.context.slicing import (
    function_source,
    identifier_tokens,
    module_docstring,
    slice_function,
    top_level_symbols,
)

SOURCE = '''
import math

LIMIT = 3


def helper(x):
    return x * 2


def target(n):
    return helper(n) + LIMIT + math.floor(1.5)


def unrelated():
    return 42
'''


def test_slice_keeps_dependencies_and_drops_unrelated():
    result = slice_function(SOURCE, "target")
    assert result.found
    assert "def target" in result.code
    assert "def helper" in result.code
    assert "LIMIT" in result.code
    assert "import math" in result.code
    assert "def unrelated" not in result.code
    assert set(result.symbols) == {"target", "helper"}


def test_slice_missing_symbol_returns_top_level():
    result = slice_function(SOURCE, "nope")
    assert not result.found
    assert result.top_level == ["helper", "target", "unrelated"]


def test_function_source_only_target():
    code = function_source(SOURCE, "target")
    assert code and "def target" in code and "def helper" not in code


def test_real_problem_slice(bitcount):
    result = slice_function(bitcount.buggy_code, "bitcount")
    assert result.found
    assert "def bitcount" in result.code
    assert result.span is not None


def test_docstring_and_symbols(bitcount):
    assert "Bitcount" in module_docstring(bitcount.buggy_code)
    assert top_level_symbols(bitcount.buggy_code) == ["bitcount"]


def test_identifier_tokens_lowercase():
    tokens = identifier_tokens("def Foo(a):\n    return Bar(a)")
    assert {"foo", "bar", "a"} <= tokens
