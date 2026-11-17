from codingagent.sandbox.runner import TestRunner


def _runner(dataset, settings, bitcount):
    return TestRunner(bitcount, dataset.support_files(), settings.sandbox)


def test_buggy_version_fails_official_tests(dataset, settings, bitcount):
    runner = _runner(dataset, settings, bitcount)
    report = runner.run({bitcount.entry_path: bitcount.buggy_code})
    # bitcount 的原始 bug 会死循环 → 超时；其他题可能是断言失败。两者都算“未通过”。
    assert not report.ok
    assert report.timed_out or report.failed >= 1


def test_fixed_version_passes_official_tests(dataset, settings, bitcount):
    runner = _runner(dataset, settings, bitcount)
    fixed = bitcount.buggy_code.replace("n ^= n - 1", "n &= n - 1")
    report = runner.run({bitcount.entry_path: fixed})
    assert report.ok, report.output_tail


def test_correct_version_passes_in_correct_mode(dataset, settings, bitcount):
    runner = TestRunner(
        bitcount, dataset.support_files(), settings.sandbox, correct_program=bitcount.correct_code
    )
    report = runner.run({bitcount.entry_path: bitcount.buggy_code}, use_correct=True)
    assert report.ok, report.output_tail


def test_syntax_error_reported_structured(dataset, settings, bitcount):
    runner = _runner(dataset, settings, bitcount)
    report = runner.run({bitcount.entry_path: "def bitcount(n):\n    return (\n"})
    assert not report.ok
    assert "SyntaxError" in report.output_tail or report.errors > 0
