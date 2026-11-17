from codingagent.dataset import QuixBugsDataset


def test_dataset_has_forty_paired_problems(dataset):
    ids = dataset.problem_ids()
    assert len(ids) == 40
    assert "bitcount" in ids
    assert dataset.unpaired() == {"program_without_test": [], "test_without_program": []}


def test_load_problem_fields(dataset, bitcount):
    assert bitcount.function_name == "bitcount"
    assert bitcount.entry_path == "python_programs/bitcount.py"
    assert bitcount.test_path == "python_testcases/test_bitcount.py"
    assert "n ^= n - 1" in bitcount.buggy_code
    assert "n &= n - 1" in bitcount.correct_code
    assert "def test_bitcount" in bitcount.test_code
    assert bitcount.test_json is not None
    assert "Bitcount" in bitcount.description


def test_support_files(dataset):
    files = dataset.support_files()
    assert "load_testdata.py" in files
    assert "node.py" in files


def test_manifest_is_stable(dataset):
    manifest = dataset.manifest()
    assert manifest["count"] == 40
    assert len(manifest["digest"]) == 64
    assert manifest == dataset.manifest()


def test_unknown_problem_raises(dataset):
    try:
        dataset.load("does_not_exist")
    except Exception as exc:
        assert "未知题目" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当报错")


def test_dataset_root_is_configured(settings):
    ds = QuixBugsDataset(settings.dataset.root, auto_fetch=False)
    assert ds.is_ready()
