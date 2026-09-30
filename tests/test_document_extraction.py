import json
from pathlib import Path

import pytest

from pm_mcp.domain.errors import IssueCode
from pm_mcp.ingestion.document_extraction import _tesseract_available, extract_document_content


def test_docx_text_tables_and_every_image(exercise_docx, tmp_path):
    doc = extract_document_content(exercise_docx, tmp_path)
    assert doc.format == "docx" and len(doc.sha256) == 64
    joined = "\n".join(doc.text_blocks)
    for label in "abcdefg":
        assert f"{label})" in joined
    parts = {img.part: img for img in doc.images}
    assert set(parts) == {f"word/media/image{n}.png" for n in range(1, 9)}
    assert (parts["word/media/image2.png"].width, parts["word/media/image2.png"].height) == (399, 161)
    assert parts["word/media/image1.png"].location == "referenced from word/numbering.xml"
    assert all(Path(img.saved_path).exists() for img in doc.images)
    assert "[IMAGE" in joined and len(doc.tables) == 1
    assert any("activity-on-arrow" in g for g in doc.guidance)


def test_xlsx_cells_and_image_placements(concepts_xlsx):
    doc = extract_document_content(concepts_xlsx)
    assert doc.sheets == ["Diagrama Gantt (2)", "Diagrama Gantt", "Red Actividad Nodo", "PERT Probabilístico", "CPM",
                          "Problema 2 CPM", "Hoja1"]
    assert len(doc.images) == 57 and len({img.part for img in doc.images}) == 53
    assert len([img for img in doc.images if img.part == "xl/media/image1.png"]) == 2
    assert not any(c.is_formula for c in doc.cells) and len(doc.cells) == 20
    b28 = next(c for c in doc.cells if (c.sheet, c.cell) == ("Diagrama Gantt (2)", "B28"))
    assert "ACTIVIDAD - NODO" in b28.value
    assert any("no live formulas" in g for g in doc.guidance)
    first_pert = next(img for img in doc.images if img.location.startswith("sheet 'PERT"))
    assert first_pert.part == "xl/media/image15.png" and first_pert.location == "sheet 'PERT Probabilístico' row 1 col 1"


def test_plain_formats_and_errors(tmp_path):
    text = tmp_path / "exercise.txt"
    text.write_text("Actividad A dura 3 días.\n\nb) Calcule la ruta crítica.", encoding="utf-8")
    assert extract_document_content(text).text_blocks == ["Actividad A dura 3 días.", "b) Calcule la ruta crítica."]
    data = tmp_path / "project.json"
    data.write_text(json.dumps({"activities": []}), encoding="utf-8")
    assert extract_document_content(data).json_content == {"activities": []}
    missing = extract_document_content(tmp_path / "nope.docx")
    assert missing.warnings[0].code == IssueCode.UNREADABLE_DOCUMENT
    fake = tmp_path / "broken.docx"
    fake.write_bytes(b"not a zip")
    assert extract_document_content(fake).warnings[0].code == IssueCode.UNREADABLE_DOCUMENT


@pytest.mark.skipif(_tesseract_available() is None, reason="tesseract executable not installed")
def test_optional_ocr_is_a_hint(exercise_docx):
    pytest.importorskip("pytesseract")
    doc = extract_document_content(exercise_docx, ocr=True)
    table = next(img for img in doc.images if img.part == "word/media/image3.png")
    assert "Optimista" in table.ocr_text
