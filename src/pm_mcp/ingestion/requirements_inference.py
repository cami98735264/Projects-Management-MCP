"""Deterministic, heuristic suggestions of *what is being asked* from an exercise's text (Spanish or English).

Output is always provenance=inferred and meant to be confirmed by the LLM layer against the source. Ambiguous
phrasings produce warnings instead of silent guesses. It never produces activity data.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from pydantic import BaseModel, Field

from pm_mcp.domain.errors import IssueCode, IssueSeverity, ValidationIssue
from pm_mcp.domain.models import (
    ProbabilityQuery,
    ProbabilityQueryKind,
    Provenance,
    QuestionItem,
    ReportLanguage,
    RequestedOutput,
    TimeUnit,
    TimeUnitCode,
)

_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"(?:semanas?|dias?|meses|mes|horas?|weeks?|days?|months?|hours?)"

_OUTPUT_PATTERNS: list[tuple[RequestedOutput, str]] = [
    (RequestedOutput.GANTT, r"\bgantt\b"),
    (RequestedOutput.NETWORK_DIAGRAM,
     r"red (del proyecto|de actividades|actividad)|represent\w* (el proyecto )?(mediante|como|en) una red|"
     r"construya la red|network diagram|activity[- ]on[- ]node|draw the network"),
    (RequestedOutput.EARLY_LATE_TIMES,
     r"(inicio|terminacion|finalizacion|comienzo)( \w+)? (mas )?(temprano|temprana|tardio|tardia|tarde|pronto)|"
     r"early (start|finish)|late (start|finish)|earliest|latest (start|finish)"),
    (RequestedOutput.SLACK, r"holgura|\bslack\b|\bfloat\b"),
    (RequestedOutput.CRITICAL_PATH, r"ruta critica|camino critico|critical path"),
    (RequestedOutput.EXPECTED_DURATION,
     r"tiempo (minimo )?esperado|duracion (minima )?(esperada|del proyecto)|tiempo minimo|expected (project )?(duration|time)|"
     r"project duration|minimum (project )?duration"),
    (RequestedOutput.VARIANCE, r"varianza|variance"),
    (RequestedOutput.ACTIVITY_ESTIMATES,
     r"(tiempo esperado|varianza|expected time|variance)[^.;]{0,60}(cada actividad|por actividad|each activity|per activity)|"
     r"(cada actividad|por actividad|each activity|per activity)[^.;]{0,40}(tiempo esperado|varianza|expected time|variance)"),
    (RequestedOutput.CRASHING,
     r"reduzca el proyecto|reducir el proyecto|comprim|acortamiento|\bcrash|costo total minimo|menor costo|minimum (total )?cost"),
]
_DELAY_RE = re.compile(
    r"(?:atrasar|retrasar|demorar)\w*\s+(?:la\s+)?actividad\s+([A-Za-z0-9]+)|actividad\s+([A-Za-z0-9]+)\s+(?:puede|podria)\s+"
    r"(?:atrasar|retrasar)|activity\s+([A-Za-z0-9]+)\s+(?:can|could|may)\s+be\s+delayed",
    re.IGNORECASE)
_LABEL_RE = re.compile(r"(?<![A-Za-z0-9])([a-zA-Z])\)")
_PERCENT_RE = re.compile(_NUM + r"\s*%")
# probability must be the subject ("probabilidad de terminar…"), not a modifier ("distribución de probabilidad")
_PROBABILITY_SUBJECT = re.compile(r"probabilidad (?:de|del|que)\b|probability (?:of|that)\b|\d+(?:[.,]\d+)?\s*%")
_BETWEEN_RE = re.compile(rf"entre\s+{_NUM}\s*(?:{_UNIT})?\s+y\s+{_NUM}|between\s+{_NUM}\s*(?:{_UNIT})?\s+and\s+{_NUM}")
_VALUE_RE = re.compile(rf"{_NUM}\s*{_UNIT}")
_ASKS_DURATION = re.compile(r"cuant[oa]s?|que duracion|que tiempo|debera?i?a? especificarse|how many|what duration|how long|"
                            r"which duration|plazo")
_AT_MOST_STRONG = re.compile(r"no mas de|no more than")
_AT_LEAST = re.compile(r"o mas\b|\bmas de\b|al menos|como minimo|no menos de|or more|at least|more than|or later")
_AT_MOST = re.compile(r"o menos\b|dentro de|a lo sumo|como maximo|antes de|or less|at most|within|or earlier|\bby\b")


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in stripped if not unicodedata.combining(ch)).lower()


def _to_number(text: str) -> float:
    return float(text.replace(",", "."))


class RequirementSuggestions(BaseModel):
    language: ReportLanguage
    time_unit: TimeUnit | None = None
    time_unit_evidence: str | None = None
    questions: list[QuestionItem] = Field(default_factory=list)
    probability_queries: list[ProbabilityQuery] = Field(default_factory=list)
    delay_queries: list[str] = Field(default_factory=list)
    requested_outputs: list[RequestedOutput] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    note: str = "Heuristic suggestions (provenance=inferred). Confirm them against the source before building the ProjectDraft."


def split_questions(text: str) -> list[tuple[str, str]]:
    """Split 'a) ... b) ... c) ...' into labelled parts; labels must appear in alphabetical sequence."""
    accepted: list[re.Match] = []
    expected = "a"
    for match in _LABEL_RE.finditer(text):
        if match.group(1).lower() == expected:
            accepted.append(match)
            expected = chr(ord(expected) + 1)
    if not accepted:
        return [("1", text.strip())] if text.strip() else []
    parts = []
    for i, match in enumerate(accepted):
        last = i + 1 == len(accepted)
        segment = text[match.end():len(text) if last else accepted[i + 1].start()]
        if last:
            segment = _trim_trailing_content(segment)
        parts.append((match.group(1).lower(), " ".join(segment.split())))
    return parts


def _trim_trailing_content(segment: str) -> str:
    """The last question ends at a blank line, or where a finished sentence is followed by a new non-lowercase line
    (so formula sheets or figure captions after the questions are not swallowed; wrapped lines are kept)."""
    lines = segment.split("\n")
    kept = [lines[0]]
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            break
        if kept[-1].rstrip().endswith((".", "?", ":", "!")) and not stripped[0].islower():
            break
        kept.append(line)
    return "\n".join(kept)


def _probability_queries(label: str, raw: str, norm: str, warnings: list[ValidationIssue]) -> list[ProbabilityQuery]:
    queries: list[ProbabilityQuery] = []
    percent = _PERCENT_RE.search(norm)
    if percent and _ASKS_DURATION.search(norm):
        return [ProbabilityQuery(id=label, kind=ProbabilityQueryKind.PERCENTILE_TO_DURATION,
                                 target_probability=_to_number(percent.group(1)) / 100,
                                 provenance=Provenance.INFERRED, source_text=raw)]
    for clause in (c for c in norm.split(";") if c.strip()):
        between = _BETWEEN_RE.search(clause)
        if between:
            numbers = sorted(_to_number(n) for n in between.groups() if n is not None)
            queries.append(ProbabilityQuery(kind=ProbabilityQueryKind.BETWEEN, lower_bound=numbers[0], upper_bound=numbers[1],
                                            provenance=Provenance.INFERRED, source_text=clause.strip()))
            continue
        for value in _VALUE_RE.finditer(clause):
            number = _to_number(value.group(1))
            if _AT_MOST_STRONG.search(clause):
                kind = ProbabilityQueryKind.AT_MOST
            elif _AT_LEAST.search(clause):
                kind = ProbabilityQueryKind.AT_LEAST
            elif _AT_MOST.search(clause):
                kind = ProbabilityQueryKind.AT_MOST
            else:
                kind = ProbabilityQueryKind.AT_MOST
                warnings.append(ValidationIssue(
                    code=IssueCode.AMBIGUOUS_QUERY, severity=IssueSeverity.WARNING, needs_user_clarification=True,
                    message=f"Question {label}: '{clause.strip()}' has no 'or less / or more' qualifier; assumed AT_MOST {number:g}."))
            bound = {"upper_bound": number} if kind == ProbabilityQueryKind.AT_MOST else {"lower_bound": number}
            queries.append(ProbabilityQuery(kind=kind, provenance=Provenance.INFERRED, source_text=clause.strip(), **bound))
    if len(queries) == 1:
        queries[0].id = label
    else:
        for i, q in enumerate(queries, start=1):
            q.id = f"{label}{i}"
    if not queries:
        warnings.append(ValidationIssue(code=IssueCode.AMBIGUOUS_QUERY, severity=IssueSeverity.WARNING, needs_user_clarification=True,
                                        message=f"Question {label} mentions a probability but no duration or percentage was recognised."))
    return queries


def suggest_requirements_from_text(text: str) -> RequirementSuggestions:
    norm_all = _normalize(text)
    spanish = len(re.findall(r"\b(el|la|de|los|las|que|para|del|una)\b", norm_all))
    english = len(re.findall(r"\b(the|of|and|what|for|to|is|an)\b", norm_all))
    language = ReportLanguage.ES if spanish >= english else ReportLanguage.EN

    unit_counts = Counter()
    for code, pattern in ((TimeUnitCode.WEEK, r"\bsemanas?\b|\bweeks?\b"), (TimeUnitCode.DAY, r"\bdias?\b|\bdays?\b"),
                          (TimeUnitCode.MONTH, r"\bmes(es)?\b|\bmonths?\b"), (TimeUnitCode.HOUR, r"\bhoras?\b|\bhours?\b")):
        unit_counts[code] = len(re.findall(pattern, norm_all))
    time_unit, evidence = None, None
    if unit_counts and unit_counts.most_common(1)[0][1] > 0:
        code, count = unit_counts.most_common(1)[0]
        time_unit, evidence = TimeUnit(code=code), f"'{code.value}' mentioned {count} time(s)"

    warnings: list[ValidationIssue] = []
    questions: list[QuestionItem] = []
    all_queries: list[ProbabilityQuery] = []
    delays: list[str] = []
    outputs: list[RequestedOutput] = []
    for label, raw in split_questions(text):
        norm = _normalize(raw)
        q_outputs = [output for output, pattern in _OUTPUT_PATTERNS if re.search(pattern, norm)]
        if RequestedOutput.ACTIVITY_ESTIMATES in q_outputs:
            q_outputs = [o for o in q_outputs if o not in (RequestedOutput.EXPECTED_DURATION, RequestedOutput.VARIANCE)]
        q_queries: list[ProbabilityQuery] = []
        if _PROBABILITY_SUBJECT.search(norm):
            q_queries = _probability_queries(label, raw, norm, warnings)
            # "ruta crítica" / "tiempo esperado" inside a probability question describe context, not a deliverable
            q_outputs = [o for o in q_outputs if o not in (RequestedOutput.CRITICAL_PATH, RequestedOutput.EXPECTED_DURATION)]
            kinds = {q.kind for q in q_queries}
            if kinds - {ProbabilityQueryKind.PERCENTILE_TO_DURATION}:
                q_outputs.append(RequestedOutput.PROBABILITY_QUERY)
            if ProbabilityQueryKind.PERCENTILE_TO_DURATION in kinds:
                q_outputs.append(RequestedOutput.PERCENTILE_DURATION)
        q_delays = [next(g for g in m.groups() if g) for m in _DELAY_RE.finditer(raw)]
        if q_delays:
            q_outputs.append(RequestedOutput.ACTIVITY_MAX_DELAY)
            q_outputs = [o for o in q_outputs if o != RequestedOutput.EARLY_LATE_TIMES]
        if not q_outputs:
            warnings.append(ValidationIssue(code=IssueCode.AMBIGUOUS_QUERY, severity=IssueSeverity.WARNING,
                                            message=f"Question {label}: no known deliverable recognised in '{raw[:80]}'."))
        questions.append(QuestionItem(label=label, text=raw, outputs=list(dict.fromkeys(q_outputs)),
                                       probability_query_ids=[q.id for q in q_queries], delay_activity_ids=q_delays))
        all_queries += q_queries
        delays += q_delays
        outputs += q_outputs
    return RequirementSuggestions(
        language=language, time_unit=time_unit, time_unit_evidence=evidence, questions=questions,
        probability_queries=all_queries, delay_queries=list(dict.fromkeys(delays)),
        requested_outputs=list(dict.fromkeys(outputs)), warnings=warnings)
