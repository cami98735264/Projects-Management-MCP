from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _single(pattern: str) -> Path | None:
    found = sorted(p for p in ROOT.glob(pattern) if not p.name.startswith("~$"))
    return found[0] if len(found) == 1 else None


@pytest.fixture(scope="session")
def exercise_docx() -> Path:
    path = _single("*.docx")
    if path is None:
        pytest.skip("reference exercise .docx not present in repository root")
    return path


@pytest.fixture(scope="session")
def concepts_xlsx() -> Path:
    path = _single("*.xlsx")
    if path is None:
        pytest.skip("reference concepts .xlsx not present in repository root")
    return path


@pytest.fixture
def output_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("PM_MCP_OUTPUT_DIR", str(tmp_path))
    return tmp_path
