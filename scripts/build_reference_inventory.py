"""Regenerate src/pm_mcp/reference/inventory.json from the reference files.

Usage:  python scripts/build_reference_inventory.py [--exercise PATH.docx] [--concepts PATH.xlsx]
Defaults: the single .docx and the single .xlsx found in the repository root.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pm_mcp.ingestion.document_extraction import extract_document_content  # noqa: E402

TARGET = ROOT / "src" / "pm_mcp" / "reference" / "inventory.json"


def _single(pattern: str) -> Path:
    found = sorted(p for p in ROOT.glob(pattern) if not p.name.startswith("~$"))
    if len(found) != 1:
        raise SystemExit(f"expected exactly one {pattern} in {ROOT}, found {[p.name for p in found]}; pass it explicitly")
    return found[0]


def inventory_for(source_id: str, path: Path) -> dict:
    doc = extract_document_content(path)
    return {
        "source_id": source_id,
        "file_name": path.name,
        "sha256": doc.sha256,
        "format": doc.format,
        "sheets": doc.sheets,
        "text_blocks": doc.text_blocks if doc.format == "docx" else [],
        "cells": [c.model_dump() for c in doc.cells],
        "images": [
            {"key": f"{source_id}:{img.part}", "part": img.part, "location": img.location, "sha256": img.sha256,
             "width": img.width, "height": img.height}
            for img in doc.images
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exercise", type=Path)
    parser.add_argument("--concepts", type=Path)
    args = parser.parse_args()
    data = {
        "generated_by": "scripts/build_reference_inventory.py",
        "sources": [
            inventory_for("exercise_docx", args.exercise or _single("*.docx")),
            inventory_for("concepts_xlsx", args.concepts or _single("*.xlsx")),
        ],
    }
    TARGET.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {TARGET} ({sum(len(s['images']) for s in data['sources'])} image placements)")


if __name__ == "__main__":
    main()
