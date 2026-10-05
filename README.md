# pm-scheduling-mcp

An **MCP server that is a deterministic calculation engine** for project-scheduling exercises (CPM, probabilistic
PERT, Gantt, crashing). An LLM reads the user's exercise (docx / xlsx / pdf / text / images), fills a strongly typed
canonical model, and calls typed tools; **every number is computed, independently re-validated and written to a
live-formula `.xlsx` by tested code**, never by LLM arithmetic.

```
User files ──► extract_document_content ──► (LLM reads text + looks at images) ──► suggest_requirements_from_text
                                                              │
                        activity-on-arrow figure? ──► convert_arrow_network
                                                              ▼
                                   ProjectDraft ──► normalize_project_input ──► ok? ── no ──► ask the user
                                                              │ yes
                               solve_project / individual engine tools (network, PERT, CPM, probability, Gantt, crashing)
                                                              ▼
                                 validate_calculations (independent re-derivation, always run)
                                                              ▼
                     generate_solution_workbook ──► validate_workbook (round-trip, every formula evaluated) ──► .xlsx + answers
```

---

## 1. What inspecting the reference files showed (and where it differs from the brief)

Both files were opened directly: the docx was unzipped and all 8 images viewed; the xlsx had its 7 sheets, all
20 non-empty cells and all 57 image placements (53 distinct images) enumerated and viewed.

| Finding | Consequence in the design |
|---|---|
| **The exercise figure is Activity-on-*Arrow*, not AON.** Activities A–J are arrows between event nodes 1–9, and the dashed arrows 3→4 and 5→6 are dummies. The reference PERT example (Ejemplo 4.2) is also arrow-based, and its solution says precedences are *read from the ACTIVIDAD-FLECHA network* before drawing ACTIVIDAD-NODO. | A deterministic `convert_arrow_network` tool derives predecessors, following dummy arrows transitively, and records a justification per activity. Dummies are also representable as zero-duration AON nodes (`is_dummy`). Both representations give identical schedules (tested). |
| **The Gantt convention uses two rows per activity.** The first bar is as early as possible (ES→EF); the second is as late as possible (LS→LF). Observations read "Actividad Crítica" (shaded) or "Holgura de N semanas". | The Gantt sheet reproduces exactly this layout with live formulas. |
| **Probabilities use a printed-table convention.** Z is rounded to 2 decimals and Φ read to 4 decimals (Z = 1.6036 → 1.60 → 0.9452). For a target probability, the smallest table Z whose Φ reaches it is used (0.90 → Z = 1.29, not 1.28). Durations are rounded **up** to whole units (26.6089 → 27). | Every probability is computed both **exact** (erf / Acklam+Halley) and **table**. `options.probability_method` selects which one is the headline answer; both always appear. |
| The reference example's solution table lists columns **a, b, m** (not a, m, b). | A clear error is raised if a ≤ m ≤ b fails, suggesting that the columns may have been transposed. |
| A formula image in the docx (σ) is referenced from `numbering.xml`, not from the body. | The extractor also walks every other relationship part and any orphan media. |
| The xlsx has **no live formulas**. Its content is images plus 20 text cells. The CPM sheets contain complete worked **crashing** examples, including two critical paths, a shared activity, an irreducible activity and indirect costs. | Crashing is implemented as an optional tool, validated against both worked examples (11 weeks at $990; optimum 11 weeks at $11,600). |
| OCR (Tesseract) on the tables misreads E→"i", G→"6", J→"ci", 11→"it". | OCR is off by default and always labelled a low-confidence hint. The LLM must transcribe images visually. |

The findings, with the source image of each rule, are persisted in `src/pm_mcp/reference/` (see §6).

## 2. Stack

| Concern | Choice | Why |
|---|---|---|
| Runtime | Python ≥ 3.11 | The reference-material pipeline (openpyxl, Tesseract, matplotlib) and the official MCP SDK live here. |
| MCP | `mcp` (FastMCP) | Official SDK. Typed signatures become JSON schemas and pydantic returns become structured output. |
| Schemas | **Pydantic v2** | One typed model is used for MCP I/O, JSON schema, validation and serialization. `extra="forbid"` catches mistranslated field names. |
| Arithmetic | `fractions.Fraction` | t_e, σ², ES/EF/LS/LF, slack and costs are exact, so "slack == 0" is never a float-tolerance question and results read like the course (σ² = 37/9). |
| Normal distribution | `math.erfc` plus Acklam with 2 Halley refinements | No lookup tables. Tested against known Z values and scipy (rel. 1e-12). |
| Workbook writer | **xlsxwriter** | It can write formula *and* cached result, so files show correct numbers even in viewers that don't recalculate. Output is reproducible byte for byte. |
| Workbook validation | **openpyxl** plus our own formula evaluator | Reading with a *different* library than the writer, and re-evaluating every formula, makes the check independent of how the file was written. |
| Diagram | worksheet cells (no pictures) | Nodes are bordered 2 × 4 cell blocks linked to the CPM sheet; arcs are box-drawing characters in narrow channel columns ending in ►, critical arcs as red double lines. |
| Crashing | networkx min-cut | Finds the cheapest set of activities that shortens *all* critical paths. |

