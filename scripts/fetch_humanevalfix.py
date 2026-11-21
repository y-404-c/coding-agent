"""下载 HumanEvalFix 数据源（bigcode/humanevalpack python fix 子集）。

数据源不在 git 中（data/raw/ 被 .gitignore），用本脚本重建：
  1. 从 hf-mirror 下载 parquet（HuggingFace 主站在部分网络环境被拦）
  2. 用 pyarrow 读 parquet 导出成 JSON

用法：
  python scripts/fetch_humanevalfix.py        # 用系统 python（需 pyarrow）
  .venv/Scripts/python.exe scripts/fetch_humanevalfix.py  # 若 venv 有 pyarrow
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "raw" / "humanevalfix"
PARQUET = OUT_DIR / "humanevalpack_python.parquet"
JSON = OUT_DIR / "humanevalpack_python.json"

URL = (
    "https://hf-mirror.com/datasets/bigcode/humanevalpack/resolve/main/"
    "python/test-00000-of-00001.parquet"
)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not PARQUET.exists():
        try:
            import httpx

            print(f"下载 {URL}")
            resp = httpx.get(URL, timeout=60, follow_redirects=True)
            resp.raise_for_status()
            PARQUET.write_bytes(resp.content)
            print(f"已保存 {PARQUET} ({len(resp.content)} bytes)")
        except Exception as exc:  # noqa: BLE001
            print(f"下载失败: {exc}", file=sys.stderr)
            print("可手动下载 parquet 放到上述路径后重跑本脚本。", file=sys.stderr)
            sys.exit(1)

    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("缺少 pyarrow：python -m pip install pyarrow", file=sys.stderr)
        sys.exit(1)

    table = pq.read_table(PARQUET)
    rows = table.to_pylist()
    JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"导出 {len(rows)} 题 → {JSON}")


if __name__ == "__main__":
    main()
