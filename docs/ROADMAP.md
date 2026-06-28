# Roadmap / issue backlog

Issue-ready follow-ups for turning this fork from a strong artifact into a cleaner public benchmark.
The A-labels are historical arm IDs; this file is ordered by what would change the conclusion most, soonest.

## 1. A6: run the query-aware in-context projection arm

**Question:** Can an in-context projection layer match the sandbox when it selects data by question intent?

**Scope:**
- Fetch only the resource types and date ranges implied by the question.
- Keep first-and-last values for temporally ordered questions.
- Deduplicate repeated resource requests across turns.
- Reuse the same 409-question split and trustworthy grading.
- Use `a6_packet_builder.py` to generate frozen packets from non-gold, metadata-assisted question fields, with `--plan-only` review before live Medplum fetches.
- Optionally run the answering step through `codex_harness.py --mode packet` with frozen A6 packets so Codex subscription runs are logged as a named substrate.

**Acceptance:**
- Add an A0'' result table next to A0/A0'/A5.
- Report overflow, resource-real, and pooled accuracy.
- Include paired tests against A0' and A5.
- Commit or publish packet manifests with source query paths, resource IDs, and packet hashes.
- Publish hashes/manifests and redacted scorer inputs for Codex-substrate runs; keep raw prompts/events under ignored `runs/` or a reviewed artifact package.

## 2. A7: run the governed read-layer proxy

**Question:** Does a governed Bonfire-style read-layer proxy match or beat the sandbox proxy without arbitrary code?

**Scope:**
- Build frozen packets from Bonfire-style reads: query-aware selection, reference resolution, code resolution, date-window handling, first/last preservation, source citations, and denial/insufficiency metadata.
- Use `a7_packet_builder.py` to generate those packets from the A6 primary search plan plus deterministic reference expansion.
- Treat this as a proxy until it has product guarantees such as policy gates, audit trail, FHIRPath/field projection, capability negotiation, and explicit access-denial semantics.
- Keep the answering substrate fixed while comparing A6 vs A7 packets.
- Report packet size, source-resource count, overflow rate, and cost/token footprint next to accuracy.
- Use `codex_harness.py --mode packet` for a Codex-substrate pilot before any expensive API-key full run.

**Acceptance:**
- Add an A7 result table against A0/A0'/A5/A6.
- Include packet-level SHA-256 hashes, source query paths, reference-resolution manifests, citations, terminology summaries, and resource-ID manifests.
- Show whether A7 improves accuracy by better evidence or merely by changing answer instructions.

## 3. A10: structured clinical operators

**Question:** Which deterministic clinical operators actually move accuracy beyond query-aware packet selection?

**Scope:**
- Add typed query planning (`A10-QP`) that maps question intent to resource type, code/date filters, and evidence shape before retrieval.
- Add Observation/code normalization (`A10-OBS`) so display strings, LOINC/SNOMED/RxNorm codes, and source table aliases converge before the model sees evidence.
- Add deterministic reducers (`A10-AGG`) for first/latest/min/max/count/nearest/date-window operations instead of asking the model to infer them from long lists.
- Add citation verification (`A10-CITE`) that rejects answers whose cited source IDs do not support the final claim.
- Add SQL-on-FHIR / evidence-card projection (`A10-VIEW`) where a ViewDefinition/FHIRPath-like plan can produce compact tabular evidence.
- Keep each operator independently togglable; do not ship a bundled "A10" headline until ablations show which pieces matter.

**Acceptance:**
- Report A6 vs A7 vs each A10 component using identical answer substrate and grading.
- Publish per-question operator traces: plan, selected resources, reducer output, evidence cards, citations, and insufficiency flags.
- Separate token/cost gains from accuracy gains; a token win alone is not a product claim.

## 4. A10-VEC: hybrid clinical memory for notes and long text

**Question:** Does hybrid retrieval help on fuzzy note/long-text questions without harming exact structured questions?

**Scope:**
- Build a BM25 + vector + rerank sidecar over notes, long DocumentReferences, narrative fields, and other longer clinical text.
- Apply hard filters first: patient, tenant, encounter/date window, resource type, code where available, and permission boundary.
- Return cited chunks/snippets as evidence cards with source-resource IDs and freshness metadata.
- Run only on question classes that need text/fuzzy concepts; structured Observation/medication/count/date questions should prefer deterministic operators.

**Acceptance:**
- Report text/fuzzy strata separately from structured strata.
- Track retrieval precision/recall, citation support rate, PHI boundary checks, chunk count, and token/cost footprint.
- Report whether vector memory improves recall without silently replacing structured evidence.

## 5. A8: skills-only falsification

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
- If the skill survives controls, keep it as a thin task-playbook layer over the Bonfire read layer.

