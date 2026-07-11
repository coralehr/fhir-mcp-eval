# Adversarial roadmap review — 2026-07-11

Independent adversarial review of docs/ROADMAP.md v2, the companion site changes, and the
merge reconciliation. Reviewer: OpenAI Codex (gpt-5.6-sol, reasoning=xhigh), read-only over
this repository, both PR states, and primary sources. 37 findings, verdict REWORK — meaning:
the backlog's direction stands, but no arm may run or publish a headline until its findings
below are addressed in that arm's pre-registration. Findings are reproduced verbatim.

1. [P1] A6 uses benchmark-construction metadata as an oracle. The roadmap calls it “non-gold metadata,” but [a6_packet_builder.py](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/a6_packet_builder.py:159) reads `main_table_name` to select the exact FHIR resource type and [lines 176–218](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/a6_packet_builder.py:176) mine `val_dict` placeholders. Those annotations do not exist in a normal user query. Minimal fix: primary A6 must plan from question text, patient ID, and assumption only; report the metadata-assisted version separately as an oracle ceiling.

2. [P1] The named A6 implementation is not bounded. It adds `_count`, then calls `search_with_pagination()` and concatenates every page and complete resource into the packet ([a6_packet_builder.py:313](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/a6_packet_builder.py:313), [fhir_client.py:52](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/fhir_client.py:52)). `_count` is page size, not a result cap. Minimal fix: enforce total resource/token ceilings, server-side date/code filters, field projection, and actual first/last selection before calling this A6.

3. [P1] A6 is still a product bundle, not the decisive experiment. Item 1 combines query-aware planning, first/last preservation, deduplication, serialization changes, and coverage-first planning. A win cannot be attributed to “query-aware selection.” Split it into:

   - A6a: question-only selection, existing renderer, no coverage.
   - A6b: identical frozen evidence, serialization only.
   - A6c: identical planner, coverage summary on/off.
   - A6d: dedup on/off with identical retrieval results.

