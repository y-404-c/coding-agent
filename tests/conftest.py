from __future__ import annotations

import pytest

from codingagent.config import Settings
from codingagent.dataset import QuixBugsDataset
from codingagent.domain.models import Problem
from codingagent.sandbox.workspace import Workspace


@pytest.fixture(scope="session")
def settings() -> Settings:
    loaded = Settings.load()
    loaded.sandbox.timeout_s = 5.0  # 测试里缩短超时，避免死循环题目拖慢用例
    return loaded


@pytest.fixture(scope="session")
def dataset(settings: Settings) -> QuixBugsDataset:
    ds = QuixBugsDataset(
        settings.dataset.root,
        source_url=settings.dataset.source_url,
        auto_fetch=False,
    )
    if not ds.is_ready():
        pytest.skip("数据集未准备：先运行 `ca dataset fetch`")
    return ds


@pytest.fixture
def bitcount(dataset: QuixBugsDataset) -> Problem:
    return dataset.load("bitcount")


@pytest.fixture
def workspace(bitcount: Problem) -> Workspace:
    return Workspace({bitcount.entry_path: bitcount.buggy_code})
