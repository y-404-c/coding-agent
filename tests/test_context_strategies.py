from codingagent.context import build_context_builder
from codingagent.context.base import BuildContext
from codingagent.domain.enums import Strategy
from codingagent.sandbox.workspace import Workspace


def _build(dataset, bitcount, strategy):
    workspace = Workspace({bitcount.entry_path: bitcount.buggy_code})
    builder = build_context_builder(strategy, dataset.load_all())
    return builder.build(BuildContext(problem=bitcount, workspace=workspace))


def test_s0_contains_whole_file(dataset, bitcount):
    bundle = _build(dataset, bitcount, Strategy.S0)
    assert "def bitcount" in bundle.text
    assert "Bitcount" in bundle.text  # 模块 docstring 也在
    assert bundle.strategy == Strategy.S0


def test_s1_is_smaller_slice(dataset, bitcount):
    s0 = _build(dataset, bitcount, Strategy.S0)
    s1 = _build(dataset, bitcount, Strategy.S1)
    assert "def bitcount" in s1.text
    assert "Bitcount" not in s1.text
    assert len(s1.text) < len(s0.text)
    assert s1.meta["slice_found"] is True


def test_s2_adds_official_tests(dataset, bitcount):
    s1 = _build(dataset, bitcount, Strategy.S1)
    s2 = _build(dataset, bitcount, Strategy.S2)
    assert "def test_bitcount" in s2.text
    assert "def test_bitcount" not in s1.text
    assert len(s2.text) > len(s1.text)


def test_s3_adds_fewshot_without_leakage(dataset, bitcount):
    s3 = _build(dataset, bitcount, Strategy.S3)
    assert "few-shot" in s3.text
    assert "bitcount" not in s3.meta["fewshot_ids"]
    assert s3.meta["fewshot_ids"]


def test_s4_same_context_as_s3(dataset, bitcount):
    s3 = _build(dataset, bitcount, Strategy.S3)
    s4 = _build(dataset, bitcount, Strategy.S4)
    assert s3.text == s4.text
    assert s4.strategy == Strategy.S4


def test_feedback_section_appended(dataset, bitcount):
    workspace = Workspace({bitcount.entry_path: bitcount.buggy_code})
    builder = build_context_builder(Strategy.S4, dataset.load_all())
    bundle = builder.build(
        BuildContext(
            problem=bitcount,
            workspace=workspace,
            feedback_text='{"error_category": "assertion_failure"}',
        )
    )
    assert "上一次尝试的反馈" in bundle.sections
    assert "assertion_failure" in bundle.text


def test_context_budget_truncates(dataset, bitcount):
    workspace = Workspace({bitcount.entry_path: bitcount.buggy_code})
    builder = build_context_builder(Strategy.S3, dataset.load_all())
    bundle = builder.build(BuildContext(problem=bitcount, workspace=workspace), max_chars=500)
    assert len(bundle.text) <= 500 + 100
    assert "已截断" in bundle.text