## 3. Layout

```
pyproject.toml                    dependencies, extras [ocr] [pdf] [dev], console script
scripts/build_reference_inventory.py
src/pm_mcp/
  server.py                       17 MCP tools, 2 resources, 1 prompt
  service.py                      application services: normalize → run → typed Envelope (never raises)
  pipeline.py                     solve_project: all calculations → independent validation → answers
  solution.py                     aggregate result models
  i18n.py                         Spanish (reference terminology) / English report strings
  logging_config.py               JSON-lines structured logs to stderr
  domain/   models.py             canonical model (ProjectDraft, Project, Activity, ArrowArc, ProbabilityQuery, …)
            rules.py              semantic validation + method inference (keyed only on data shape)
            numbers.py            exact number types (NumberIn, Exact) and rounding helpers
            errors.py             ValidationIssue / IssueCode / exceptions
  engine/   network.py            AON graph, cycle naming, unreachable/disconnected detection
            aoa.py                activity-on-arrow → activity-on-node
            pert.py  cpm.py  probability.py  gantt.py  crashing.py
            validation.py         validate_calculations (independent re-derivations)
  ingestion/document_extraction.py  docx/xlsx/pdf/text/json/image → text, tables, cells, saved images (+OCR)
            requirements_inference.py question split + outputs/queries/time-unit suggestions (es/en)
            normalize.py          ProjectDraft → Project with recorded safe inferences
  output/   workbook.py           sheet planning + live-formula writer
            formula_eval.py       Excel formula evaluator (whitelisted grammar)
            workbook_validation.py round-trip validation
            network_diagram.py    AON layout: layers, exact crossing minimisation, straight critical path
            network_cells.py      the layout drawn with cells: node blocks, routed arcs (─│┌┐…►), red double critical arcs
            answers.py            per-question answer (direct result first, bullet lines) and numbered procedure steps
  reference/ inventory.json       machine-generated inventory of the reference files
            methodology.json      curated classification + methodology rules with sources
tests/                            99 tests (unit, integration, golden, synthetic, server, guards) + fixtures
examples/<case>/                  project_draft.json · solucion.xlsx · solution_summary.json
```

## 4. Install and run

```bash
pip install -e ".[dev,ocr]"          # ocr/pdf extras are optional
pytest                               # 99 tests
pm-scheduling-mcp                    # stdio MCP server  (or: python -m pm_mcp.server)
```

MCP client configuration (Claude Desktop / Claude Code):

```json
{ "mcpServers": { "pm-scheduling": {
    "command": "pm-scheduling-mcp",
    "env": { "PM_MCP_OUTPUT_DIR": "C:/Users/me/pm-output" } } } }
```

| Env var | Default | Meaning |
|---|---|---|
| `PM_MCP_OUTPUT_DIR` | `./output` | Base directory for relative workbook paths and extracted images |
| `PM_MCP_LOG_LEVEL` | `INFO` | Structured log level (stderr) |
| `PM_MCP_TESSERACT_CMD` | PATH lookup | Tesseract executable for optional OCR |

## 5. Canonical domain model (`domain/models.py`)

```
ProjectDraft                                (what the LLM produces; Project = validated subclass)
 ├─ metadata: title, time_unit{code: hour|day|week|month|custom, singular, plural}, source_description, language es|en
 ├─ activities: Activity[]
 │    id, name, predecessors[], is_dummy, duration | optimistic/most_likely/pessimistic_duration,
 │    crash{crash_duration, normal_cost, crash_cost}?, provenance, field_provenance{field→provenance}, provenance_notes[]
 ├─ calculation_method: CPM | PERT | BOTH | null (inferred from data shape)
 ├─ requested_outputs: GANTT, NETWORK_DIAGRAM, EARLY_LATE_TIMES, SLACK, CRITICAL_PATH, EXPECTED_DURATION, VARIANCE,
 │                     ACTIVITY_ESTIMATES, PROBABILITY_QUERY, PERCENTILE_DURATION, ACTIVITY_MAX_DELAY, CRASHING, FULL_REPORT
 ├─ probability_queries: {id, kind AT_MOST|AT_LEAST|BETWEEN|PERCENTILE_TO_DURATION, lower/upper_bound, target_probability}
 ├─ delay_queries[], crashing{target_duration, indirect_costs[], step}, questions[{label, text, outputs, query ids}]
 ├─ assumptions[{field_path, value, justification, provenance}], arrow_network_trace[]
 └─ options: probability_method exact|table, variance_strategy max_variance_path|all_critical_activities
```

