# Roadmap / issue backlog

Issue-ready follow-ups for turning this fork from a strong artifact into a cleaner public benchmark.
The A-labels are historical arm IDs; this file is ordered by what would change the conclusion most, soonest.

**Disclosure:** Bonfire ([bonfiredb.dev](https://bonfiredb.dev)) is the author's product; the planned A6/A7
arms (referenced in the README) and several follow-ups here test its design hypotheses. We pre-commit to
publishing results either way.

## Framing: a portfolio of bets on one axis

The final report establishes one lever (query-aware bounded selection into context) and several nulls
(tool count, payload coaching, reasoning effort, out-of-context compute at matched budget). What it does
not establish is **where the selection work should live**. The arms below deliberately spread across that
axis — call it the **substrate legibility ladder**:

1. Raw FHIR + silent failures (A0 — measured, fails on overflow)
2. Raw FHIR + honest, corrective errors (A12 — does the agent fix its own queries when told what broke?)
3. Raw FHIR + described capabilities and local conventions (A10-CAT — does the agent plan better when it can read the map?)
4. Compiled query-aware packets (A6/A7 — the substrate does the selection)
5. Governed read layer with handles, citations, and policy (A7+ — the substrate does selection *and* enforcement)

Plus one orthogonal axis: **inference-time scaling** (A13) — more steps, smaller reads, verifier loops,
loop-until-goal — which can be layered on any rung. We do not currently know which rung (or which
combination of rung + harness) is the right bet, and external evidence cuts both ways: MedAgentBench v2's
only cleanly isolated gain (+7pp) is corrective memory — a feedback mechanism, rung 2; the RL paper
(arXiv:2605.14126) learns rung-4 selection into model weights; ACIE's hospital deployment (arXiv:2606.19602)
is rungs 4–5 with iterative preview. These arms are designed so the benchmark can adjudicate between rungs
instead of assuming one. Run cheap and decisive first; report every arm on the same token/cost/failure ledger
so accuracy gains are never conflated with budget gains.

## 1. A6: run the query-aware in-context projection arm

**Question:** Can an in-context projection layer match the sandbox when it selects data by question intent?

**Scope:**
- Fetch only the resource types and date ranges implied by the question.
- Keep first-and-last values for temporally ordered questions.
- Deduplicate repeated resource requests across turns.
- Reuse the same 409-question split and trustworthy grading.
- Use `a6_packet_builder.py` to generate frozen packets from non-gold, metadata-assisted question fields, with `--plan-only` review before live Medplum fetches.
- Optionally run the answering step through `codex_harness.py --mode packet` with frozen A6 packets so Codex subscription runs are logged as a named substrate.
- **Serialization ablation (new):** render the same packet in 2–4 formats (raw JSON, markdown table, clinical narrative, chronological timeline) on a subset. Serialisation alone has moved medication-reconciliation F1 by up to ~19 points on ≤8B models while raw JSON won at 70B (arXiv:2604.21076) — packet *format* is a config variable, not a given, and it likely interacts with model size.
- **Coverage-first planning (new):** before fetching, expose a `getDataCoverage`-style summary (per-type counts, date ranges, top categories — numbers, not records) so the packet plan is made against what exists. Log whether plans grounded in coverage differ from blind plans.

**Acceptance:**
- Add an A0'' result table next to A0/A0'/A5.
- Report overflow, resource-real, and pooled accuracy.
- Include paired tests against A0' and A5.
- Commit or publish packet manifests with source query paths, resource IDs, and packet hashes.
- Publish hashes/manifests and redacted scorer inputs for Codex-substrate runs; keep raw prompts/events under ignored `runs/` or a reviewed artifact package.
- Report the serialization sub-table separately (exploratory unless pre-registered).

## 2. A12: error fidelity — does an honest substrate make the agent smarter? (new)

**Question:** When the server *tells the agent what went wrong* — instead of silently ignoring unknown
parameters or converting upstream failures into empty bundles — does the agent recover, and how much
accuracy does that recovery buy?

This is the cheapest untested rung on the legibility ladder, and both our own decomposition and external
evidence say it is live: 53% of A0' residual overflows re-requested the *same* resource type (agent-loop
waste that better feedback could break); 82 of 107 fit-but-wrong answers said "cannot find / truncated"
(the agent knew something was missing but had no signal about *what* or *why*); FHIR-AgentBench's error
analysis shows wrong-resource-type and over-restrictive queries as dominant failures; and MedAgentBench v2's
corrective memory — the one component of its +28pp bundle that was cleanly isolated (+7pp) — is exactly
error-driven self-correction.

**Scope:**
- Three server-behavior conditions on the same tool surface, same questions, same model:
  1. **Silent-ignore (status quo):** unknown/unsupported search params dropped without notice; upstream failures surfaced as empty results.
  2. **Lenient + warning:** FHIR-native `search.mode="outcome"` OperationOutcome entries naming each ignored parameter, in-band, without failing the request.
  3. **Strict + corrective:** `Prefer: handling=strict` honored — unknown params and unsupported modifiers return 400 + OperationOutcome listing the supported parameter set for that resource type, with *semantically safe* suggestions only (never alias `date` → `_lastUpdated`; say honestly that clinical-date search is unsupported and what `_lastUpdated` actually filters).
- Implement as a toggle on `treatment_mcp_server.py` so A9's matrix can carry it; no new substrate needed.
- Measure the *mechanism*, not just the outcome: rejected-call recovery rate (does the next call fix the problem?), wasted-call rate, same-type re-request rate, turns-to-first-useful-payload, and the fraction of wrong answers attributable to silently dropped filters.
- This arm doubles as the empirical companion to the HealthClaw error-fidelity issue set (umbrella + sub-issues A–G drafted in `medplum/.scratch/healthclaw-issues/`, grown out of medplum/medplum#9616): those issues argue the guardrail layer should preserve truth on the failure path; this arm measures what that truth-preservation is worth in agent accuracy. If the effect is real, the issue set carries its own evidence.

**Acceptance:**
- Paired three-condition table on a pre-registered slice before any full run.
- Failure taxonomy split: query-rejected-and-recovered / query-rejected-not-recovered / silently-wrong (the dangerous class — wrong answers shaped like right ones).
- An explicit verdict sentence: "corrective errors recover X of the Y failures that packets solve by construction" — this is the number that locates the right rung of the ladder.

## 3. A7: run the governed read-layer proxy

**Question:** Does a governed read-layer proxy match or beat the sandbox proxy without arbitrary code?

**Scope:**
- Build frozen packets from governed reads: query-aware selection, reference resolution, code resolution, date-window handling, first/last preservation, source citations, and denial/insufficiency metadata.
- Use `a7_packet_builder.py` to generate those packets from the A6 primary search plan plus deterministic reference expansion.
- **Result handles + external workspace (new):** large result sets stay server-side behind opaque handles; the model sees schema, counts, small samples, and computed projections. The A0' residual-overflow decomposition showed multi-turn *accumulation* (97 overflows, max single block 24.8k) is the failure mode — handles eliminate accumulation structurally, and the data plane never transits the model (a governance win in itself). Maintain an external evidence ledger (resolved concepts, handles, rejected hypotheses, calculations, support status) so context carries state summaries, not payload history.
- **Coverage tool (new):** `getDataCoverage(patientId)` returning counts/ranges per resource type — planning input, not evidence.
- **Principal-aware capability projection (new):** the read contract should be answerable per caller — what this principal, for this patient context and purpose, may ask of this endpoint — with explicit denial semantics distinct from empty results. Treat this as spec-level in A7 and measured in A14.
- Treat this as a proxy until it has product guarantees such as policy gates, audit trail, FHIRPath/field projection, capability negotiation, and explicit access-denial semantics.
- Keep the answering substrate fixed while comparing A6 vs A7 packets.
- Report packet size, source-resource count, overflow rate, and cost/token footprint next to accuracy.
- Use `codex_harness.py --mode packet` for a Codex-substrate pilot before any expensive API-key full run.

**Acceptance:**
- Add an A7 result table against A0/A0'/A5/A6.
- Include packet-level SHA-256 hashes, source query paths, reference-resolution manifests, citations, terminology summaries, and resource-ID manifests.
- Show whether A7 improves accuracy by better evidence or merely by changing answer instructions.
- For the handle variant: report answers-from-handles vs answers-from-inlined-packets at matched information content.

## 4. A13: inference-time scaling — steps, verifiers, and loops (new)

**Question:** If accuracy is the claim and tokens are not, how much do more steps, smaller reads, and
maker≠checker loops buy on top of any given substrate rung?

Everything agentic-development practice suggests — reviewer agents, loop-until-goal, bounded small reads
with more turns — is an untested axis here. Our own report warns exactly how this can fool us: the
code-interpreter "win" was a budget artifact, so every A13 contrast must be reported at matched *and*
unmatched budget, as a cost–accuracy frontier rather than a single number.

**Scope, as independently togglable harness variants over a fixed substrate (start with A6 packets and the generic MCP arm):**
- **Chunked bounded reading:** cap each retrieval at a small size and allow more turns — trade turns for fit. (The A0' decomposition predicts this helps only if re-request dedup comes with it.)
- **Verifier loop (maker≠checker):** a second pass that checks the drafted answer's citations against the evidence (deterministic A10-CITE where possible, model-judge where not) and triggers one bounded retry with the failure reason. This is the judge-reliability insight applied at inference time — and the deterministic version is the trustworthy one.
- **Goal-conditioned looping:** iterate until the evidence policy is satisfied (answer supported by cited resources, or explicit insufficiency declared), with a hard turn cap.
- **Best-of-N with panel selection:** N independent attempts, panel picks — the expensive upper bound, run on a subset only, to see what headroom exists.
- Pre-register the primary contrast (verifier-retry vs single-pass at matched substrate); label the rest exploratory.

**Acceptance:**
- A cost–accuracy frontier plot per harness variant: accuracy vs total tokens and vs wall-clock turns.
- Paired effects at matched token budget (the honest comparison) alongside unmatched (the practical one).
- Failure-mode attribution: does the verifier catch citation-unsupported answers, or just add spend?
- An explicit statement of where extra inference spend substitutes for substrate quality and where it cannot (e.g., no amount of looping recovers silently dropped filters — that is A12's rung).

## 5. A10: structured clinical operators

**Question:** Which deterministic clinical operators actually move accuracy beyond query-aware packet selection?

**Scope:**
- Add typed query planning (`A10-QP`) that maps question intent to resource type, code/date filters, and evidence shape before retrieval. **Concretize as a typed intermediate representation** — scope (patient/cohort), intent (lookup/timeline/comparison/aggregation/causal-evidence/summary), concepts with code bindings and local-mapping flags, temporal window (absolute or event-relative), operations, evidence policy, and hard limits (max resources/documents/depth/execution time) — so the model proposes intent, a deterministic compiler owns FHIR mechanics, and a verifier owns evidence requirements. The IR is also where principal/purpose (A14) and limits attach naturally.
- Add a **clinical semantic catalog** (`A10-CAT`, new): a searchable layer over CapabilityStatements, SearchParameters, profiles/extensions, terminology systems, *local* code-to-standard mappings, resource counts and date coverage, common reference paths, and human-approved example queries + prior successful traces. Enterprise text-to-SQL is the evidence base: LinkedIn's deployed system reports ~9% vs ~48% of responses rated correct-or-close (LLM/expert-judged — not independently verified "usable queries") for schema-only vs full-knowledge-graph context, with example queries and richer table attributes contributing most (arXiv:2507.14372); CHESS reaches the same conclusion (retrieve entities/metadata first, then a minimal sufficient schema). The catalog is also the direct fix for documented FHIR-AgentBench failures — e.g., teach the agent "this server codes respiratory rate as local `220210`, not only LOINC `9279-1`." Note the catalog is *learnable per-deployment data*, which makes it a compounding asset rather than commodity code.
- Add Observation/code normalization (`A10-OBS`) so display strings, LOINC/SNOMED/RxNorm codes, and source table aliases converge before the model sees evidence.
- Add deterministic reducers (`A10-AGG`) for first/latest/min/max/count/nearest/date-window operations instead of asking the model to infer them from long lists.
- Add citation verification (`A10-CITE`) that rejects answers whose cited source IDs do not support the final claim.
- Add SQL-on-FHIR / evidence-card projection (`A10-VIEW`) where a ViewDefinition/FHIRPath-like plan can produce compact tabular evidence.
- Keep each operator independently togglable; do not ship a bundled "A10" headline until ablations show which pieces matter.

**Acceptance:**
- Report A6 vs A7 vs each A10 component using identical answer substrate and grading.
- For A10-CAT: paired catalog-vs-no-catalog contrast at matched tool surface (this is rung 3 of the ladder measured directly).
- Publish per-question operator traces: plan, selected resources, reducer output, evidence cards, citations, and insufficiency flags.
- Separate token/cost gains from accuracy gains; a token win alone is not a product claim.

## 6. A10-VEC: hybrid clinical memory for notes and long text

**Question:** Does hybrid retrieval help on fuzzy note/long-text questions without harming exact structured questions?

**Scope:**
- Build a BM25 + vector + rerank sidecar over notes, long DocumentReferences, narrative fields, and other longer clinical text.
- Apply hard filters first: patient, tenant, encounter/date window, resource type, code where available, and permission boundary.
- Return cited chunks/snippets as evidence cards with source-resource IDs and freshness metadata.
- Use the **structured-narrowing-then-semantic order** (narrow deterministically → small candidate set → semantic search inside candidates → passage-level read), and the ACIE preview pattern (search result → query-focused preview → full evidence read) rather than one flat vector index; ACIE's hospital deployment also found heterogeneous-JSON tool inputs caused patient-specific malformed calls that a compact markdown representation eliminated — the model-facing representation is part of the design, with raw JSON kept behind the handle.
- Run only on question classes that need text/fuzzy concepts; structured Observation/medication/count/date questions should prefer deterministic operators.

**Acceptance:**
- Report text/fuzzy strata separately from structured strata.
- Track retrieval precision/recall, citation support rate, PHI boundary checks, chunk count, and token/cost footprint.
- Report whether vector memory improves recall without silently replacing structured evidence.

## 7. A8: skills-only falsification

**Question:** Does a FHIR skill help when the returned clinical packet is byte-identical?

**Scope:**
- Run frozen-packet arms with base prompt, neutral length pad, placebo prompt, and FHIR retrieval playbook.
- Use the same model, same packet hashes, same answer schema, and same grading.
- Use `run_a8_skill_matrix.py` so neutral/placebo controls are generated and hashed under the run directory.
- Use `codex_collect_results.py` to convert each Codex run directory back into `score_taxonomy.py` input JSON.
- Pre-register primary contrasts and sample size; report cluster-aware CIs and family-wise correction, or label the run exploratory.

**Acceptance:**
- Report paired skill-vs-placebo and skill-vs-length-pad effects.
- If the skill only beats the short baseline, label it as prompt-length/placebo sensitive.
- If the skill survives controls, keep it as a thin task-playbook layer over the governed read layer.

## 8. A9: Codex + MCP/tools substrate

**Question:** Do skills compound with an MCP tool surface in the actual agent interface?

**Scope:**
- Run four live-tool arms: generic FHIR MCP, generic FHIR MCP + skill, expanded read-tool catalog MCP proxy, expanded read-tool catalog MCP proxy + skill.
- Carry the A12 error-fidelity toggle through this matrix where cheap (strict vs silent conditions on the generic arm) so delivery-layer and legibility effects can be separated.
- Add a follow-up issue to replace the expanded catalog proxy with a real governed read-contract MCP tool before making product claims.
- Register the local tool server with Codex (`codex mcp add bonfire-eval --url http://127.0.0.1:8765/mcp`) and run `run_a9_mcp_matrix.py`.
- Use `--start-server` for live runs so each arm starts `treatment_mcp_server.py` with the correct `TOOL_SUBSET`.
- Use `codex_collect_results.py` to convert each Codex run directory back into `score_taxonomy.py` input JSON.
- Record Codex CLI version, configured MCP server name/URL, selected `TOOL_SUBSET`, treatment-server source hash, skill hash, prompt hash, event-log paths, and final answers. Add live `tools/list` schema hashing before making product claims.
- Treat subscription-backed Codex as a named substrate; do not mix it into raw API cost tables without labeling.
- Pre-register primary contrasts and sample size; report cluster-aware CIs and family-wise correction, or label the run exploratory.
- MCP-vs-CLI remains a *delivery-layer ablation over the same governed broker*, not the headline question.

**Acceptance:**
- Separate product/tooling value from skill value:
  - Expanded read-tool catalog proxy vs generic MCP.
  - Generic MCP + skill vs generic MCP.
  - Expanded read-tool catalog proxy + skill vs expanded read-tool catalog proxy.
- Report retrieval precision/recall, actual MCP-returned resource IDs, payload bytes/tokens, repeated-call rate, failure taxonomy, and answer accuracy.

## 9. A14: identity and authorization axis (new)

**Question:** Do identical questions behave differently — in accuracy, disclosure, and recovery — when
asked by different principals, and can the layer enforce that difference without wrecking utility?

No published FHIR-agent benchmark measures this: MedAgentBench, FHIR-AgentBench, PhysicianBench, and
HealthAgentBench are all single-identity with effectively admin credentials (as are our own runs to date).
Yet every production deployment pattern (Epic/Oracle embedded assistants, SMART scopes, HealthClaw's
step-up model) is identity-first. This is the eval's most defensible unclaimed territory.

**Scope:**
- Run a shared task slice under three principals: patient (own record only), clinician (panel scope), backend service (population scope, aggregate-only), with scopes actually enforced at retrieval (AccessPolicy / SMART scopes), not in the prompt.
- Measure: answer accuracy per principal, **unnecessary-disclosure rate** (resources retrieved beyond what the answer needed and the principal's scope licenses), denied-call recovery (does the agent adapt to a denial, or thrash?), and filter-in-prompt vs filter-at-retrieval leakage under prompt-injection attempts (the ABAC-at-retrieval safety design from the original eval plan — a safety property independent of accuracy).
- Explicit denial semantics (from A7/A12) are prerequisite plumbing: "denied" must be distinguishable from "empty."
- Multi-tenant/aggregate extension (cohort queries under an aggregate-only principal with small-cell suppression) stays a labeled feasibility spike — the fair cohort baseline is bulk `$export` → SQL, and aggregate-ABAC is under-testable on 100 single-tenant demo patients; do not over-claim from this substrate.

**Acceptance:**
- Per-principal result table with disclosure and recovery metrics alongside accuracy.
- A leakage table: filter-in-prompt vs filter-at-retrieval under adversarial questions (expected: ≥1 vs 0).
- Honest scoping note about what a 100-patient single-tenant corpus can and cannot support.

## 10. A11: graph and timeline retrieval

**Question:** Do explicit temporal/reference graphs improve long-horizon, multi-call clinical tasks beyond flat packets?

**Scope:**
- Build a patient-scoped event timeline with typed edges: Encounter -> Observation, MedicationRequest -> Medication, Procedure -> Encounter, Condition -> evidence.
- Add graph/path retrieval for questions that need chains, sequencing, or "around this event" context.
- Keep traversal **bounded and typed** (allowed paths, allowed resource types, max depth, max resources — no generic traverse-everything tool); score edges best-first and stop when the evidence requirement is satisfied, preserving the traversal path for provenance.
- **Lazy node views (tree-walk variant):** the agent is never handed a full raw resource — each visited node returns a compact typed summary plus reference stubs (type, id, one-line description), and the agent asks for specific fields when it needs them. Pair with a visited-set so re-visits return "already read" instead of re-inlining. Our existing data says the *naive* version of this fails: A0's multi-turn retrieval is already iterative and it accumulated to overflow (97 residual overflows with no single block >24.8k; 53% same-type re-requests), and Exp 3 showed traversal without query-awareness fetches 448 resources to capture 2 gold ones. Tree-walking is a live arm precisely when combined with compact node views + dedup + a stopping criterion — test it as that combination, not as bare reference-following.
- Test on FHIR-AgentBench-style questions first, then extend to long-horizon tasks where multi-turn accumulation dominates.

**Acceptance:**
- Report whether graph/timeline retrieval reduces repeated calls, residual overflow, and date-order errors.
- Include path citations, not just resource citations.
- Keep graph retrieval separate from vector memory so failures can be attributed.

## 11. Re-grade the RL result with trustworthy grading (new)

**Question:** Does the reported 77% of the RL-post-trained agent (arXiv:2605.14126, Qwen3-8B CodeAct on
FHIR-AgentBench, vs 50% prompt-only o4-mini) survive deterministic + panel grading?

Their number is **not directly comparable to ours**: different split, harness, model, and turn budget, and
their evaluation uses a physician-audited judge (a stronger setup than the benchmark's default). The honest
framing is a comparability + grading-robustness check, not a suspicion of their result: put their outputs and
ours through one identical grading pipeline (deterministic-numeric + multi-family panel) on the matched
question set. If their number survives, it is the strongest learned-selection result in the field and the
A6/A7 comparison target; if it shifts, that quantifies how much judge choice moves the field's newest
headline — either outcome is a citable contribution, and neither requires training anything.

**Scope:**
- Obtain their released outputs (or re-run their released agent) on the paired 409 split.
- Apply the deterministic-numeric + multi-family panel pipeline unchanged (`build_labels.py` → `final_grade.py`).
- Compare on matched questions and matched grading; report their-grading vs our-grading deltas.
- Frame respectfully: this is a grading-robustness check, not a takedown; their result is directionally consistent with our thesis (the lever is selection, learned or engineered).

**Acceptance:**
- A their-number vs our-grading table with agreement statistics.
- A short note usable in RELATED_WORK either way.

## 12. Enterprise-access conformance map + enforcement-surface audit (new, no-model)

**Question:** What does each real substrate actually offer an agent — capabilities, authz, error behavior,
approval mechanics — before any model spends a token?

Two zero-model-token artifacts that double as benchmark design inputs and standalone publishable pages:
- **Conformance map:** for Medplum, HAPI, AWS HealthLake, Epic (public sandbox), Oracle (public docs): supported search params/operations, CapabilityStatement truthfulness (does /metadata match behavior?), error-handling behavior under unknown params/strict mode (the A12 conditions, probed per server), authz model, bulk/export paths. National context from ASTP briefs 75/81 (FHIR is the sanctioned app layer, not the dominant integration mechanism) frames why per-endpoint variance matters — there is no single "FHIR" any agent targets.
- **Enforcement-surface audit:** code-level comparison of AWS HealthLake MCP (11 tools, IAM-delegated, one read-only boolean, no capability contract), HealthClaw Guardrails (29 tools, step-up writes, HITL, redaction), Medplum MCP (3 tools inheriting AccessPolicy via ctx.repo), and the four surveyed OSS servers (jcafazzo/momentum/wso2/xSoVx — wso2's per-resource `get_capabilities` is the closest existing describe()). Most of this audit already exists from the 2026-07-10/11 reviews; every claim carries a primary-source citation.
- A later **two-plane arm** (same questions over live FHIR vs bulk `$export` → SQL) is the honest first step toward multi-plane realism — real substrates, deterministic ground truth. A five-plane synthetic estate (HL7v2/C-CDA/claims/OMOP) is explicitly deferred: hand-authored data + self-authored gold is the self-graded criticism squared.
- **Representation-variance research (new):** document how the *same clinical fact* is stored differently across real implementations — local codes vs LOINC/SNOMED (the FHIR-AgentBench `220210` respiratory-rate failure), discrete Observation vs narrative-only DocumentReference, choice-type divergence (`valueQuantity` vs `valueString`), extension use where base fields exist, profile variance (base R4 vs US Core vs none), and unit non-uniformity. This is why US Core/IPS profiles exist and why a CapabilityStatement alone is not a truthful contract. Survey the published evidence + probe the public sandboxes; the output feeds A10-CAT (what the catalog must learn per deployment) and bounds what any "FHIR-aware" agent can promise. An agent that is *aware of these limits* — and says "this server may store X in Y" or "not findable in discrete data" — is itself a testable condition (fold into A12's honest-substrate framing: honesty about representation, not just about errors).

**Acceptance:**
- Published map + audit with per-claim citations and probe scripts committed.
- The A12 strict/lenient probe results per server (deterministic, re-runnable).

## 13. Publish a reproducibility artifact package

**Question:** How can a fresh checkout recompute the final table without committing giant raw dumps?

**Scope:**
- Create a minimized answer-level artifact with only fields required for scoring.
- Include SHA-256 checksums for any external raw dumps.
- Document exactly which scripts require local raw answer files.

**Acceptance:**
- `python a0prime_verdict.py` runs from a clean checkout after fetching the artifact package.
- `FINAL_REPORT.md` links to artifact checksums and commands.

## 14. Rerun A0, A0', and A5 on one substrate

**Question:** Does the A0' conclusion survive when all three arms run against the same Medplum instance?

**Scope:**
- Fresh-load the MIMIC-IV-on-FHIR demo once.
- Run all three arms against that same instance.
- Preserve answer dumps and per-question resource IDs.

**Acceptance:**
- Replace cross-substrate caveat with same-instance evidence.
- Recompute UUID/Jaccard parity as a sanity check, not the main proof.

## 15. Add cross-family or human adjudication for A0' non-numeric labels

**Question:** Are A0' non-numeric labels stable outside the codex-only panel?

**Scope:**
- Rejudge the A0' non-numeric real answers with an independent model family or human review.
- Compare agreement against the existing codex panel.

**Acceptance:**
- Add an A0' judge-family agreement table.
- Update the A0' conservative-lower-bound caveat.

## 16. Judge re-measurement (before re-asserting the judge-reliability headline)

**Question:** Does the 61% single-judge figure survive an on-spec, apples-to-apples re-measurement — and how
does the benchmark's actual shipped default judge score?

**Scope:**
- Run the upstream shipped default judge (o4-mini) through `judge_leaderboard.py` on the same 111 numeric
  arm-answers (we never measured it).
- Re-run gpt-5-mini on-spec: include the question text, as the benchmark's own pipeline does — our original
  invocation omitted it.
- Equalize (or ablate) the numeric-tolerance coaching and the side-by-side/both-arms prompt format between
  single judges and panels, so 61% vs 98–99% is not confounded by invocation differences.

**Acceptance:**
- An updated judge leaderboard with o4-mini and on-spec gpt-5-mini rows.
- The judge-reliability headline in README/FINDINGS/TRUSTWORTHY_REGRADE re-asserted or softened to match the
  measured numbers.

## 17. Run a projection cap sweep

**Question:** How sensitive is blunt projection to the recency cap?

**Scope:**
- Sweep cap values such as 10, 25, 50, 100, and first+last variants.
- Track residual overflow versus data-drop errors.

**Acceptance:**
- Add a cap curve: accuracy, residual overflow, and fit-but-wrong counts.
- Replace cap=50-only language with measured cap sensitivity.

## 18. Add a tracked failure-decomposition script

**Question:** Can every A0' decomposition number be regenerated by one command?

**Scope:**
- Generate qid-level categories: correct, still-overflow, cap-drop, earliest/first, repeated-resource overflow.
- Emit JSON and Markdown summaries.

**Acceptance:**
- `python decompose_a0prime_failures.py` regenerates the numbers in `FINAL_REPORT.md`.
- The report cites the generated artifact directly.

## 19. RELATED_WORK additions (new citations, doc-only)

Fold into `RELATED_WORK.md` with pattern assignments:
- **RL for tool-calling agents in FHIR** (arXiv:2605.14126, May 2026) — learned rung-4 selection; 77% claim pending grading-robustness check (item 11).
- **Serialisation Strategy Matters** (arXiv:2604.21076) — packet format as a lever; interacts with model size.
- **LLMonFHIR** (JACC Advances 2025, Stanford Spezi) — physician-validated (210 responses, median 5/5) patient-facing RAG+function-calling over FHIR; clinical-venue evidence for retrieval-over-stuffing, and a patient-identity data point for A14.
- **ACIE** (arXiv:2606.19602) — University Medicine Essen hospital deployment, 96.5% clinician acceptance over 7,326 verified judgments (acceptance includes correct abstentions; no architecture ablation). Cite as deployment-existence evidence for iterative search + previews + citations at production scale — not as component-level evidence that any specific mechanism causes the accuracy.
- **HealthAgentBench** (Microsoft, arXiv:2606.31179) and **PhysicianBench** (Stanford HealthRex, arXiv:2605.02240) — the long-horizon/terminal-task end of the landscape; both single-identity, which motivates A14.
- **Text-to-SQL for Enterprise Data Analytics** (LinkedIn, arXiv:2507.14372) + **CHESS** (arXiv:2405.16755) + **Spider 2.0** (arXiv:2411.07763, 21.3% best) — the enterprise structured-data evidence base for A10-CAT and for honest difficulty calibration.
- **MCP-FHIR framework** (arXiv:2506.13800) — cite-only; framework description, no quantitative eval, no authz treatment.
- Artifacts, not papers: AWS HealthLake MCP server (awslabs), HealthClaw Guardrails, Medplum MCP — covered by item 12's audit.
