from codingagent.sandbox.patch import PatchError, apply_unified_diff
from codingagent.sandbox.workspace import Workspace


def _fixed(workspace: Workspace) -> Workspace:
    outcome = workspace.apply_edit(
        "python_programs/bitcount.py", "        n ^= n - 1", "        n &= n - 1"
    )
    assert outcome.ok
    return workspace


def test_edit_success_and_diff(workspace):
    fixed = _fixed(workspace)
    assert fixed.changed()
    assert fixed.substantive_change()
    diff = fixed.unified_diff()
    assert "--- a/python_programs/bitcount.py" in diff
    assert "+        n &= n - 1" in diff
    assert "-        n ^= n - 1" in diff


def test_edit_rejects_missing_fragment(workspace):
    outcome = workspace.apply_edit("python_programs/bitcount.py", "nope", "x")
    assert not outcome.ok
    assert "未在文件中找到" in outcome.message
    assert "def bitcount" in outcome.message  # 返回真实内容片段而不是抛异常


def test_edit_rejects_duplicate_fragment(workspace):
    workspace.write(
        "python_programs/bitcount.py",
        "def f():\n    x = 1\n    x = 1\n    return x\n",
    )
    outcome = workspace.apply_edit("python_programs/bitcount.py", "    x = 1", "    x = 2")
    assert not outcome.ok
    assert "2 处" in outcome.message


def test_edit_rejects_noop(workspace):
    outcome = workspace.apply_edit("python_programs/bitcount.py", "count = 0", "count = 0")
    assert not outcome.ok


def test_whitespace_only_change_not_substantive(workspace):
    workspace.write("python_programs/bitcount.py", workspace.read("python_programs/bitcount.py") + "\n\n")
    assert workspace.changed()
    assert not workspace.substantive_change()


def test_reset_restores_original(workspace):
    _fixed(workspace)
    workspace.reset()
    assert not workspace.changed()


def test_patch_applies_to_pristine(workspace):
    fixed = _fixed(workspace)
    patched = apply_unified_diff(fixed.unified_diff(), workspace.originals)
    assert patched["python_programs/bitcount.py"] == fixed.read("python_programs/bitcount.py")


def test_patch_context_mismatch_raises(workspace):
    broken = (
        "--- a/python_programs/bitcount.py\n"
        "+++ b/python_programs/bitcount.py\n"
        "@@ -1,3 +1,3 @@\n-not the real content\n+replaced\n"
    )
    try:
        apply_unified_diff(broken, workspace.originals)
    except PatchError:
        pass
    else:  # pragma: no cover
        raise AssertionError("应当抛出 PatchError")