* Provenance values: `user_stated`, `document_extracted`, `inferred`, `calculated`, `assumed`. They are kept end to end and shown in the Datos, Enunciado and Resultados sheets.
* **Constructing a `Project` runs every rule**, so an invalid project cannot exist at runtime. Tools accept a `ProjectDraft` and normalize it first, so they never run on unvalidated data.
* **Method semantics:**
  * `CPM`: the deterministic `duration` feeds the passes.
  * `PERT`: t_e feeds the passes, and σ² and probabilities are available.
  * `BOTH`: both data sets exist, and two schedules are produced.
* Graphs may have multiple start and end activities, dummies, and several critical paths. Disconnected groups produce a warning; cycles are errors that name the loop.

## 6. Reference-material strategy (`reference/`)

* `inventory.json` is generated once by `scripts/build_reference_inventory.py`, reusing the same extractor the server uses. It records every sheet, cell and image placement with location and **sha256**.
* `methodology.json` is curated from the visual inspection:
  * a category and summary for **every** image and cell (tests enforce full coverage);
  * **methodology rules** (t_e, σ², project variance, Z, table and percentile conventions, slack, Gantt layout, AON notation, AOA→AON, crash slope, crashing procedure, total-cost optimum). Each rule cites its source items and names the engine function that implements it. Tests check that the cited items exist, that the functions import, and that the formula strings agree with the engine on random inputs.
* It governs **methodology and conventions only**. It contains no exercise numbers used for calculation.
* It is exposed as the `get_methodology_reference(topic)` tool and the `pm://methodology` resource.

## 7. MCP tools

All project tools take `project: ProjectDraft` and return `Envelope{ok, result, issues[], inferences[]}` unless noted.

| Tool | Result | Notes |
|---|---|---|
| `extract_document_content(path, image_output_dir?, ocr?)` | `ExtractedDocument` | Text in document order with `[IMAGE n]` markers, tables, cells, saved images with location and sha256, guidance |
| `get_methodology_reference(topic?)` | rules + classified items | pert / cpm / gantt / network / probability / crashing |
| `suggest_requirements_from_text(text)` | `RequirementSuggestions` | Splits a)…g); suggests outputs, queries, delay ids, time unit, language; warns on ambiguity |
| `convert_arrow_network(arcs, keep_dummies?, language?)` | activities + trace | AOA → AON with a justification per activity |
| `normalize_project_input(project)` | `NormalizationResult` | Validated `Project`, or issues plus `clarification_questions` |
| `build_activity_network` | `ActivityNetwork` | Topological order, starts/ends, levels, warnings |
| `calculate_pert_estimates` | per-activity t_e and σ² | Exact fractions with working |
| `calculate_cpm(project, duration_source?)` | `CpmResult` | ES/EF/LS/LF, total and free slack, all critical paths |
| `aggregate_critical_path_variance(project, strategy?)` | Σσ², σ | Per-path variances when several paths exist |
| `calculate_completion_probability(t_e, σ, query, method?)` | `ProbabilityResult` | AT_MOST / AT_LEAST / BETWEEN; exact and table |
| `calculate_percentile_duration(t_e, σ, p, method?)` | `PercentileResult` | Z exact and table, T_p, rounded-up duration |
| `generate_gantt_schedule` | `GanttSchedule` | Early and late bar columns, observation |
| `calculate_crashing_schedule` | `CrashingResult` | Slopes, states, stop reason, cost table, optimum |
| `solve_project` | `ProjectSolution` | Everything requested, validated, with per-question answers |
| `validate_calculations(project, solution?)` | check report | Also detects a tampered solution passed back in |
| `generate_solution_workbook(project, output_path, overwrite?)` | path, sheets, answers, both reports, `layout` | Writes to a temp file, validates, then publishes; an invalid file is kept as `*.invalid.xlsx`. Progressive by default (§10.1), classic fallback with a note |
| `validate_workbook(path, project?)` | `WorkbookValidationReport` | With a project, every computed cell is compared with the engine |

Resources: `pm://methodology`, `pm://schema/project-draft`. Prompt: `solve_exercise_workflow`.

## 8. AI / MCP interaction model