## 6. A9: Codex + MCP/tools substrate

**Question:** Do skills compound with an MCP tool surface in the actual agent interface?

**Scope:**
- Run four live-tool arms: generic FHIR MCP, generic FHIR MCP + skill, expanded read-tool catalog MCP proxy, expanded read-tool catalog MCP proxy + skill.
- Add a follow-up issue to replace the expanded catalog proxy with a real Bonfire read-contract MCP tool before making product claims.
- Register the local tool server with Codex (`codex mcp add bonfire-eval --url http://127.0.0.1:8765/mcp`) and run `run_a9_mcp_matrix.py`.
- Use `--start-server` for live runs so each arm starts `treatment_mcp_server.py` with the correct `TOOL_SUBSET`.
- Use `codex_collect_results.py` to convert each Codex run directory back into `score_taxonomy.py` input JSON.
- Record Codex CLI version, configured MCP server name/URL, selected `TOOL_SUBSET`, treatment-server source hash, skill hash, prompt hash, event-log paths, and final answers. Add live `tools/list` schema hashing before making product claims.
- Treat subscription-backed Codex as a named substrate; do not mix it into raw API cost tables without labeling.
- Pre-register primary contrasts and sample size; report cluster-aware CIs and family-wise correction, or label the run exploratory.

**Acceptance:**
- Separate product/tooling value from skill value:
  - Expanded read-tool catalog proxy vs generic MCP.
  - Generic MCP + skill vs generic MCP.
  - Expanded read-tool catalog proxy + skill vs expanded read-tool catalog proxy.
- Report retrieval precision/recall, actual MCP-returned resource IDs, payload bytes/tokens, repeated-call rate, failure taxonomy, and answer accuracy.

## 7. A11: graph and timeline retrieval

**Question:** Do explicit temporal/reference graphs improve long-horizon, multi-call clinical tasks beyond flat packets?

**Scope:**
- Build a patient-scoped event timeline with typed edges: Encounter -> Observation, MedicationRequest -> Medication, Procedure -> Encounter, Condition -> evidence.
- Add graph/path retrieval for questions that need chains, sequencing, or "around this event" context.
- Test on FHIR-AgentBench-style questions first, then extend to long-horizon tasks where multi-turn accumulation dominates.

**Acceptance:**
- Report whether graph/timeline retrieval reduces repeated calls, residual overflow, and date-order errors.
- Include path citations, not just resource citations.
- Keep graph retrieval separate from vector memory so failures can be attributed.

## 8. Publish a reproducibility artifact package

**Question:** How can a fresh checkout recompute the final table without committing giant raw dumps?

**Scope:**
- Create a minimized answer-level artifact with only fields required for scoring.
- Include SHA-256 checksums for any external raw dumps.
- Document exactly which scripts require local raw answer files.

**Acceptance:**
- `python a0prime_verdict.py` runs from a clean checkout after fetching the artifact package.
- `FINAL_REPORT.md` links to artifact checksums and commands.

## 9. Rerun A0, A0', and A5 on one substrate

**Question:** Does the A0' conclusion survive when all three arms run against the same Medplum instance?

**Scope:**
- Fresh-load the MIMIC-IV-on-FHIR demo once.
- Run all three arms against that same instance.
- Preserve answer dumps and per-question resource IDs.

**Acceptance:**
- Replace cross-substrate caveat with same-instance evidence.
- Recompute UUID/Jaccard parity as a sanity check, not the main proof.

## 10. Add cross-family or human adjudication for A0' non-numeric labels

**Question:** Are A0' non-numeric labels stable outside the codex-only panel?

**Scope:**
- Rejudge the A0' non-numeric real answers with an independent model family or human review.
- Compare agreement against the existing codex panel.

**Acceptance:**
- Add an A0' judge-family agreement table.
- Update the A0' conservative-lower-bound caveat.

## 11. Run a projection cap sweep

**Question:** How sensitive is blunt projection to the recency cap?

**Scope:**
- Sweep cap values such as 10, 25, 50, 100, and first+last variants.
- Track residual overflow versus data-drop errors.

**Acceptance:**
- Add a cap curve: accuracy, residual overflow, and fit-but-wrong counts.
- Replace cap=50-only language with measured cap sensitivity.

## 12. Add a tracked failure-decomposition script

**Question:** Can every A0' decomposition number be regenerated by one command?

**Scope:**
- Generate qid-level categories: correct, still-overflow, cap-drop, earliest/first, repeated-resource overflow.
- Emit JSON and Markdown summaries.

**Acceptance:**
- `python decompose_a0prime_failures.py` regenerates the numbers in `FINAL_REPORT.md`.
- The report cites the generated artifact directly.
