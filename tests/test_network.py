import pytest

from helpers import act
from pm_mcp.domain.errors import DomainValidationError, IssueCode, IssueSeverity
from pm_mcp.engine.network import analyze_network, build_activity_network


def codes(issues):
    return [i.code for i in issues]


def test_multi_start_multi_end_network():
    network = build_activity_network([
        act("A", [], 2), act("B", [], 5), act("C", [], 4), act("D", ["A"], 1), act("E", ["C"], 3),
        act("F", ["B", "D", "E"], 6), act("G", ["C"], 8),
    ])
    assert network.start_activities == ["A", "B", "C"]
    assert network.end_activities == ["F", "G"]
    assert network.topological_order == ["A", "B", "C", "D", "E", "F", "G"]  # ready activities released in input order
    assert network.levels == {"A": 0, "B": 0, "C": 0, "D": 1, "E": 1, "G": 1, "F": 2}
    assert network.component_count == 1


def test_cycle_is_named_and_downstream_is_unreachable():
    with pytest.raises(DomainValidationError) as exc:
        build_activity_network([act("A", ["C"], 1), act("B", ["A"], 1), act("C", ["B"], 1), act("D", ["C"], 1), act("S", [], 1)])
    issues = exc.value.issues
    cycle = next(i for i in issues if i.code == IssueCode.CYCLE)
    assert "A → B → C → A" in cycle.message
    assert set(cycle.activity_ids) == {"A", "B", "C"}
    unreachable = next(i for i in issues if i.code == IssueCode.UNREACHABLE_ACTIVITY)
    assert unreachable.activity_ids == ["D"]


def test_two_independent_cycles_are_both_reported():
    _, issues = analyze_network([act("A", ["B"], 1), act("B", ["A"], 1), act("C", ["D"], 1), act("D", ["C"], 1)])
    assert codes(issues).count(IssueCode.CYCLE) == 2


def test_structural_errors_are_all_collected():
    _, issues = analyze_network([act("A", [], 1), act("A", [], 2), act("B", ["X"], 1), act("C", ["C"], 1)])
    found = set(codes(issues))
    assert {IssueCode.DUPLICATE_ACTIVITY_ID, IssueCode.UNKNOWN_PREDECESSOR, IssueCode.SELF_DEPENDENCY} <= found
    unknown = next(i for i in issues if i.code == IssueCode.UNKNOWN_PREDECESSOR)
    assert "'X'" in unknown.message and "A, B, C" in unknown.suggestion


def test_warnings_do_not_block():
    network, issues = analyze_network([act("A", [], 1), act("B", ["A", "A"], 1), act("Z", [], 3), act("d", ["A"], dummy=True)])
    assert network is not None
    assert {IssueCode.DUPLICATE_PREDECESSOR, IssueCode.DISCONNECTED_NETWORK, IssueCode.INVALID_DUMMY} <= set(codes(issues))
    assert all(i.severity == IssueSeverity.WARNING for i in issues)
    assert network.component_count == 2