1. **Extract.** Call `extract_document_content` on each file. Read `text_blocks`, then **open every saved image**: tables, formulas and figures are often only images.
2. **Understand the questions.** Call `suggest_requirements_from_text` on the joined text, and confirm each suggested question, output, query and time unit against the source. A part that sets a minimum chance of meeting a deadline ("presentar la licitación solo si tiene al menos el 70 % de oportunidad de cumplir") gets two queries: P(T ≤ deadline) and the duration with that chance (`PERCENTILE_TO_DURATION`, sheet `Duración objetivo`). `normalize_project_input` adds the percentile from the literal question text when a draft forgot it (recorded in `inferences`).
3. **Transcribe the data.**
   * A precedence table becomes `Activity[]`.
   * A numbered-circle, lettered-arrow figure becomes `ArrowArc[]` (dashed = dummy), then `convert_arrow_network`.
   * Leave unknown values unset. **Never invent** a duration, estimate or precedence.
4. **Normalize.** Call `normalize_project_input`. If `ok=false`, ask the user the `clarification_questions` and resubmit. Safe inferences come back in `inferences`, with justifications: dummy duration 0, method from data shape, outputs from queries.
5. **Solve.** Call `solve_project` (or the individual tools for step-by-step teaching), then `generate_solution_workbook`.
6. **Report.** Quote numbers exactly as returned. Present both exact and table probabilities when the course uses tables.

Branching anywhere in the engine depends only on the **shape** of the validated project: which fields exist, what was requested. `tests/test_server_and_guards.py` scans the source for exercise-specific literals.

## 9. Deterministic engine notes

* **Network.** Kahn topological sort with input-order tie-breaks. Remaining nodes are walked backwards to name each cycle (`A → B → C → A`). Activities downstream of a cycle are reported as unreachable. Union-find detects disconnected groups.
* **CPM.** ES = max EF(pred); LF = min LS(succ), with the project duration used for terminals. Total slack = LS − ES, checked equal to LF − EF. Free slack is also reported. Critical paths are enumerated over *tight* critical arcs (EF(u) = ES(v)), with dummies hidden in the display.
* **PERT.** t_e = (a + b + 4m)/6 and σ² = ((b − a)/6)². Project σ² sums the critical path. With several critical paths the default takes the largest-variance path, reports every path, and emits a warning.
* **Probability.** Z = (T_p − t_e)/σ. P(≥) = 1 − Φ; P(between) = Φ(Z₂) − Φ(Z₁); T_p = t_e + Z·σ. The table conventions are described in §1. σ = 0 is handled explicitly.
* **Gantt.** Column k covers [k−1, k]. A bar occupies columns ⌊start⌋+1 … ⌈finish⌉. Partially covered columns are listed.
* **Crashing.** Each step computes CPM, then finds the **minimum-cost vertex cut** of the critical sub-network among reducible activities, cutting at slope; ties go to the fewest activities. That set is shortened by min(step, remaining reduction, smallest positive slack, distance to target). The loop stops when some critical path has nothing reducible. Both reference problems are reproduced step by step.
* **Cost curve (S-curve).** The plan is the compressed one when crashing recommends a minimum-total-cost duration, otherwise the normal durations; each activity's direct cost is the same `Cn + slope·(Dn − D)` the crashing states use. The only added convention is the usual one for an S-curve: a cost is spread uniformly over the activity's execution, so period k (the interval [k−1, k]) is charged `cost_per_period × overlap` with [ES, EF]. Periods therefore sum back to the total direct cost exactly.

## 10. XLSX output

Sheets are planned by `plan_sheets(project, outputs)`:

