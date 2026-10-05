"""Ambiguity and invalid-input handling (§8): nothing is fabricated; safe inferences are recorded."""

from helpers import act
from pm_mcp.domain.errors import IssueCode
from pm_mcp.domain.models import ProbabilityQuery, ProjectDraft, QuestionItem, TimeUnit
from pm_mcp.ingestion.normalize import normalize_project_input


def run(**kw):
    return normalize_project_input(ProjectDraft(**kw))


def codes(result):
    return {i.code for i in result.issues}


def test_missing_duration_is_asked_not_fabricated():
    result = run(activities=[act("A", [], 3), act("B", ["A"])])
    assert not result.ok and result.project is None
    issue = next(i for i in result.issues if i.code == IssueCode.METHOD_DATA_MISSING)
    assert issue.activity_ids == ["B"] and issue.needs_user_clarification
    assert result.clarification_questions


def test_mixed_duration_data_is_ambiguous():
    assert IssueCode.AMBIGUOUS_METHOD in codes(run(activities=[act("A", [], 3), act("B", ["A"], est=(1, 2, 3))]))


def test_explicit_method_requires_its_data():
    result = run(activities=[act("A", [], 3), act("B", ["A"], 2)], calculation_method="PERT")
    issue = next(i for i in result.issues if i.code == IssueCode.METHOD_DATA_MISSING)
    assert issue.activity_ids == ["A", "B"]


def test_estimate_problems():
    assert IssueCode.INCOMPLETE_ESTIMATES in codes(run(activities=[act("A", est=(1, None, 3))]))
    result = run(activities=[act("A", est=(1, 5, 3))])
    issue = next(i for i in result.issues if i.code == IssueCode.INCONSISTENT_ESTIMATES)
    assert "wrong order" in issue.message
    assert IssueCode.NEGATIVE_VALUE in codes(run(activities=[act("A", [], -1)]))


def test_probability_query_problems():
    cpm_only = [act("A", [], 3)]
    assert IssueCode.PROBABILITY_WITHOUT_ESTIMATES in codes(
        run(activities=cpm_only, probability_queries=[ProbabilityQuery(kind="AT_MOST", upper_bound=3)]))
    pert = [act("A", est=(1, 2, 3))]
    assert IssueCode.INVALID_PROBABILITY_QUERY in codes(
        run(activities=pert, probability_queries=[ProbabilityQuery(kind="PERCENTILE_TO_DURATION", target_probability=98)]))
    assert IssueCode.INVALID_PROBABILITY_QUERY in codes(
        run(activities=pert, probability_queries=[ProbabilityQuery(kind="BETWEEN", lower_bound=5, upper_bound=2)]))
    assert IssueCode.INVALID_PROBABILITY_QUERY in codes(run(activities=pert, probability_queries=[ProbabilityQuery(kind="AT_MOST")]))
    assert IssueCode.MISSING_QUERY in codes(run(activities=pert, requested_outputs=["PROBABILITY_QUERY"]))


def test_reference_and_structure_problems():
    assert IssueCode.EMPTY_PROJECT in codes(run(activities=[]))
    assert IssueCode.UNKNOWN_ACTIVITY_REFERENCE in codes(run(activities=[act("A", [], 1)], delay_queries=["Z"]))
    assert IssueCode.INVALID_DUMMY in codes(run(activities=[act("A", [], 1), act("x", ["A"], duration=2, dummy=True)]))
    assert IssueCode.INVALID_TIME_UNIT in codes(run(activities=[act("A", [], 1)], metadata={"time_unit": TimeUnit(code="custom")}))
    assert IssueCode.CYCLE in codes(run(activities=[act("A", ["B"], 1), act("B", ["A"], 1)]))


def test_safe_inferences_are_recorded_and_idempotent():
    result = run(
        activities=[act("A", est=(1, 2, 3)), act("B", est=(1, 2, 3)), act("x", ["A"], dummy=True), act("C", ["x", "B"], est=(2, 3, 4))],
        probability_queries=[ProbabilityQuery(id="p", kind="AT_MOST", upper_bound=5)],
    )
    assert result.ok
    inferred = {(a.field_path, a.value) for a in result.inferences}
    assert {("activities[2].duration", "0"), ("calculation_method", "PERT"), ("requested_outputs", "PROBABILITY_QUERY")} <= inferred
    assert result.project.activities[2].field_provenance["duration"] == "inferred"
    assert len(result.project.assumptions) == len(result.inferences)
    again = normalize_project_input(result.project)
    assert again.ok and again.inferences == [] and again.project == result.project


def test_default_and_question_driven_outputs():
    assert run(activities=[act("A", [], 1)]).project.requested_outputs == ["FULL_REPORT"]
    result = run(activities=[act("A", [], 1)], questions=[QuestionItem(label="a", text="ruta", outputs=["CRITICAL_PATH", "SLACK"])])
    assert result.project.requested_outputs == ["CRITICAL_PATH", "SLACK"]


def test_minimum_chance_question_gets_its_percentile_query():
    """A literal question that sets a minimum chance of meeting the deadline also needs the duration with exactly
    that chance; when the draft only carries P(T <= deadline) the percentile is added from the question text."""
    text = ('Maria Camila concluye que la licitacion que debe hacer para tener una oportunidad realista de ganar el contrato dejara a su empresa una ganancia de cerca de 300000 pesos si el proyecto termina en 16 semanas. Sin embargo, dada la multa por no entregar a tiempo, su compania perderia esa ganancia si el proyecto toma mas de 16 semanas. Por lo tanto, desea presentar la licitacion solo si tiene, al menos, el 70 % de oportunidad de cumplir con la fecha de entrega. Que le aconsejaria?')
    estimates = [act("A", [], est=(1, 2, 9)), act("B", ["A"], est=(2, 3, 4))]
    draft = dict(activities=estimates,
                 probability_queries=[ProbabilityQuery(id="d", kind="AT_MOST", upper_bound=16)],
                 questions=[QuestionItem(label="d", text="Probabilidad de terminar en 16 semanas",
                                         outputs=["PROBABILITY_QUERY"], probability_query_ids=["d"]),
                            QuestionItem(label="e", text=text, outputs=["PROBABILITY_QUERY"],
                                         probability_query_ids=["d"])])
    result = run(**draft)
    assert result.ok
    queries = {q.id: (q.kind.value, q.target_probability) for q in result.project.probability_queries}
    assert queries == {"d": ("AT_MOST", None), "e": ("PERCENTILE_TO_DURATION", 0.7)}
    e = result.project.questions[-1]
    assert e.probability_query_ids == ["d", "e"]
    assert [o.value for o in e.outputs] == ["PROBABILITY_QUERY", "PERCENTILE_DURATION"]
    assert any(i.field_path == "probability_queries[e]" for i in result.inferences)
    # already present (the fixture way): nothing is added twice
    again = normalize_project_input(ProjectDraft.model_validate(result.project.model_dump()))
    assert len(again.project.probability_queries) == 2
    assert again.project.questions[-1].probability_query_ids == ["d", "e"]
