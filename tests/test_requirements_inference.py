from helpers import load_fixture
from pm_mcp.domain.errors import IssueCode
from pm_mcp.ingestion.document_extraction import extract_document_content
from pm_mcp.ingestion.requirements_inference import split_questions, suggest_requirements_from_text


def outputs(s):
    return {q.label: list(q.outputs) for q in s.questions}


def test_exercise_document_questions_are_understood(exercise_docx):
    doc = extract_document_content(exercise_docx)
    s = suggest_requirements_from_text("\n".join(doc.text_blocks))
    assert [q.label for q in s.questions] == list("abcdefg")
    out = outputs(s)
    assert out["a"] == ["GANTT"]
    assert out["b"] == ["EARLY_LATE_TIMES", "SLACK"]
    assert set(out["c"]) == {"EXPECTED_DURATION", "VARIANCE", "CRITICAL_PATH"}
    assert out["d"] == out["e"] == out["f"] == ["PROBABILITY_QUERY"]
    assert out["g"] == ["PERCENTILE_DURATION"]  # trailing formula paragraphs are not part of question g
    queries = {q.id: q for q in s.probability_queries}
    assert (queries["d"].kind, queries["d"].upper_bound) == ("AT_MOST", 13)
    assert (queries["e"].kind, queries["e"].lower_bound) == ("AT_LEAST", 16)
    assert (queries["f"].kind, queries["f"].lower_bound, queries["f"].upper_bound) == ("BETWEEN", 16, 20)
    assert (queries["g"].kind, queries["g"].target_probability) == ("PERCENTILE_TO_DURATION", 0.98)
    assert all(q.provenance == "inferred" for q in s.probability_queries)
    assert s.time_unit.code == "week" and s.language == "es" and not s.warnings


def test_reference_pert_example_questions():
    s = suggest_requirements_from_text(load_fixture("pert_example_transcription.json")["question_text"])
    out = outputs(s)
    assert "NETWORK_DIAGRAM" in out["a"]
    assert out["b"] == ["ACTIVITY_ESTIMATES"]  # "… de probabilidad" is a modifier here, not a probability question
    assert not s.warnings
    assert out["c"] == ["EARLY_LATE_TIMES", "SLACK"]
    assert set(out["d"]) == {"CRITICAL_PATH", "EXPECTED_DURATION", "VARIANCE"}
    e = [(q.kind, q.lower_bound, q.upper_bound) for q in s.probability_queries if q.id.startswith("e")]
    assert e == [("AT_MOST", None, 27), ("AT_MOST", None, 25), ("AT_MOST", None, 23), ("AT_LEAST", 27, None), ("BETWEEN", 25, 27)]
    f = next(q for q in s.probability_queries if q.id == "f")
    assert (f.kind, f.target_probability) == ("PERCENTILE_TO_DURATION", 0.9)
    assert out["f"] == ["PERCENTILE_DURATION"]


def test_reference_gantt_problem_questions():
    s = suggest_requirements_from_text(load_fixture("crashing_reference_problems.json")["gantt_problem"]["question_text"])
    out = outputs(s)
    assert out["a"] == ["GANTT"] and out["b"] == ["NETWORK_DIAGRAM"] and out["c"] == ["EARLY_LATE_TIMES", "SLACK"]
    assert out["d"] == ["ACTIVITY_MAX_DELAY"] and s.delay_queries == ["D"]


def test_english_exercise():
    text = ("a) Draw the network diagram.\nb) What is the probability of completing the project within 30 days?\n"
            "c) How many days are needed for a 95% probability of completion?\nd) Find the critical path and the slack of each activity.")
    s = suggest_requirements_from_text(text)
    assert s.language == "en" and s.time_unit.code == "day"
    assert outputs(s) == {"a": ["NETWORK_DIAGRAM"], "b": ["PROBABILITY_QUERY"], "c": ["PERCENTILE_DURATION"],
                          "d": ["SLACK", "CRITICAL_PATH"]}
    assert [(q.kind, q.upper_bound, q.target_probability) for q in s.probability_queries] == [
        ("AT_MOST", 30, None), ("PERCENTILE_TO_DURATION", None, 0.95)]


def test_ambiguous_probability_phrase_warns():
    s = suggest_requirements_from_text("a) La probabilidad de terminar el proyecto en 13 semanas.")
    assert s.probability_queries[0].kind == "AT_MOST"
    assert s.warnings[0].code == IssueCode.AMBIGUOUS_QUERY and s.warnings[0].needs_user_clarification


def test_split_questions_requires_sequence():
    assert [label for label, _ in split_questions("Tiempo (Semanas) a) uno b) dos x) no d) tres")] == ["a", "b"]
    assert split_questions("Calcule la ruta crítica") == [("1", "Calcule la ruta crítica")]