| Sheet (es / en) | Generated when | Contents |
|---|---|---|
| Enunciado / Problem | always | Questions, requested outputs, queries, arrow→node trace, assumptions |
| Datos / Activity Data | always | Actividad / Descripción / Predecesoras / Ficticia / Duración or Optimista (a), Normal (m), Pesimista (b) / crash data / Procedencia |
| PERT | schedule outputs **and** three-point data | `=(a+b+4*m)/6`, `=((b-a)/6)^2`, fractions, working, critical flag, Σσ² as `=F5+F7+…`, `=SQRT()` |
| CPM | any schedule-dependent output | Live forward/backward pass: `=MAX(TC preds)`, `=MIN(IL succs)`, `=ROUND(IL-IC,10)`, critical flag; grey conditional formatting |
| CPM determinístico | method BOTH | Same, on deterministic durations |
| Red AON | NETWORK_DIAGRAM | Node and arrow legend; the network drawn with cells only (no images): node blocks whose six cells are formulas to CPM, arcs routed with box-drawing characters and ► heads, critical arcs as red double lines, critical nodes shaded; arc list |
| Gantt | GANTT | Two rows per activity, week columns, bar cells `=IF(AND(…),"█","")` with fill, Observaciones formula |
| Probabilidad | probability or percentile queries | t_e, σ², σ links; per query Z, `NORMSDIST`, table Z and Φ, probability; `NORMSINV`, T_p, `ROUNDUP` |
| Compresión / Crashing | CRASHING | Slope formulas, step table with chained cost formulas, direct/indirect/total summary, optimum check (tables only, no charts) |
| Red AON por paso / Network per step | NETWORK_DIAGRAM **and** CRASHING | One AON network **per compression step**, drawn with cells: the same network re-scheduled with that step's durations, so every node's t, IC \| TC, IL \| TL and slack are the step's own and the critical path moves with them. Beside each network, a panel linked to the compression sheet (project duration, activity shortened to reach the step, direct-cost increase paid, direct / indirect / total cost, length of every route) and one sentence saying what was shortened and what is shortened next |
| Curva de costos / Cost curve | schedule outputs **and** a duration + cost for every activity | Per-activity cost and cost per period (linked to Compresión or Datos), a row per period with the activities in progress, that period's cost and the cumulative cost, a self-check `=acumulado−total`. Tables only: the workbook contains no charts and no pictures |
| Resultados / Final Results | always | One block per question: the answer as short bullet lines (direct result first), the **Procedimiento** as numbered steps — one row per step, bold title with the formula and one substituted operation per line — and a **linked key value**; provenance counts; assumptions; the independent check table; warnings |

Derived numbers are **live formulas** chained from Datos, so changing an estimate recalculates the whole workbook (tested). Each formula is written with the engine's value cached.

### 10.1 Progressive layout (default: `options.workbook_layout = "progressive"`)

The table above is the **classic** layout (`"classic"`, unchanged and still available, also via
`generate_workbook(..., layout="classic")`). By default the workbook is **progressive and cumulative**: one sheet per
solution step, every sheet = the previous sheet + the new step, and the last sheet holds the whole solution.

| Sheet | Adds |
|---|---|
| `1.1 Contexto` | title, `metadata.context` (literal narrative of the statement; only the title when it is unset — nothing is invented), time unit |
| `1.2 Datos` | the Datos table (+ the arrow→node trace when the network came from an AoA figure) |
| `1.3 Preguntas` | part / literal question (`QuestionItem.text`); only if there are questions |
| `N Tiempos esperados` | PERT only: table A–J (no "critical?" column, no project σ²) |
| `N Red del proyecto` | AON network with only Act and t, single black arrows, arcs without "critical?"; CPM table A–D |
| `N Pase adelante` / `Pase atrás` | IC/TC (+ D2) / IL/TL, in the network nodes and in the CPM table |
| `N Holguras y ruta crítica` | slack columns, row 3, shading, red double arrows, arc "critical?" column, PERT column K, CPM determinístico |
| `N Gantt` | the Gantt (if requested) |
| `N Varianza del proyecto`, `Probabilidad`, `Duración objetivo` | PERT σ²/σ rows, the probability table, the percentile table |
| `N Pendientes` | slopes, normal direct cost, state 0 row and its network |
| `N Compresión 27-26`, … | one per engine step (down to the last one, even past the optimum): E–G of the previous state, the new state row and its network; the stop reason on the last one |
| `N Costo total` / `Decisión` | direct + indirect = total table (and the hidden indirect/total rows of every step panel) / optimum rows |
| `N Curva de costos`, `N Respuestas` | the cost curve; the full Resultados block at the bottom |

Rows 1–2 of every sheet are a yellow `Paso N — <title>` banner with a 1–2 line note (es/en, `i18n.py`
`prog.*`) saying what is computed, with which formula and why. Every block has a header row (`Paso k — …`, yellow when
it changed on this sheet); no emoji anywhere; cells new on the sheet are yellow unless they have their own
fill.

How it works (`output/recording.py`, `output/progressive.py`): the classic writer draws every sheet through a
recording proxy (forwards every call unchanged — the classic file is byte-identical — and records it with its
format spec and *tags* such as `es`, `slack`, `line_crit`, `state:3`, `costpanel`). A mask assigns every record to a
step; the layout is computed once for the last sheet (so nothing moves between sheets) and every sheet replays the
records whose step ≤ its own, translating coordinates and rewriting formula / conditional-format references with
openpyxl's Tokenizer (`$` kept). Lanes, because column widths are global to a sheet: tables on the left in uniform
13.7-wide columns (a wider classic column becomes several merged cells), then the main network, the per-step networks
and the Gantt side by side with their own widths (so box-drawing arrows still join), each separated by a fence column;
the drawing lanes start below the problem blocks; Resultados goes below the bottom of every lane.