4. [P1] The serialization ablation repeats the project’s budget confound. JSON, Markdown, narrative, and timeline renderings differ in tokens, ordering, redundancy, and sometimes facts retained. “Same source packet” is not matched information or matched budget. Require deterministic lossless renderers, report token counts, and compare at a common context budget. The cited ~19 F1 result is from synthetic medication reconciliation on ≤8B models, not GPT-5.5 clinical QA; it licenses an exploratory arm, not a transferred expectation. [Primary paper](https://arxiv.org/abs/2604.21076).

5. [P1] `getDataCoverage` is evidence, not merely “planning input.” Per-type counts, date ranges, and top categories can directly answer existence/count questions or reveal the likely label. Minimal fix: make coverage a separate treatment, score direct-answer leakage, and restrict the summary to fields proven incapable of resolving the benchmark question.

6. [P1] A6 still proposes paired claims against a historical A5 run from the torn-down substrate before item 14’s same-instance rerun. Pairing question IDs does not control server version, loaded data, model snapshot, harness, or run date. The existing report explicitly disclaims strict parity ([FINAL_REPORT.md:111](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/docs/FINAL_REPORT.md:111)). Run contemporaneous A0/A0′/A5/A6 on one instance before claiming A6 matches the sandbox.

7. [P1] The roadmap repeats post-treatment strata as acceptance criteria. “Overflow” and “resource-real” are defined by A0’s observed outcome; the report already calls this post-hoc and success-selected ([FINAL_REPORT.md:52](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/docs/FINAL_REPORT.md:52)). Add pre-treatment strata based on independently tokenized record size and question class. Keep the old strata descriptive only.

8. [P1] A12’s motivation does not support A12’s mechanism. Same-type re-requests are loop/dedup failures; “cannot find/truncated” rows are mostly projection cap-drops. Neither implies an unsupported search parameter or a suppressed server error. OperationOutcome fidelity cannot restore records discarded by cap-50. Restrict A12 to rows with observed malformed, ignored, or unsupported queries; do not compare it against all packet-solved failures.

9. [P1] A12 changes error fidelity and corrective coaching simultaneously. “Strict + corrective” rejects the call, lists every supported parameter, explains semantics, and supplies suggestions; any gain could come from the embedded catalog, not strictness. FHIR only says servers should honor `Prefer: handling=strict/lenient`; it does not require this coaching bundle. [FHIR R4 search handling](https://hl7.org/fhir/R4/search.html). Minimal split:

   - Silent vs explicit error with identical diagnostic content.
   - Explicit error vs error plus supported-parameter list.
   - Supported-parameter list vs semantic suggestions.
   - Unknown-parameter handling separate from upstream/network failures.

10. [P1] The claimed A12 “status quo” does not match the named harness. [treatment_mcp_server.py:75](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/treatment_mcp_server.py:75) already returns JSON containing HTTP status and response detail; it does not generally convert failures into empty bundles. Implement and verify each treatment at the proxy boundary before calling it a three-condition server experiment.

11. [P1] A12’s pilot design invites winner’s curse. A “pre-registered slice before any full run” becomes adaptive arm selection if the same 409-question test set is later reused. There is no powered effect size, three-condition paired test, patient clustering rule, or multiplicity rule. Use a development slice to debug mechanics and an untouched confirmatory set with Cochran-Q/paired contrasts, cluster-aware intervals, and a declared primary contrast.

12. [P1] A7 cannot identify why it wins. It simultaneously adds reference expansion, terminology summaries, citations, insufficiency metadata, a read-contract prompt, handles, samples, computed projections, an evidence ledger, and policy semantics. The current builder visibly emits all of those together ([a7_packet_builder.py:182](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/a7_packet_builder.py:182)). Minimal sequence: A6 + references only; + terminology only; + citation envelope only; then handle-vs-inline using the exact same frozen evidence. Authorization belongs in A14, not the A7 accuracy headline.

13. [P1] “Handles vs inlined packets at matched information content” is undefined. A handle that permits adaptive reads exposes a conditional information channel; a static packet does not. Either replay and inline the exact handle trace after the fact, or compare handle transport against inline transport under a frozen access plan. Otherwise the arm measures transport, planning, and evidence quantity together.

14. [P1] A13’s chunked-reading arm bundles chunk size, turn budget, and dedup. Use a 2×2 design: bounded/unbounded chunks × dedup on/off, with the same maximum evidence and total token budget. Frozen A6 packets cannot test interactive chunking at all; that requires a live retrieval substrate and must not share the same headline.

15. [P1] A13’s verifier arm bundles detection, feedback, and retry. A model verifier that supplies a failure reason may simply coach the second answer; a deterministic citation check tests a different capability. Split verification-only/no-retry, retry with a generic failure bit, and retry with diagnostic feedback. Final scoring must be independent of the verifier and selector.

16. [P1] Best-of-N cannot identify inference-time scaling without selector controls. “Panel picks” measures both sampling diversity and panel quality. Report random selection, deterministic evidence-support selection, model-panel selection, and an oracle upper bound separately. Do not reuse the grading panel as the selection panel.

17. [P1] A10-CAT is another bundle: schemas, local mappings, counts, date coverage, example queries, and prior successful traces. The paired “catalog vs no catalog” contrast cannot identify which component matters. Worse, examples and successful traces learned from the same 409 questions leak templates and deployment-specific answers. Freeze the catalog from training/development patients only, prohibit test traces, and add components incrementally.

18. [P1] A10-CITE is described as deterministic when claim support is usually semantic. Checking that a cited ID exists is deterministic; checking that it supports a causal, temporal, or comparative claim is not. Separate referential validity from semantic entailment and use blinded human adjudication or an independently validated verifier for the latter.

19. [P1] A10-VEC is un-runnable on this benchmark. The 409-question split is generated entirely from structured MIMIC tables; its gold types are Observation, Encounter, Medication/MedicationRequest, Procedure, Patient, Location, and Condition—not notes or DocumentReference passages. Build and pre-register a note-grounded dataset before promising a BM25/vector accuracy result.

20. [P1] A9’s partial A12 carry-through does not separate delivery and legibility. Applying strict-vs-silent only “where cheap” on the generic arm produces an incomplete factorial. Either run error fidelity as its own experiment or cross it fully with generic/expanded tool surfaces.

21. [P1] A14 lacks principal-specific gold outcomes. A patient, panel clinician, and aggregate-only backend service are not supposed to answer identical tasks identically. Legitimate denial will be mis-scored as inaccuracy, and SMART scopes alone do not encode clinician panels or aggregate-only policy. Define authorized answer/abstention gold per principal, hold accessible evidence constant where accuracy is compared, and evaluate disclosure separately.

22. [P1] A14 pre-commits the leakage result: “expected ≥1 vs 0.” An acceptance criterion cannot require the baseline to leak and the treatment never to leak. Specify attacks, sensitive canaries, confidence intervals, and a failure threshold without dictating the observed outcome.

23. [P2] “All published benchmarks are single-identity with effectively admin credentials” remains too absolute. PhysicianBench is explicitly framed around physician workflows, and HealthAgentBench spans seven heterogeneous environments; absence of an authorization-axis evaluation does not prove admin-equivalent credentials in every environment. Scope the claim to “we found no reported principal-varying authorization evaluation.” [PhysicianBench](https://arxiv.org/abs/2605.02240), [HealthAgentBench](https://arxiv.org/abs/2606.31179).

24. [P1] Item 11’s re-grade remains mechanically impossible “unchanged.” `build_labels.py` and `final_grade.py` are hard-coded to `runs/full409`, two arms named `resource`/`code`, and the fork’s panel file shapes ([build_labels.py:28](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/build_labels.py:28), [final_grade.py:16](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/final_grade.py:16)). The RL paper evaluates 424 validation questions and different output structures. Write a generic single-arm grader adapter before putting this in acceptance criteria.

25. [P1] Item 16’s judge re-measurement is also not runnable through `judge_leaderboard.py` as written. That script scores existing cached labels; it does not invoke o4-mini or construct the equalized prompts required by the merged roadmap. The reconciliation preserved the issue text but not the executable path. Add a judge runner that freezes question inclusion, tolerance instructions, arm presentation, model version, seed, and cache schema.

26. [P1] The lazy tree-walk arm combines compact summaries, field-on-demand reads, visited-set dedup, best-first ordering, and a stopping rule. A win cannot be attributed to tree traversal. Split compact-vs-raw node representation, dedup on/off, and fixed-vs-evidence-conditioned stopping. Also stop presenting the 448-to-2 example as general evidence: it is one medication question ([retrieval_per_question.csv:56](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/medplum-eval/retrieval-metrics/retrieval_per_question.csv:56)).

27. [P1] The enterprise conformance map conflates products with deployments. Unknown-parameter behavior, supported search parameters, indexing, and CapabilityStatement completeness are version/configuration-specific. Epic/Oracle documentation-only rows are not comparable to live Medplum/HAPI probes. Publish separate “observed deployment” and “documented product” tables with version, configuration, timestamp, credentials, and raw probe artifacts.

28. [P1] Representation variance cannot be measured by probing unrelated vendor sandboxes. Different facts in different patient corpora confound representation with dataset content. Load one canonical synthetic fixture into every writable server; treat closed vendor sandboxes as qualitative case studies only.

29. [P1] The merged public roadmap still cites non-public scratch work as evidence: `medplum/.scratch/healthclaw-issues/` and unnamed “2026-07-10/11 reviews.” Public readers cannot inspect or reproduce those claims. Move the artifacts into the public repository or remove them.

30. [P1] The roadmap has no family-level statistical policy across 19 items, dozens of component toggles, multiple formats, principals, strata, models, and selectors. Per-item pre-registration does not prevent cross-item cherry-picking. Declare one primary A6 hypothesis, a hierarchical testing order, patient-clustered inference, confirmatory holdouts, and explicit exploratory labeling for everything else.

31. [P1] The ordering contradicts its own “changes the conclusion most, soonest” rule. Same-instance reruns, cross-family adjudication, reproducibility, and judge re-measurement sit behind large catalog, authz, graph, vector, and enterprise programs. These credibility repairs must precede new product-shaped headlines.

32. [P2] The “substrate legibility ladder” is not one axis. Error diagnostics, semantic catalogs, compiled selection, authorization, handles, and governance are neither nested nor monotonic. Treat them as separate factors; otherwise “moving up a rung” has no stable experimental meaning.

33. [P2] MedAgentBench v2’s isolated +7pp memory gain is real, but calling it evidence for A12 is mechanistically wrong. It is cross-episode memory synthesized from prior failures, not same-request server error fidelity. Cite it as evidence for learned corrective instructions, not strict FHIR errors. [MedAgentBench v2](https://pubmed.ncbi.nlm.nih.gov/41758153/).

34. [P1] Site authorship now conflicts with repository citation metadata. The report page and JSON-LD claim sole authorship by Aanish Sachdev/bonfireDB ([research-agent-context-fhir.html:24](/Users/aanishsachdev/Desktop/cstack/bonfire-db-site/src/fragments/research-agent-context-fhir.html:24), [agent-context-fhir.astro:15](/Users/aanishsachdev/Desktop/cstack/bonfire-db-site/src/pages/research/agent-context-fhir.astro:15)), while the repository’s [CITATION.cff:14](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/CITATION.cff:14) names CoralEHR as author. Decide whether these are distinct artifacts and say so explicitly; otherwise this is an attribution dispute waiting to happen.

35. [P1] The conflict-of-interest disclosure is in the eval roadmap, not visibly attached to the citable Bonfire report. The report is authored and published by the product whose design hypotheses it advances. Put the disclosure beside the byline or citation block; a disclosure in another repository is not adequate for page readers.

36. [P2] Self-issuing `@techreport` is not inherently invalid; non-peer-reviewed technical reports are citeable. The problem is that this entry gives a mutable marketing URL no report number, version, archive, artifact hash, or “not peer reviewed” status ([research-agent-context-fhir.html:291](/Users/aanishsachdev/Desktop/cstack/bonfire-db-site/src/fragments/research-agent-context-fhir.html:291)). Add a versioned archival release/DOI or use `@misc`/`@online` with an explicit preliminary technical-report note.

37. [P2] Person author + organization affiliation is schema-valid; the remaining schema risk is status ambiguity. The page is now explicitly preliminary, while `ScholarlyArticle` metadata does not expose draft/preprint/non-peer-reviewed status. Add `creativeWorkStatus` and a version/archive URL so crawlers do not infer more finality than the visible copy licenses.

REWORK
1. [P1] A6 uses benchmark-construction metadata as an oracle. The roadmap calls it “non-gold metadata,” but [a6_packet_builder.py](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/a6_packet_builder.py:159) reads `main_table_name` to select the exact FHIR resource type and [lines 176–218](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/a6_packet_builder.py:176) mine `val_dict` placeholders. Those annotations do not exist in a normal user query. Minimal fix: primary A6 must plan from question text, patient ID, and assumption only; report the metadata-assisted version separately as an oracle ceiling.

2. [P1] The named A6 implementation is not bounded. It adds `_count`, then calls `search_with_pagination()` and concatenates every page and complete resource into the packet ([a6_packet_builder.py:313](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/a6_packet_builder.py:313), [fhir_client.py:52](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/fhir_client.py:52)). `_count` is page size, not a result cap. Minimal fix: enforce total resource/token ceilings, server-side date/code filters, field projection, and actual first/last selection before calling this A6.

3. [P1] A6 is still a product bundle, not the decisive experiment. Item 1 combines query-aware planning, first/last preservation, deduplication, serialization changes, and coverage-first planning. A win cannot be attributed to “query-aware selection.” Split it into:

   - A6a: question-only selection, existing renderer, no coverage.
   - A6b: identical frozen evidence, serialization only.
   - A6c: identical planner, coverage summary on/off.
   - A6d: dedup on/off with identical retrieval results.

4. [P1] The serialization ablation repeats the project’s budget confound. JSON, Markdown, narrative, and timeline renderings differ in tokens, ordering, redundancy, and sometimes facts retained. “Same source packet” is not matched information or matched budget. Require deterministic lossless renderers, report token counts, and compare at a common context budget. The cited ~19 F1 result is from synthetic medication reconciliation on ≤8B models, not GPT-5.5 clinical QA; it licenses an exploratory arm, not a transferred expectation. [Primary paper](https://arxiv.org/abs/2604.21076).

5. [P1] `getDataCoverage` is evidence, not merely “planning input.” Per-type counts, date ranges, and top categories can directly answer existence/count questions or reveal the likely label. Minimal fix: make coverage a separate treatment, score direct-answer leakage, and restrict the summary to fields proven incapable of resolving the benchmark question.

6. [P1] A6 still proposes paired claims against a historical A5 run from the torn-down substrate before item 14’s same-instance rerun. Pairing question IDs does not control server version, loaded data, model snapshot, harness, or run date. The existing report explicitly disclaims strict parity ([FINAL_REPORT.md:111](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/docs/FINAL_REPORT.md:111)). Run contemporaneous A0/A0′/A5/A6 on one instance before claiming A6 matches the sandbox.

7. [P1] The roadmap repeats post-treatment strata as acceptance criteria. “Overflow” and “resource-real” are defined by A0’s observed outcome; the report already calls this post-hoc and success-selected ([FINAL_REPORT.md:52](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/docs/FINAL_REPORT.md:52)). Add pre-treatment strata based on independently tokenized record size and question class. Keep the old strata descriptive only.

8. [P1] A12’s motivation does not support A12’s mechanism. Same-type re-requests are loop/dedup failures; “cannot find/truncated” rows are mostly projection cap-drops. Neither implies an unsupported search parameter or a suppressed server error. OperationOutcome fidelity cannot restore records discarded by cap-50. Restrict A12 to rows with observed malformed, ignored, or unsupported queries; do not compare it against all packet-solved failures.

9. [P1] A12 changes error fidelity and corrective coaching simultaneously. “Strict + corrective” rejects the call, lists every supported parameter, explains semantics, and supplies suggestions; any gain could come from the embedded catalog, not strictness. FHIR only says servers should honor `Prefer: handling=strict/lenient`; it does not require this coaching bundle. [FHIR R4 search handling](https://hl7.org/fhir/R4/search.html). Minimal split:

   - Silent vs explicit error with identical diagnostic content.
   - Explicit error vs error plus supported-parameter list.
   - Supported-parameter list vs semantic suggestions.
   - Unknown-parameter handling separate from upstream/network failures.

10. [P1] The claimed A12 “status quo” does not match the named harness. [treatment_mcp_server.py:75](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/treatment_mcp_server.py:75) already returns JSON containing HTTP status and response detail; it does not generally convert failures into empty bundles. Implement and verify each treatment at the proxy boundary before calling it a three-condition server experiment.

11. [P1] A12’s pilot design invites winner’s curse. A “pre-registered slice before any full run” becomes adaptive arm selection if the same 409-question test set is later reused. There is no powered effect size, three-condition paired test, patient clustering rule, or multiplicity rule. Use a development slice to debug mechanics and an untouched confirmatory set with Cochran-Q/paired contrasts, cluster-aware intervals, and a declared primary contrast.

12. [P1] A7 cannot identify why it wins. It simultaneously adds reference expansion, terminology summaries, citations, insufficiency metadata, a read-contract prompt, handles, samples, computed projections, an evidence ledger, and policy semantics. The current builder visibly emits all of those together ([a7_packet_builder.py:182](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/a7_packet_builder.py:182)). Minimal sequence: A6 + references only; + terminology only; + citation envelope only; then handle-vs-inline using the exact same frozen evidence. Authorization belongs in A14, not the A7 accuracy headline.

13. [P1] “Handles vs inlined packets at matched information content” is undefined. A handle that permits adaptive reads exposes a conditional information channel; a static packet does not. Either replay and inline the exact handle trace after the fact, or compare handle transport against inline transport under a frozen access plan. Otherwise the arm measures transport, planning, and evidence quantity together.

14. [P1] A13’s chunked-reading arm bundles chunk size, turn budget, and dedup. Use a 2×2 design: bounded/unbounded chunks × dedup on/off, with the same maximum evidence and total token budget. Frozen A6 packets cannot test interactive chunking at all; that requires a live retrieval substrate and must not share the same headline.

15. [P1] A13’s verifier arm bundles detection, feedback, and retry. A model verifier that supplies a failure reason may simply coach the second answer; a deterministic citation check tests a different capability. Split verification-only/no-retry, retry with a generic failure bit, and retry with diagnostic feedback. Final scoring must be independent of the verifier and selector.

16. [P1] Best-of-N cannot identify inference-time scaling without selector controls. “Panel picks” measures both sampling diversity and panel quality. Report random selection, deterministic evidence-support selection, model-panel selection, and an oracle upper bound separately. Do not reuse the grading panel as the selection panel.

17. [P1] A10-CAT is another bundle: schemas, local mappings, counts, date coverage, example queries, and prior successful traces. The paired “catalog vs no catalog” contrast cannot identify which component matters. Worse, examples and successful traces learned from the same 409 questions leak templates and deployment-specific answers. Freeze the catalog from training/development patients only, prohibit test traces, and add components incrementally.

18. [P1] A10-CITE is described as deterministic when claim support is usually semantic. Checking that a cited ID exists is deterministic; checking that it supports a causal, temporal, or comparative claim is not. Separate referential validity from semantic entailment and use blinded human adjudication or an independently validated verifier for the latter.

19. [P1] A10-VEC is un-runnable on this benchmark. The 409-question split is generated entirely from structured MIMIC tables; its gold types are Observation, Encounter, Medication/MedicationRequest, Procedure, Patient, Location, and Condition—not notes or DocumentReference passages. Build and pre-register a note-grounded dataset before promising a BM25/vector accuracy result.

20. [P1] A9’s partial A12 carry-through does not separate delivery and legibility. Applying strict-vs-silent only “where cheap” on the generic arm produces an incomplete factorial. Either run error fidelity as its own experiment or cross it fully with generic/expanded tool surfaces.

21. [P1] A14 lacks principal-specific gold outcomes. A patient, panel clinician, and aggregate-only backend service are not supposed to answer identical tasks identically. Legitimate denial will be mis-scored as inaccuracy, and SMART scopes alone do not encode clinician panels or aggregate-only policy. Define authorized answer/abstention gold per principal, hold accessible evidence constant where accuracy is compared, and evaluate disclosure separately.

22. [P1] A14 pre-commits the leakage result: “expected ≥1 vs 0.” An acceptance criterion cannot require the baseline to leak and the treatment never to leak. Specify attacks, sensitive canaries, confidence intervals, and a failure threshold without dictating the observed outcome.

23. [P2] “All published benchmarks are single-identity with effectively admin credentials” remains too absolute. PhysicianBench is explicitly framed around physician workflows, and HealthAgentBench spans seven heterogeneous environments; absence of an authorization-axis evaluation does not prove admin-equivalent credentials in every environment. Scope the claim to “we found no reported principal-varying authorization evaluation.” [PhysicianBench](https://arxiv.org/abs/2605.02240), [HealthAgentBench](https://arxiv.org/abs/2606.31179).

24. [P1] Item 11’s re-grade remains mechanically impossible “unchanged.” `build_labels.py` and `final_grade.py` are hard-coded to `runs/full409`, two arms named `resource`/`code`, and the fork’s panel file shapes ([build_labels.py:28](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/build_labels.py:28), [final_grade.py:16](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/final_grade.py:16)). The RL paper evaluates 424 validation questions and different output structures. Write a generic single-arm grader adapter before putting this in acceptance criteria.

25. [P1] Item 16’s judge re-measurement is also not runnable through `judge_leaderboard.py` as written. That script scores existing cached labels; it does not invoke o4-mini or construct the equalized prompts required by the merged roadmap. The reconciliation preserved the issue text but not the executable path. Add a judge runner that freezes question inclusion, tolerance instructions, arm presentation, model version, seed, and cache schema.

26. [P1] The lazy tree-walk arm combines compact summaries, field-on-demand reads, visited-set dedup, best-first ordering, and a stopping rule. A win cannot be attributed to tree traversal. Split compact-vs-raw node representation, dedup on/off, and fixed-vs-evidence-conditioned stopping. Also stop presenting the 448-to-2 example as general evidence: it is one medication question ([retrieval_per_question.csv:56](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/medplum-eval/retrieval-metrics/retrieval_per_question.csv:56)).

27. [P1] The enterprise conformance map conflates products with deployments. Unknown-parameter behavior, supported search parameters, indexing, and CapabilityStatement completeness are version/configuration-specific. Epic/Oracle documentation-only rows are not comparable to live Medplum/HAPI probes. Publish separate “observed deployment” and “documented product” tables with version, configuration, timestamp, credentials, and raw probe artifacts.

28. [P1] Representation variance cannot be measured by probing unrelated vendor sandboxes. Different facts in different patient corpora confound representation with dataset content. Load one canonical synthetic fixture into every writable server; treat closed vendor sandboxes as qualitative case studies only.

29. [P1] The merged public roadmap still cites non-public scratch work as evidence: `medplum/.scratch/healthclaw-issues/` and unnamed “2026-07-10/11 reviews.” Public readers cannot inspect or reproduce those claims. Move the artifacts into the public repository or remove them.

30. [P1] The roadmap has no family-level statistical policy across 19 items, dozens of component toggles, multiple formats, principals, strata, models, and selectors. Per-item pre-registration does not prevent cross-item cherry-picking. Declare one primary A6 hypothesis, a hierarchical testing order, patient-clustered inference, confirmatory holdouts, and explicit exploratory labeling for everything else.

31. [P1] The ordering contradicts its own “changes the conclusion most, soonest” rule. Same-instance reruns, cross-family adjudication, reproducibility, and judge re-measurement sit behind large catalog, authz, graph, vector, and enterprise programs. These credibility repairs must precede new product-shaped headlines.

32. [P2] The “substrate legibility ladder” is not one axis. Error diagnostics, semantic catalogs, compiled selection, authorization, handles, and governance are neither nested nor monotonic. Treat them as separate factors; otherwise “moving up a rung” has no stable experimental meaning.

33. [P2] MedAgentBench v2’s isolated +7pp memory gain is real, but calling it evidence for A12 is mechanistically wrong. It is cross-episode memory synthesized from prior failures, not same-request server error fidelity. Cite it as evidence for learned corrective instructions, not strict FHIR errors. [MedAgentBench v2](https://pubmed.ncbi.nlm.nih.gov/41758153/).

34. [P1] Site authorship now conflicts with repository citation metadata. The report page and JSON-LD claim sole authorship by Aanish Sachdev/bonfireDB ([research-agent-context-fhir.html:24](/Users/aanishsachdev/Desktop/cstack/bonfire-db-site/src/fragments/research-agent-context-fhir.html:24), [agent-context-fhir.astro:15](/Users/aanishsachdev/Desktop/cstack/bonfire-db-site/src/pages/research/agent-context-fhir.astro:15)), while the repository’s [CITATION.cff:14](/Users/aanishsachdev/Desktop/cstack/fhir-mcp-eval/CITATION.cff:14) names CoralEHR as author. Decide whether these are distinct artifacts and say so explicitly; otherwise this is an attribution dispute waiting to happen.

35. [P1] The conflict-of-interest disclosure is in the eval roadmap, not visibly attached to the citable Bonfire report. The report is authored and published by the product whose design hypotheses it advances. Put the disclosure beside the byline or citation block; a disclosure in another repository is not adequate for page readers.

36. [P2] Self-issuing `@techreport` is not inherently invalid; non-peer-reviewed technical reports are citeable. The problem is that this entry gives a mutable marketing URL no report number, version, archive, artifact hash, or “not peer reviewed” status ([research-agent-context-fhir.html:291](/Users/aanishsachdev/Desktop/cstack/bonfire-db-site/src/fragments/research-agent-context-fhir.html:291)). Add a versioned archival release/DOI or use `@misc`/`@online` with an explicit preliminary technical-report note.

37. [P2] Person author + organization affiliation is schema-valid; the remaining schema risk is status ambiguity. The page is now explicitly preliminary, while `ScholarlyArticle` metadata does not expose draft/preprint/non-peer-reviewed status. Add `creativeWorkStatus` and a version/archive URL so crawlers do not infer more finality than the visible copy licenses.

REWORK
