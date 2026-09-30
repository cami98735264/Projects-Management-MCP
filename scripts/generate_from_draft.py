"""Generate a solution workbook from a saved ProjectDraft JSON, through the same service the MCP tool calls.

    python scripts/generate_from_draft.py examples/taller2_problema1_cpm/project_draft.json Taller2_Problema1_CPM.xlsx

The output path is resolved exactly as ``generate_solution_workbook`` resolves it (relative paths land in
``PM_MCP_OUTPUT_DIR``, else ./output).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pm_mcp.domain.models import ProjectDraft  # noqa: E402
from pm_mcp.service import generate_workbook  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    draft = ProjectDraft.model_validate(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
    result = generate_workbook(draft, argv[2], overwrite=True)
    print(f"ok={result.ok} path={result.path}")
    print(f"sheets={result.sheets}")
    report = result.workbook_validation
    if report is not None:
        print(f"formulas={report.formula_count} evaluated={report.evaluated_formula_count} "
              f"expected_checked={report.expected_values_checked} passed={report.passed}")
        for issue in report.issues[:10]:
            print(f"  ISSUE {issue.kind} {issue.sheet}!{issue.cell}: {issue.message}")
    for issue in result.issues:
        print(f"  {issue.severity}: {issue.message}")
    for note in result.notes:
        print(f"  note: {note}")
    if result.calculation_validation is not None:
        failed = [c for c in result.calculation_validation.checks if not c.passed]
        print(f"calculation checks: {len(result.calculation_validation.checks)} run, {len(failed)} failed")
        for check in failed:
            print(f"  FAILED {check.name}: {check.detail}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