Guarantees (tested in `tests/test_progressive.py`): formulas only reference their own sheet and only cells already
visible on it (checked while writing; the builder raises otherwise); every sheet evaluates on its own; no cell, double
arrow, shading or conditional format appears before its step; one sheet per engine compression step; the last sheet
contains every value of the classic Resultados; sheet names ≤ 27 characters (fit `P12 ` from pm-solve-api's merge);
LibreOffice recalculates every formula of the Taller 2 to the engine values. Expected values of the progressive file
are the classic expectations of each source cell. If the progressive build raises or fails `validate_workbook`, the
classic workbook is delivered instead, with a note in `notes` and a warning in the log (`result.layout` says which
one was written). `validate_workbook(path, project)` regenerates with the layout of the file it checks.

Pitfalls: a merged cell never lets text overflow, so plain text cells are merged as far right as their text needs
(up to the next cell of the final layout); the fence columns hold a `" "` in rows with text so a long line stops at its
lane; static text that mentions a classic sheet name ("ver hoja 'Compresión'", "la hoja CPM", "the CPM sheet") is
rewritten to the tab where that block first appears (e.g. "ver hoja '6 Pendientes'"; tested for every case).

Merging several problems (pm-solve-api `merge.py`) copies the sheets with openpyxl, which cannot write formula
results, so it injects each source cell's cached result into the saved XML: the merged file shows every number even
in readers that do not recalculate.

## 11. Validation layer

* **`validate_calculations`** runs after every solve; any failure is a hard error. Its checks:
  * network arcs against the predecessor lists;
  * t_e and σ² via the alternate forms a/6 + 2m/3 + b/6 and (b − a)²/36;
  * EF − ES = LF − LS = d and LS − ES = LF − EF;
  * every precedence respected;
  * project duration by an independent label-correcting longest path in input order;
  * each critical path summing to the duration;
  * Σσ² re-summed from the raw estimates;
  * Φ by Simpson integration of the pdf (not erf);
  * percentile round-trip and the table rule;
  * Gantt bar positions;
  * every crashing state re-scheduled and re-costed;
  * the cost curve re-derived period by period (costs, overlaps, cumulative sum and total);
  * delays equal to slack.
* **`validate_workbook`** runs after every write. It opens the file with openpyxl and checks promised versus present sheets, error tokens, parseability, missing sheets and unknown functions (also inside conditional formats), and circular or dangling references. It evaluates **every formula** and compares the result with both the cached value and the engine value.

## 12. Tests (`pytest`: 175 passing)

| Area | File |
|---|---|
| Exact numbers, schema strictness, JSON logging | `test_numbers_and_models.py` |
| Cost curve: per-period spread, compressed vs normal basis, sheet (no charts) and the per-step networks | `test_cost_curve.py` |
| Cycles (named), duplicates, unknown and self references, unreachable, disconnected | `test_network.py` |
| AOA → AON (exercise dummies, reference example table, transitive dummies, kept-dummy equivalence) | `test_aoa_conversion.py` |
| PERT and CPM hand-derived tables: exercise, reference Gantt example, 12-activity multi-critical synthetic, fractions, dummy nodes, variance strategies | `test_pert_and_cpm.py` |
| Φ and Φ⁻¹ against known values and scipy; exercise queries; reference table values 0.9452 / 0.5 / 0.0548 / 0.4452 and 26.6089 → 27; σ = 0 | `test_probability.py` |
| Gantt bars and observations; both crashing problems step by step; target; input errors | `test_gantt_and_crashing.py` |
| §8 ambiguity: nothing fabricated, safe inferences recorded, idempotence | `test_normalize.py` |
| Question understanding for the docx, the reference PERT and Gantt examples, and English text | `test_requirements_inference.py` |
| docx and xlsx extraction (all 8 / 57 images, cells), plain formats, OCR hint | `test_document_extraction.py` |
| Inventory hashes, full classification coverage, rule sources, engine formula agreement | `test_reference_material.py` |
| Formula evaluator | `test_formula_eval.py` |
| Sheet planning, live-formula recalculation, broken-workbook detection, reproducibility, path guards | `test_workbook.py` |
| **Golden:** docx → answers → workbook; reference PERT example → workbook | `test_golden_exercises.py` |
| Progressive layout: every fixture + penalty crashing, cumulative masks, nothing ahead, own-sheet formulas, compression sheets, merge.py, LibreOffice recalculation of the Taller 2, classic fallback | `test_progressive.py` |
| CPM-only English days with 2 critical paths; BOTH in months with a dummy node and fractional times; 60-activity random DAG | `test_synthetic.py` |
| MCP JSON round trip, tampered solution, typed errors, no-hardcoding guard | `test_server_and_guards.py` |

Fixtures in `tests/fixtures/` hold **raw transcribed inputs only**, pinned to the sha256 of the source image. Expected results are derived in test comments (hand arithmetic) or come from scipy.

## 13. Extensibility

* **Crashing** is already implemented behind its own output, tool and sheet, with no change to the other contracts. Adding an "un-crash" pass or a cost cap is internal to `engine/crashing.py`.
* **Resource leveling:** add `Activity.resources: dict[str, NumberIn]` and `ProjectDraft.resource_limits`, then a pure `engine/leveling.py` that consumes `CpmResult` (free and total slack are already computed) and returns shifted starts. Add a `RESOURCE_LEVELING` output, a `plan_sheets` rule and an answer builder. Existing outputs are unaffected, because every stage is keyed on requested outputs.
* **Other duration distributions** (triangular, beta with custom λ): add an estimate model and a strategy enum next to `options.variance_strategy`. `calculate_pert_estimates` is the only consumer.
* **Monte Carlo** schedule risk: a separate engine module using a seeded `random.Random`, keeping determinism.
* **More languages:** add a block to `i18n.py`.
* **New input formats:** add a branch to `document_extraction.py`. The output shape does not change.

## 14. Acceptance checklist

| Requirement | Status |
|---|---|
| Inspect both files (text, images, cells, drawings) before designing | ✅ §1; inventory of all 65 image placements and 20 cells |
| Methodology derived from the reference material, persisted, not re-parsed per request | ✅ `reference/*.json`, tested for coverage and agreement |
| Canonical typed model, validated before any calculation | ✅ Pydantic; `Project` construction enforces all rules |
| Dummy activities, multiple start/end activities, multiple critical paths | ✅ tested |
| Separate tools: normalization, network, CPM, PERT, variance, probability, percentile, Gantt, XLSX, validation | ✅ 17 tools |
| Normal CDF / inverse without lookup tables; tested (1.96 → 0.975, 2.05 → 0.9798) | ✅ |
| Conditional sheets; live formulas; critical highlighting; conventions reproduced | ✅ §10 |
| Mandatory calculation validation and workbook round-trip validation | ✅ both run on every solve / write |
| Provenance and assumptions end to end; no fabrication; clarification questions | ✅ |
| Structured logging per stage; specific error messages | ✅ |
| No exercise-specific code | ✅ guard test |
| Golden exercise, reference example, and structurally different synthetic cases | ✅ |
| Exercise solved end to end | ✅ `examples/taller1/solucion.xlsx` (548 formulas, all validated) |

## 15. Walkthrough: the exercise document end to end

**1. `extract_document_content`** returns 8 images, the question text a)–g), and 1 table (used for layout).
`word/media/image2.png` is the arrow network and `image3.png` the Actividad/Optimista/Normal/Pesimista table.

