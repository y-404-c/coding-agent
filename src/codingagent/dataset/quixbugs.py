from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

import httpx

from codingagent.context.slicing import module_docstring, top_level_symbols
from codingagent.domain.models import Problem

_SUBDIRS = ("python_programs", "correct_python_programs", "python_testcases", "json_testcases")
SUPPORT_FILE_NAMES = ("load_testdata.py", "node.py")


class DatasetError(RuntimeError):
    pass


class QuixBugsDataset:
    """QuixBugs（Python 版）加载器：40 题配对、官方测试、正确版本。

    只读本地目录；缺失时可从官方仓库拉取（zip，无需 git）。
    """

    def __init__(self, root: Path | str, source_url: str = "", auto_fetch: bool = True) -> None:
        self.root = Path(root)
        self.source_url = source_url
        self.auto_fetch = auto_fetch
        self._cache: dict[str, Problem] = {}

    # ------------------------------------------------------------------ 布局
    @property
    def programs_dir(self) -> Path:
        return self.root / "python_programs"

    @property
    def correct_dir(self) -> Path:
        return self.root / "correct_python_programs"

    @property
    def tests_dir(self) -> Path:
        return self.root / "python_testcases"

    @property
    def json_dir(self) -> Path:
        return self.root / "json_testcases"

    def is_ready(self) -> bool:
        return all(path.is_dir() for path in (self.programs_dir, self.correct_dir, self.tests_dir))

    def ensure(self) -> None:
        if self.is_ready():
            return
        if not self.auto_fetch:
            raise DatasetError(f"数据集不完整: {self.root}")
        self.fetch()

    # ------------------------------------------------------------------ 拉取
    def fetch(self, *, force: bool = False) -> None:
        if self.is_ready() and not force:
            return
        if not self.source_url:
            raise DatasetError("未配置 source_url，无法自动拉取数据集")
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="quixbugs-") as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "quixbugs.zip"
            self._download(archive)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(tmp_path / "extract")
            source_root = self._find_source_root(tmp_path / "extract")
            for name in _SUBDIRS:
                src = source_root / name
                if not src.is_dir():
                    raise DatasetError(f"压缩包缺少目录: {name}")
                dst = self.root / name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.move(str(src), str(dst))
            license_file = source_root / "LICENSE"
            if license_file.exists():
                shutil.copy2(license_file, self.root / "LICENSE")
        self.write_manifest()

    def _download(self, target: Path) -> None:
        with httpx.stream(
            "GET", self.source_url, follow_redirects=True, timeout=120.0
        ) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)

    @staticmethod
    def _find_source_root(extract_dir: Path) -> Path:
        candidates = [path for path in extract_dir.iterdir() if path.is_dir()]
        for candidate in candidates:
            if (candidate / "python_programs").is_dir():
                return candidate
        raise DatasetError("压缩包结构异常：找不到 python_programs")

    # ------------------------------------------------------------------ 题目
    def problem_ids(self) -> list[str]:
        self.ensure()
        programs = {
            path.stem
            for path in self.programs_dir.glob("*.py")
            if not path.stem.endswith("_test") and path.stem != "node"
        }
        tests = {
            path.stem[len("test_") :]
            for path in self.tests_dir.glob("test_*.py")
        }
        return sorted(programs & tests)

    def unpaired(self) -> dict[str, list[str]]:
        self.ensure()
        programs = {
            path.stem
            for path in self.programs_dir.glob("*.py")
            if not path.stem.endswith("_test") and path.stem != "node"
        }
        tests = {path.stem[len("test_") :] for path in self.tests_dir.glob("test_*.py")}
        return {
            "program_without_test": sorted(programs - tests),
            "test_without_program": sorted(tests - programs),
        }

    def load(self, problem_id: str) -> Problem:
        self.ensure()
        if problem_id in self._cache:
            return self._cache[problem_id]
        if problem_id not in self.problem_ids():
            raise DatasetError(f"未知题目: {problem_id}")

        buggy_path = self.programs_dir / f"{problem_id}.py"
        correct_path = self.correct_dir / f"{problem_id}.py"
        test_path = self.tests_dir / f"test_{problem_id}.py"
        if not correct_path.exists():
            raise DatasetError(f"缺少正确版本: {correct_path.name}")

        buggy_code = buggy_path.read_text(encoding="utf-8")
        json_path = self.json_dir / f"{problem_id}.json"
        function_name = self._function_name(buggy_code, problem_id)

        problem = Problem(
            problem_id=problem_id,
            function_name=function_name,
            entry_path=f"python_programs/{problem_id}.py",
            test_path=f"python_testcases/test_{problem_id}.py",
            buggy_code=buggy_code,
            correct_code=correct_path.read_text(encoding="utf-8"),
            test_code=test_path.read_text(encoding="utf-8"),
            test_json=json_path.read_text(encoding="utf-8") if json_path.exists() else None,
            description=module_docstring(buggy_code),
        )
        self._cache[problem_id] = problem
        return problem

    def load_all(self, problem_ids: Iterable[str] | None = None) -> list[Problem]:
        ids = list(problem_ids) if problem_ids is not None else self.problem_ids()
        return [self.load(problem_id) for problem_id in ids]

    def support_files(self) -> dict[str, str]:
        """测试辅助文件（load_testdata.py / node.py），沙箱会拷进 python_testcases/。"""

        self.ensure()
        files: dict[str, str] = {}
        for name in SUPPORT_FILE_NAMES:
            path = self.tests_dir / name
            if path.exists():
                files[name] = path.read_text(encoding="utf-8")
        if not files:
            raise DatasetError("缺少测试辅助文件 load_testdata.py / node.py")
        return files

    @staticmethod
    def _function_name(source: str, fallback: str) -> str:
        symbols = top_level_symbols(source)
        return symbols[0] if symbols else fallback

    # ------------------------------------------------------------------ 清单
    def manifest(self) -> dict:
        self.ensure()
        problems = {}
        for problem_id in self.problem_ids():
            problems[problem_id] = {
                "buggy": _sha256(self.programs_dir / f"{problem_id}.py"),
                "correct": _sha256(self.correct_dir / f"{problem_id}.py"),
                "test": _sha256(self.tests_dir / f"test_{problem_id}.py"),
                "json": _sha256_if_exists(self.json_dir / f"{problem_id}.json"),
            }
        digest = hashlib.sha256(
            json.dumps(problems, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {
            "source_url": self.source_url,
            "count": len(problems),
            "digest": digest,
            "problems": problems,
            "unpaired": self.unpaired(),
        }

    def write_manifest(self) -> Path:
        path = self.root / "manifest.json"
        path.write_text(
            json.dumps(self.manifest(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    def verify(self) -> dict:
        """与落盘的 manifest 比对，防止后续误改数据。"""

        self.ensure()
        path = self.root / "manifest.json"
        current = self.manifest()
        if not path.exists():
            self.write_manifest()
            return {"status": "created", "digest": current["digest"], "count": current["count"]}
        stored = json.loads(path.read_text(encoding="utf-8"))
        ok = stored.get("digest") == current["digest"] and stored.get("count") == current["count"]
        return {
            "status": "ok" if ok else "mismatch",
            "count": current["count"],
            "stored_digest": stored.get("digest"),
            "current_digest": current["digest"],
            "unpaired": current["unpaired"],
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_if_exists(path: Path) -> str | None:
    return _sha256(path) if path.exists() else None
