import pytest

from helpers import arcs_from, load_fixture, normalized
from pm_mcp.domain.errors import DomainValidationError, IssueCode
from pm_mcp.domain.models import ArrowArc, ProjectDraft
from pm_mcp.engine.aoa import convert_arrow_network
from pm_mcp.pipeline import solve_project


def preds(conversion):
    return {a.id: a.predecessors for a in conversion.activities}


def test_exercise_arrow_network_dummies_carry_precedence():
    conversion = convert_arrow_network(arcs_from(load_fixture("taller1_transcription.json")))
    # Node 4 is entered by C directly and by B through the dummy 3→4; node 6 by G directly and by F through dummy 5→6.
    assert preds(conversion) == {
        "A": [], "B": ["A"], "C": ["A"], "D": ["B"], "E": ["D"], "F": ["C", "B"], "G": ["C", "B"],
        "H": ["F"], "I": ["G", "F"], "J": ["H"],
    }
    trace = {t.activity_id: t for t in conversion.trace}
    assert trace["F"].via_dummies == ["ficticia_3_4"]
    assert trace["I"].via_dummies == ["ficticia_5_6"]
    assert trace["A"].predecessors == [] and "inicial" in trace["A"].justification
    assert conversion.dummy_arcs == ["ficticia_3_4", "ficticia_5_6"]
    assert all(a.field_provenance["predecessors"] == "inferred" for a in conversion.activities)


def test_reference_example_arrow_network_matches_its_published_precedence_table():
    conversion = convert_arrow_network(arcs_from(load_fixture("pert_example_transcription.json")))
    # The reference solution lists: A none; B A; C B; D B; E A; F D,E; G C,F; H E; I G,H
    assert {k: sorted(v) for k, v in preds(conversion).items()} == {
        "A": [], "B": ["A"], "C": ["B"], "D": ["B"], "E": ["A"], "F": ["D", "E"], "G": ["C", "F"], "H": ["E"], "I": ["G", "H"],
    }


def test_keeping_dummies_as_nodes_gives_the_same_schedule():
    fixture = load_fixture("taller1_transcription.json")
    eliminated = solve_project(normalized(ProjectDraft(activities=convert_arrow_network(arcs_from(fixture)).activities)))
    kept_conversion = convert_arrow_network(arcs_from(fixture), keep_dummies=True)
    assert any(a.is_dummy for a in kept_conversion.activities)
    kept = solve_project(normalized(ProjectDraft(activities=kept_conversion.activities)))
    assert kept.cpm.project_duration == eliminated.cpm.project_duration
    assert kept.cpm.critical_paths == eliminated.cpm.critical_paths
    by_id = eliminated.cpm.by_id()
    for row in kept.cpm.activities:
        if not row.is_dummy:
            assert (row.early_start, row.late_start) == (by_id[row.activity_id].early_start, by_id[row.activity_id].late_start)


def test_chained_dummies_are_followed_transitively():
    conversion = convert_arrow_network([
        ArrowArc(id="A", tail_node=1, head_node=2, duration=1),
        ArrowArc(tail_node=2, head_node=3, is_dummy=True),
        ArrowArc(tail_node=3, head_node=4, is_dummy=True),
        ArrowArc(id="B", tail_node=1, head_node=4, duration=2),
        ArrowArc(id="C", tail_node=4, head_node=5, duration=1),
    ])
    # node 4 is entered by the dummy chain 2→3→4 (carrying A) and by B; order follows arc input order
    assert preds(conversion)["C"] == ["A", "B"]


def test_arrow_network_errors():
    with pytest.raises(DomainValidationError) as exc:
        convert_arrow_network([ArrowArc(id="A", tail_node=1, head_node=2), ArrowArc(id="B", tail_node=2, head_node=1)])
    assert exc.value.issues[0].code == IssueCode.AOA_CYCLE
    with pytest.raises(DomainValidationError) as exc:
        convert_arrow_network([ArrowArc(tail_node=1, head_node=2), ArrowArc(id="B", tail_node=3, head_node=3)])
    assert {i.code for i in exc.value.issues} == {IssueCode.AOA_INVALID_ARC}


def test_parallel_arcs_warn():
    conversion = convert_arrow_network([ArrowArc(id="A", tail_node=1, head_node=2, duration=1),
                                        ArrowArc(id="B", tail_node=1, head_node=2, duration=2)])
    assert conversion.warnings[0].code == IssueCode.AOA_DUPLICATE_ARC