**2. `suggest_requirements_from_text`** returns time unit week, language es, and these questions:

| Part | Output(s) |
|---|---|
| a | GANTT |
| b | EARLY_LATE_TIMES + SLACK |
| c | EXPECTED_DURATION + VARIANCE + CRITICAL_PATH |
| d | AT_MOST 13 |
| e | AT_LEAST 16 |
| f | BETWEEN 16–20 |
| g | PERCENTILE 0.98 |

**3. Transcribe the figure as arcs, then `convert_arrow_network`:**

```
A 1→2  B 2→3  C 2→4  D 3→8  (dummy 3→4)  E 8→9  F 4→5  G 4→6  H 5→7  (dummy 5→6)  I 6→9  J 7→9
⇒ A:–  B:A  C:A  D:B  E:D  F:C,B (B via 3→4)  G:C,B  H:F  I:G,F (F via 5→6)  J:H
```

**4. `normalize_project_input`** returns `ok`, with these inferences: `calculation_method = PERT` (all activities have a, m, b) and requested outputs taken from the questions.

**5. PERT** (`t_e = (a + b + 4m)/6`, `σ² = ((b − a)/6)²`):

| | a | m | b | t_e | σ² |
|---|---|---|---|---|---|
| A | 1 | 2 | 3 | (1+3+8)/6 = 2 | (2/6)² = 1/9 |
| B | 1 | 3 | 5 | (1+5+12)/6 = 3 | (4/6)² = 4/9 |
| C | 2 | 3 | 10 | (2+10+12)/6 = 4 | (8/6)² = 16/9 |
| D | 2 | 5 | 8 | (2+8+20)/6 = 5 | (6/6)² = 1 |
| E | 1 | 2 | 3 | 2 | 1/9 |
| F | 1 | 1 | 1 | 1 | 0 |
| G | 1 | 1 | 1 | 1 | 0 |
| H | 1 | 3 | 5 | 3 | 4/9 |
| I | 2 | 4 | 6 | (2+6+16)/6 = 4 | 4/9 |
| J | 1 | 5 | 9 | (1+9+20)/6 = 5 | 16/9 |

**6. CPM.** Forward pass:

* A 0–2; B 2–5; C 2–6; D 5–10.
* F and G both start at max(TC C = 6, TC B = 5) = 6, so F 6–7 and G 6–7.
* H 7–10; I starts at max(7, 7) = 7, so I 7–11.
* J 10–15; E 10–12.
* Project duration = max(12, 11, 15) = **15**.

Backward pass:

* J: IL 10, TL 15. E: IL 13, TL 15. I: IL 11, TL 15.
* H: TL = IL J = 10, so IL 7. D: TL = IL E = 13, so IL 8.
* F: TL = min(IL H 7, IL I 11) = 7, so IL 6. G: TL 11, so IL 10.
* C: TL = min(IL F 6, IL G 10) = 6, so IL 2.
* B: TL = min(IL D 8, IL F 6, IL G 10) = 6, so IL 3.
* A: TL = min(IL B 3, IL C 2) = 2, so IL 0.

| | IC | TC | IL | TL | Holgura |
|---|---|---|---|---|---|
| A | 0 | 2 | 0 | 2 | **0** |
| B | 2 | 5 | 3 | 6 | 1 |
| C | 2 | 6 | 2 | 6 | **0** |
| D | 5 | 10 | 8 | 13 | 3 |
| E | 10 | 12 | 13 | 15 | 3 |
| F | 6 | 7 | 6 | 7 | **0** |
| G | 6 | 7 | 10 | 11 | 4 |
| H | 7 | 10 | 7 | 10 | **0** |
| I | 7 | 11 | 11 | 15 | 4 |
| J | 10 | 15 | 10 | 15 | **0** |

Critical path **A – C – F – H – J** (2 + 4 + 1 + 3 + 5 = 15).

**7. Variance.** σ² = 1/9 + 16/9 + 0 + 4/9 + 16/9 = **37/9 ≈ 4.1111**; σ = √(37/9) ≈ **2.0276**.

**8. Probabilities** (exact; table in brackets):

* d) Z = (13 − 15)/2.0276 = −0.9864 → P(T ≤ 13) = **0.1620** [Z −0.99 → 0.1611]
* e) Z = (16 − 15)/2.0276 = 0.4932 → P(T ≥ 16) = 1 − 0.6891 = **0.3109** [1 − 0.6879 = 0.3121]
* f) Z₂ = (20 − 15)/2.0276 = 2.4660 → P(16 ≤ T ≤ 20) = 0.9932 − 0.6891 = **0.3041** [0.9932 − 0.6879 = 0.3053]
* g) Z = Φ⁻¹(0.98) = 2.0537 → T = 15 + 2.0537 × 2.0276 = 19.164 [table Z 2.06 → 19.177] → contract **20 weeks**

**9. `generate_solution_workbook`** produces sheets Enunciado · Datos · PERT · CPM · Gantt · Probabilidad · Resultados. There is no Red AON sheet, because the exercise does not ask for the network.

* 548 live formulas, 548 evaluated, 548 matching the engine, 23 conditional-format rules.
* The independent calculation checks pass (11/11).
* The result is in `examples/taller1/`, with the reference PERT example and crashing problem 2 alongside it.

## 16. Known limitations

* The LLM orchestration prompt is documented (§8 and the `solve_exercise_workflow` prompt) but not implemented. Visual transcription of images is the LLM's job; OCR is only a hint.
* With several critical paths, the reference material gives no rule for project variance. The default (largest-variance path) is documented and configurable.
* A Gantt cell with a fractional start or finish is shaded whole; `partial_columns` lists these cells.
* The network layout is layered (longest level). Arcs spanning several columns are routed through virtual nodes in the free space between boxes. Column orders are chosen by exact crossing minimisation when the number of combinations is ≤ 40 000 (every exercise-sized network); above that, barycenter sweeps + transpositions are used, which are good but not guaranteed optimal. One critical path is pinned to a single height so it is drawn as a straight line.
* PDF text extraction needs the `pdf` extra. Scanned PDFs need image extraction and visual reading.
