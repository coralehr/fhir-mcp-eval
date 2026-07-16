# Evaluating MCP tool surfaces for agents — an experimental FHIR / Medplum eval

> ⚗️ **Experimental / exploratory work.** This is a fork of
> [glee4810/FHIR-AgentBench](https://github.com/glee4810/FHIR-AgentBench) carrying a rig for a simple but
> slippery question: **when you expose tools to an LLM agent through an
> [MCP](https://modelcontextprotocol.io) server, do those tools actually make the agent better — and how
> would you *honestly* measure that?** The worked example is FHIR / [Medplum](https://www.medplum.com).
> The harness pattern generalizes to other MCP tool surfaces; the empirical lessons are scoped to this FHIR
> case study. Treat the numbers as preliminary; see
> [Reproducibility status](#where-this-was-actually-run--reproducibility-status).
>
> **Disclosure:** Bonfire ([bonfiredb.dev](https://bonfiredb.dev)) is the author's product; the planned
> A6/A7 arms test its design hypotheses. We pre-commit to publishing results either way.

> **New (2026-07-12): the pre-registered A6a arm ran.** Deterministic **question-only selection beat
> query-blind blunt projection by +9.5pp** (54.3% vs 44.7%, n=409 paired, McNemar p=1.3×10⁻⁵,
> patient-cluster 95% CI [+5.4, +13.7]) at 43% less packet payload, on a frozen pre-registration with the
> null branch committed in advance. Preliminary — single substrate, single-family panel, judge
> re-measurement pending. Full table, strata, and caveats: [docs/A6A_RESULT.md](docs/A6A_RESULT.md).

> **New (2026-07-14): QT-4 passed untouched-holdout confirmation for vocabulary only.** On the
> predeclared 44-question microbiology stratum within a fresh 374-question valid split, A6a-r scored
> 10/44, fixed terminology vocabulary 25/44, and vocabulary plus bounded traversal 29/44. Vocabulary
> versus A6a-r was **+34.1pp** (patient-cluster 95% CI [+17.9, +50.0], exact McNemar p=.000275) with
> the 330 negative controls inside the registered safety bound. Traversal's incremental **+9.1pp** was
> unresolved (p=.219), so the fixed-sequence decision is **promote vocabulary only**. Full result,
> token ledger, receipts, and forensic audit: [QT4_VALID374_RESULT.md](docs/results/QT4_VALID374_RESULT.md)
> and [QT4_VALID374_FORENSIC_AUDIT.md](docs/results/QT4_VALID374_FORENSIC_AUDIT.md).

> **New (2026-07-15): A11 completed. Traversal solved the deliberately
> path-required evidence problem; event grouping did not earn promotion.** On
> 120 paired non-PHI synthetic questions, vocabulary-star V scored 24/120,
> flat-traversal T scored 119/120, and event-group E scored 120/120. The
> registered primary E-minus-T estimate was +0.833 points with patient-cluster
> 95% CI [0, +2.564], so E is **not promoted**. The registered secondary T-minus-V
> contrast was +100 points on the 96 answerable questions; it was not a
> promotion gate. E used 9.1% fewer payload bytes and 2.0% fewer answer tokens
> than T. The post-result audit found no hidden-gold or tool-use contamination,
> but E bundled event structure with an answerability receipt, so its one gain
> cannot be attributed to grouping alone. Full result, preserved aggregate
> artifacts, and forensic review: [A11_RESULT.md](docs/results/A11_RESULT.md) and
> [A11_FORENSIC_AUDIT.md](docs/results/A11_FORENSIC_AUDIT.md).

> **New (2026-07-14): the promoted recipe and A11 event-group gate are executable.** New packet builds
> can use `compile_evidence.py`, which defaults to the holdout-promoted vocabulary recipe while preserving
> every historical experiment entrypoint. The zero-model A11 gate separately exercises synthetic
> promoted-recipe-shaped star, flat-traversal, and event-group proxies. On ten non-PHI mechanism/safety
> cases, traversal and event grouping reached 100% terminal-evidence recall with zero scope leakage; the
> star proxy reached 0%. The hardened gate derives its plan from question text alone, enforces a registered
> relation vocabulary plus
> patient/purpose/practice/version scope, hides unavailable target IDs, and applies one byte bound to every
> arm. This is a synthetic compiler/integrity result, not byte-equivalence to product packets or
> answer-accuracy evidence. The new path-bound A11 adapter now proves literal V model-packet equality for
> non-plan-only promoted records and rejects unsealed artifacts, forbidden benchmark metadata, duplicate,
> non-dispatched, or patient-ambiguous/directly cross-patient resources. The sealed efficacy corpus now
> adds independently pinned source provenance plus a governed authorization/source-version receipt.
> The QT-4 inventory has only ten depth-two rows and one path family, so the 120-question efficacy run is
> correctly blocked pending a multi-family non-PHI substrate. A local HolyFHIR export is graph-rich in general
> but has zero depth-two paths under A11's frozen microbiology registry and only 11 patient clusters. The
> frozen `qo-v2.1` planner also never queries `DiagnosticReport` for microbiology questions. That producer
> blocker is now resolved without rewriting history: the explicit pre-answer `a11-four-family-v1` recipe
> binds `qo-v2.2-a11-four-family`, reaches all four registered root/path families, and passes the actual
> entrypoint-to-adapter zero-model gate. The 2026-07-15 governed-retrieval gate now binds an
> adapter-verified V packet to pinned source and policy artifacts plus one immutable T/E traversal receipt,
> without claiming production Bonfire ABAC. A second pre-answer amendment adds the isolated
> `a11-four-family-depth-aware-v1` recipe and deterministic 24-development/120-efficacy corpus builder over
> a pinned official 115-patient Synthea sample archive. It freezes all eight family-depth cells and exact
> unanswerable/temporal quotas without changing older recipes. On 2026-07-15 the receipt and dataset gates
> both closed: two independent official-source builds were byte-identical at manifest SHA-256
> `442ca8d204fbd81f06e0abaf2ea5022b375deabb71a93d5ebaeccef98e99fe3c`, and all 144 rows passed the complete
> real-producer/strict-adapter/governed-retrieval preflight with zero model calls. An independent pre-answer
> review also forced two competing temporal facts per case, balanced every failure mode 3 first/3 latest,
> and added mechanism-level audits. The sealed V/T/E controller, grading, panel-replay and finalization
> implementation subsequently passed 185 no-model tests plus an end-to-end local seal rehearsal and
> independent adversarial review. The official Mac mini controller is now sealed at manifest SHA-256
> `3f1209ebc750c7f9eeb67d0a7e5ed3a455aa91dbda2be2ffd4c1905fe192fdce` from merged commit
> `0123ca2bf4e1aecfdf5092b0a2b333c5afbe75dc`, using Codex CLI `0.144.1`. The fixed
> 120-question/360-answer run subsequently completed cleanly; see the final
> result above. The pre-answer evidence chain remains in
> [A11_EVENT_GROUP_MECHANISM.md](docs/results/A11_EVENT_GROUP_MECHANISM.md) and the frozen
> [A11_EVENT_GROUP.md](docs/prereg/A11_EVENT_GROUP.md) protocol, plus the
> [adapter gate](docs/results/A11_PRODUCT_PACKET_ADAPTER.md),
> [four-family producer gate](docs/results/A11_FOUR_FAMILY_PRODUCER_GATE.md),
> [governed retrieval gate](docs/results/A11_GOVERNED_RETRIEVAL_GATE.md),
> [dataset gate](docs/results/A11_DATASET_GATE.md),
> [controller gate](docs/results/A11_CONTROLLER_GATE.md),
> [candidate-inventory result](docs/results/A11_CANDIDATE_INVENTORY.md), and
> [substrate audit](docs/results/A11_SUBSTRATE_AUDIT.md).

## TL;DR

**What actually makes an LLM agent more accurate on FHIR clinical QA?** We swept the levers an engineer
would reach for, each a **paired** comparison with exact stats. **Every "win" decomposed to one thing: the
context budget.** Tool catalog, payload coaching, and thinking time are nulls; the code interpreter's
apparent win is a context-overflow artifact (a null at matched budget), not a reasoning gain.

- **Bigger / purpose-built tool catalog → NULL.** No detectable advantage over one generic `fhir-request`
  on either **Opus 4.8** or **GPT-5.5** (Opus structure-lift p=0.69; GPT-5.5 curve flat, 1 tool never
  significantly beaten; underpowered for small effects — MDE ~34–46pp at n=25–30/cell). The early **+11pp**
  (≈39%→50%) was a **context-budget confound**, not a tool win — and it
  replicates the parent paper's own ablation.
- **Payload shaping → cost-only** (Δ0.00). **Reasoning effort medium→high → NULL** (0/30 judged-correctness flips;
  95% upper bound ≈12% flip rate).
- **Code interpreter → overflow-avoidance, not a reasoning win.** Under **trustworthy grading**
  (deterministic numeric + a 3-Claude-judge panel, cross-checked by an independent codex/GPT panel and
  validated against non-LLM ground truth), the code arm shows **no significant benefit where the no-code
  agent can answer** — matched budget (n=140, both arms produced a real answer): **71.4% vs 67.9%, −3.6pp, 95% CI
  −7.7…+0.6, McNemar p=0.18 → not significant** (slight negative point estimate; this matched-budget set is a
  different, success-conditioned stratum from the resource-real n=147 stratum in the table below). Its large pooled lift
  (**+39.9pp**) is **entirely** the 262/409 (64%) questions where the no-code agent **overflows the 32k cap**
  and the code agent sidesteps it via a sandbox. **The bottleneck is *getting bounded data into context* —
  not tool design, payload, thinking time, or compute.** A code path helps only because sandboxing the
  payload dodges the overflow; payload projection plausibly pulls the same lever.
- **⚠️ A single small LLM judge is unreliable here — and we quantified it.** Against **non-LLM ground truth**
  on the 111 numeric arm-answers (97 numeric-gold questions), the small judge we ran with the benchmark's judge
  prompt (gpt-5-mini; the benchmark's shipped default is o4-mini, which we did not measure) is **61% accurate**
  (43 false-negatives, a one-directional
  precision-punishing bias), while 3-vote panels score **98–99%** (Claude 98.2%, codex/GPT 99.1%). Measurement
  caveat: our gpt-5-mini invocation omitted the question text while the panels received the question plus a
  numeric-tolerance instruction and judged both arms side-by-side, so 61% measures *our configuration* of a
  single small judge, and a fair re-measurement could move it. That bias
  manufactured an earlier spurious "code HURTS −8.6pp." Always audit your LLM judge against ground truth and
  use a panel. See [TRUSTWORTHY_REGRADE.md](docs/TRUSTWORTHY_REGRADE.md).
- **Contributions:** (1) the *correct decomposition* — the code "win" is a context-overflow artifact, not a
  compute gain; the same confound faked the tool-catalog "win"; (2) a **judge-reliability finding** — the
  small judge we ran with the benchmark's judge prompt (gpt-5-mini) is 61% accurate vs ground truth; a
  multi-vote panel (two model families, 97% mutual
  agreement) mitigates it; (3) the grading methodology that also caught a boolean Yes/No grading bug in our own
  first fix; (4) the cap-factorial + paired-stats harness that caught the confound twice.
- **QT-4 confirmatory result: terminology binding earned promotion; generic traversal did not.** Fixed
  question-only microbiology vocabulary moved the registered untouched-holdout stratum from 10/44 to
  25/44 while reducing accepted answer tokens 13.9% across all 374 questions. Bounded traversal reached
  29/44 and recovered substantially more mapped gold evidence, but its incremental correctness contrast
  did not pass the registered significance and interval gates. The failure audit points next to typed
  event grouping, temporal rank, and deterministic answerability—not simply deeper traversal.
- **A11 result: traversal passed its path-required mechanism test; event groups
  did not earn an incremental accuracy claim.** V/T/E scored 24/120, 119/120,
  and 120/120. E minus T was +0.833 points with 95% CI [0, +2.564], so E was not
  promoted. T minus V was +100 points on the 96 answerable cases, where V had
  zero terminal-evidence recall and T/E had complete recall. The only T error
  substituted a later complete event for an earlier incomplete one. E prevented
  it, but E bundled event grouping, temporal rank, and an answerability receipt,
  so the causal feature is unresolved.
- ⚠️ **Reproducibility is split.** For the trustworthy re-grade, the committed artifacts are the aggregate
  summary (`medplum-eval/full409_summary.json`) and a durable per-question answer backup
  (`medplum-eval/full409_answers.json`); the per-question panel/deterministic labels live under gitignored
  `runs/` and are **not** committed — regenerating them requires the large gitignored raw answer dumps plus
  judge-panel LLM calls. The GPT-5.5 tool-curve per-question labels *are* committed
  (`medplum-eval/results/*.judged.json` + `_scores.csv`/`_paired.json`). The new A0′ table is
  locally recomputable when those dumps are present; [FINAL_REPORT.md](docs/FINAL_REPORT.md) records the exact scope.
  **Opus tool-ablation numbers are not** (run on torn-down EC2). See
  [Reproducibility status](#where-this-was-actually-run--reproducibility-status).
- **Start here: [A11_RESULT.md](docs/results/A11_RESULT.md)** for the latest
  mechanism result and [A11_FORENSIC_AUDIT.md](docs/results/A11_FORENSIC_AUDIT.md)
  for the post-result no-cheating and failure analysis. Then read the
  [QT-4 holdout result](docs/results/QT4_VALID374_RESULT.md),
  [FINDINGS.md](docs/FINDINGS.md) (the earlier capstone conclusion), the tool-ablation deep-dive
  **[REPORT.md](docs/REPORT.md)** and the code result **[CODE_EXPERIMENT.md](docs/CODE_EXPERIMENT.md)**.

## Final result: A0 vs A0' vs A5

The final control asks whether the code sandbox is valuable because it computes out-of-context, or because it
selects a bounded slice of the chart. The answer is narrower: **query-aware selection is the lever; the sandbox
is one implementation.** The tested projection was deliberately blunt, so it is a floor for projection quality,
not a ceiling.

![Grouped bar chart of final three-arm accuracy](docs/images/final_three_arm_accuracy.svg)

| Arm | Overflow stratum (n=262) | Resource-real stratum (n=147) | Pooled (n=409) |
|---|---:|---:|---:|
| A0 — raw FHIR in context | 0.0% | 70.7% | 25.4% |
| A0' — projection only | 22.1% | 70.1% | 39.4% |
| A5 — code interpreter | 65.6% | 64.6% | 65.3% |

![Stacked bar chart of A0 prime overflow decomposition](docs/images/a0prime_overflow_decomposition.svg)

Read the full, red-teamed version in [FINAL_REPORT.md](docs/FINAL_REPORT.md). The clean next experiment is a
query-aware in-context projection arm: fetch the resource type and date range the question asks for, keep
first-and-last values, and deduplicate repeated requests.

### Cost and token accounting for the final 409-question run

The final three-arm control cost **$106.86 in tracked agent API spend** across **29.15M tokens**. These numbers
come from the per-question `usage` objects in the raw answer dumps
(`runs/full409/multi_turn_resource.json`, `runs/a0prime/multi_turn_projected_resource.json`,
`runs/full409/multi_turn_code_resource.json` — these raw dumps are gitignored and not committed; see
[Reproducibility status](#where-this-was-actually-run--reproducibility-status)) and use the model-pricing table available to LiteLLM at run time.
They exclude EC2/Docker/Colima infrastructure and do **not** fully include the ad hoc judge-panel/red-team spend.
One reconciliation note: the committed `medplum-eval/full409_summary.json` records `"cost_total_agent_usd": 34.38`
for the A0/A5 agent passes — that is the figure the run-time tracker had logged when the summary was written,
whereas the ledger below is a later recomputation from the per-question `usage` objects; the two figures cover
different accounting scopes, and this README ledger is the authoritative one for total agent spend.

| Arm | Questions | Prompt tokens | Completion tokens | Total tokens | LLM calls | Tracked cost | Cost / question |
|---|---:|---:|---:|---:|---:|---:|---:|
| A0 — raw FHIR in context | 409 | 2.64M | 0.12M | 2.76M | 632 | $11.63 | $0.028 |
| A0' — projection only | 409 | 17.43M | 0.53M | 17.96M | 1,284 | $59.92 | $0.147 |
| A5 — code interpreter | 409 | 7.75M | 0.68M | 8.43M | 1,872 | $35.31 | $0.086 |
| **Total** | **1,227 arm-questions** | **27.82M** | **1.33M** | **29.15M** | **3,788** | **$106.86** | **$0.087** |

The cost result is part of the finding: the blunt A0' projection was both less accurate than the sandbox and
more expensive, because it still let the agent accumulate repeated projected payloads across turns. The
future A6/A7 arms should report accuracy beside the same token/cost ledger, not as a separate afterthought.

## Future work / issues

The evidence-driven execution plan is
[NEXT_EXPERIMENT_PLAN.md](docs/NEXT_EXPERIMENT_PLAN.md); the full issue-ready
backlog remains in [ROADMAP.md](docs/ROADMAP.md):

- Before another answer run, independently approve and install the deterministic
  restricted-executor package, externally anchor the complete install surface
  and controller/native-binary digest, retain panel event streams, and build a
  patient-disjoint harder A11b holdout. The package compiler exists, but no
  executor account, sshd policy, credential, or live service is installed.
- Run A11b as flat traversal versus flat traversal plus selection/completeness
  aids versus event groups with the identical aids and clinical evidence.
- The zero-model A11b representation compiler is implemented and adversarially
  clean on separated synthetic development source/gold; the untouched corpus,
  power calculation, materializer, preregistration, controller seal, install,
  and efficacy calls remain blocked. See
  [docs/A11B_EVENT_COMPILER.md](docs/A11B_EVENT_COMPILER.md).
- The zero-model A11b prospective power gate is also implemented and
  adversarially clean. It derives 384 unique efficacy patients plus 64
  development patients under a receipt-bound 30% discordance ceiling; that
  ceiling is an explicit assumption, and neither the spec nor receipt is an
  external approval or corpus seal. See
  [docs/results/A11B_POWER_GATE.md](docs/results/A11B_POWER_GATE.md).
- The zero-model Synthea generation-receipt verifier is implemented. It binds
  the power-derived 448-Patient population to a pinned generator/JAR, complete
  staged Java runtime and probe, exact argv/environment, registered
  config/modules, and the complete raw-output tree. No real release has yet
  been pinned or generated; see
  [docs/A11B_GENERATION_RECEIPT.md](docs/A11B_GENERATION_RECEIPT.md).
- A provider-neutral direct-API contract is implemented with canonical
  content-only receipts and two provider-shaped fake adapters. It contains no
  provider SDK, credential, network request, or model call and is not yet wired
  into the confirmatory executor; see
  [docs/PROVIDER_API_CONTRACT.md](docs/PROVIDER_API_CONTRACT.md).
- In parallel, benchmark query-time traversal against a materialized Postgres
  edge projection for byte equivalence, policy behavior, correction/rebuild
  semantics, and latency. Do not select a native graph store from accuracy data.
- After A11b, prioritize error fidelity (A12), then principal-varying
  authorization (A14).
- Publish a minimized reproducibility artifact package with checksums.
- Rerun A0, A0', and A5 on one substrate.
- Add cross-family or human adjudication for A0' non-numeric labels.
- Run a projection cap sweep.
- Add a tracked failure-decomposition script.

### External pre-answer anchor for A11 v3

An A11 v3 seal writes `anchor-request.json` beside the controller manifest. The
request contains only SHA-256 digests, byte counts, the experiment profile, and
the registered model configuration. It does not contain FHIR content, prompts,
answers, local paths, or credentials.

The experiment host is not allowed to approve its own request. Before any
answer or panel call:

1. Copy the exact `anchor-request.json` bytes to a separate trusted laptop.
2. Commit them under `anchors/<experiment>/anchor-request.json` through a PR in
   this repository. A repository member other than the PR author and merger
   must approve the exact PR head commit before it is squash-merged to `main`.
   The v1 protocol allowlists `AJ112103` and `Arhaan2104` as those independent
   approvers by their stable GitHub numeric account IDs; renaming or adding a
   collaborator does not silently widen the gate.
3. Record the full 40-character merge commit SHA. GitHub documents that a
   commit-SHA link is a permanent file version, unlike a moving branch link:
   [permanent links to files](https://docs.github.com/en/repositories/working-with-files/using-files/getting-permanent-links-to-files).
4. Pass a pinned GitHub Contents API URL to both live runners:

   ```text
   https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/anchors/<experiment>/anchor-request.json?ref=<40-character-merge-sha>
   ```

   The Contents API accepts a commit SHA as `ref` and supports raw file media:
   [repository contents API](https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28).

5. Run answers and the panel with `--anchor-url <url>`. The runners fetch the
   exact published bytes, require GitHub's commit verification to report a
   valid signature, prove the commit came from one uniquely merged `main` PR,
   require that PR to add or modify the exact anchor path, re-fetch the exact
   bytes from its reviewed head commit, and require an independent
   member/owner/collaborator approval on that same head. They then write one
   read-only `external-anchor-verification.json` receipt before any model call.
   Every new runner process revalidates GitHub and requires the remote evidence
   to match that exact local receipt, so a run host cannot forge its own cache.

Moving branch names, abbreviated SHAs, other repositories, non-`anchors/`
paths, unsigned commits, changed bytes, missing local request files, or altered
verification receipts all fail closed. Self-approval, approval of an older PR
head, dismissed approval, bot approval, and unmerged or non-`main` PRs also
fail closed. Completed A11 v1/v2 bundles remain
readable for audit and finalization, but cannot make new live calls.

## What we're actually trying to do

"Add an MCP server with N purpose-built tools and the agent gets smarter" is the kind of claim that's
**easy to assert and easy to fool yourself about.** This project is an attempt to build the eval
*correctly* — to measure whether an MCP tool surface helps while controlling for the things that usually
masquerade as a tool win:

- **Context budget.** More/richer tools return bigger payloads; bigger payloads overflow the model's
  context window. A "tool win" is often just "this arm overflowed less." → we vary the context cap as an
  explicit factor (a **2×2 cap-factorial**) to separate *reasoning gain* from *cap-dodging*.
- **Prompt vs. structure.** Is the typed tool better, or did we just *tell* the agent something we never
  told the generic tool? → a **coached-generic control** (generic tool + the same coaching in its
  description only).
- **Noise.** The original +11pp headline (and the residual ~8pp tool-structure lift after controls) sits
  across only 25–30 questions/cell — inside the noise floor (our MDE at this n is **~34–46pp**, REPORT §9.2).
  → **paired McNemar + bootstrap** on per-question deltas, not eyeballed averages.
- **Hidden failures.** Overflows / rate-limits / no-answers ≠ wrong answers. → an **answerable-set
  accuracy** + a by-cause failure taxonomy.

The reusable artifact is this methodology, not any one number.

## The harness (generalizes to any MCP eval)

| File | Role |
|---|---|
| `agent/mcp_agent.py` | Agent that retrieves through an **MCP server** — the only variable across arms is the advertised tool surface. |
| `agent/ai_agent.py` | Variant that routes completions through a server-side LLM proxy (here, Medplum's in-FHIR `$ai` op) — test the lift on the platform's *own* agentic surface, not just an external client. |
| `treatment_mcp_server.py` | **Catalog-driven** FastMCP server: one server, `TOOL_SUBSET`-selectable arms. The baseline "generic" arm is a **local FastMCP re-implementation** whose *description string* is copied byte-for-byte from Medplum's shipped `fhir-request` tool, proxying the same FHIR REST surface — it is **not** the platform's production MCP tool path. (The smoke test confirms Medplum advertises a tool playing the same role — named `fhir-request` with a hyphen; our local control registers `fhir_request` with an underscore — so it's a description-matched reimplementation, not the identical tool.) Typed tools toggle in on top. |
| `run_matrix.py` | Parameterized ablation runner: per-cell tool subset, a **nested dose-response staircase** (1→2→4→… tools), the cap-factorial, and a **hard $-budget ledger** that stops cleanly instead of surprising you. |
| `score_taxonomy.py` | raw + **answerable-set** accuracy, by-cause failure taxonomy, **paired McNemar + bootstrap**. |
| `robustness_analysis.py` | Judge-free post-hoc pass (no re-run, no spend): deterministic re-score, minimum-detectable-effect (MDE) power sim, Holm-Bonferroni. |
| `eval_budget.py` | Token-cost ledger with a hard cap. |
| `medplum-eval-bundle/` | Reproducible substrate: `docker compose` (Medplum + Postgres + Redis, MCP enabled) + an open-access MIMIC-IV-on-FHIR-demo loader. |

To point it at a *different* MCP server/domain, swap the tool server + the dataset; the runner, budget
ledger, cap-factorial, and scorer are domain-agnostic.

## The case study & what we found (preliminary)

**Finding (honest, and a null):** across Claude Opus 4.8 and GPT-5.5, the tool catalog showed **no
statistically significant accuracy advantage** over the single generic tool. The early **+11pp** (generic
≈39% → 5-tool catalog ≈50%) **did not survive** the controls above — it folded in a
context-budget/overflow artifact, has no paired statistics, and does not replicate on either model. The
only robust, significant effect was the **context cap**: reference-resolution (`_include`) tools overflow
the default budget (e.g. one Opus arm overflowed 20/25 medication questions at the stock 32k cap;
p=0.0005, the one comparison that survives Holm-Bonferroni). At least here, a well-designed single tool is
plenty. Full write-up + tables + paired stats: **[REPORT.md](docs/REPORT.md)**.

> **⚠️ This is a replication, not a discovery — and we say so up front.** The parent benchmark's *own*
> paper already ran a generic-vs-specialized retrieval ablation and found specialization does **not** help:
> o4-mini single-turn, **generic FHIR Query Generator 0.25 vs specialized Retriever 0.22**, with the lift
> to the paper's 0.50 ceiling coming from a **code interpreter** in their **multi-turn** retriever+code arm
> (their single-turn code arm reaches 0.33), not specialization
> ([arXiv 2509.19319](https://arxiv.org/abs/2509.19319), Table 3). Our null **corroborates their
> intra-paper result** on a different tool surface (an MCP server's single generic tool) — what we add is
> the *method* they didn't run: paired stats + a **manipulated context-cap factorial** (they held the cap
> fixed at 32k), which traces the apparent +11pp to context budget. The contribution is the harness and the
> cap finding, **not** the (already-known) "tools don't beat generic" number.
>
> **We further refine the paper's code-interpreter attribution.** Under trustworthy grading on GPT-5.5, the
> code interpreter's lift is **also** a context-budget effect: it is **not significant at matched budget**
> (−3.6pp, p=0.18) and shows up only on the 64% of questions where the no-code agent overflows. So the code
> path doesn't reason better — it avoids the overflow. See [CODE_EXPERIMENT.md](docs/CODE_EXPERIMENT.md) and
> [TRUSTWORTHY_REGRADE.md](docs/TRUSTWORTHY_REGRADE.md).

> **The "token economics, not tool count" headline is consistent with established work, not new.** The
> input-token budget dominating retrieval over large records is documented in Lost-in-the-Middle
> ([2307.03172](https://arxiv.org/abs/2307.03172)), RULER ([2404.06654](https://arxiv.org/abs/2404.06654)),
> Chroma's "context rot," and RAG-MCP (tool-selection collapses ~43%→<14% as the tool pool grows). The
> parent paper itself reports **~3M-token full FHIR records** and that "naive loading consistently failed"
> (arXiv 2509.19319). Community FHIR MCP servers are independently moving the same way — most
> ([WSO2](https://github.com/wso2/fhir-mcp-server) Apache-2.0, [Aidbox](https://docs.aidbox.app/modules/other-modules/mcp),
> Medplum) expose a generic request tool + CRUD rather than a purpose-built typed retrieval catalog, and
> WSO2's answer to payload size is **FHIRPath response-*filtering*** (`response_filter_fhirpaths`, shipped
> mid-2025) — i.e. the field is solving the bottleneck with projection, not tool count. Our FHIR-specific
> contribution is narrower and sharper: naming `_include`/reference-**expansion** tools as a concrete
> budget-overflow anti-pattern (design for response-*filtering*/projection instead).

## Setup (Docker) — verified boot path

The whole substrate is a `docker compose` bundle in [`medplum-eval-bundle/`](medplum-eval-bundle/):
self-hosted **Medplum** (server + Postgres + Redis, MCP enabled) loaded with the open-access
**MIMIC-IV-on-FHIR demo** (100 real de-identified ICU patients, ODbL — no PhysioNet credentialing). The
boot/auth/FHIR/MCP path below was **smoke-tested on macOS + Docker Desktop 28.4.0 on 2026-06-21**
([`medplum-eval-bundle/SMOKE_TEST.md`](medplum-eval-bundle/SMOKE_TEST.md)).

**Prereqs:** Docker + Compose v2, Python 3.10+, **`wget`** (the MIMIC loader needs it — `brew install wget`
on macOS), and an `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`. The judge is OpenAI `gpt-5-mini`, so an
OpenAI key is needed even for the opus arms. **Keys are read from environment variables only** (litellm) —
the MCP ablation path does *not* read `config.yml` (that's only for the upstream GCP agents; copy the
template with `cp config.yml.example config.yml` if you run those). `requirements.txt` includes the MCP SDK
(`mcp`); `pip install -r requirements.txt` covers the harness.

```bash
# 0. harness deps
pip install -r requirements.txt

# Steps 1–2 are SMOKE-VERIFIED on this laptop Docker path. Steps 3–4 are NOT (see "Where this was
# actually run") — the load + ablation only ever ran on EC2; run them here at your own (budget) risk.

# 1. [verified] Stand up Medplum (postgres:16 + redis:7 + medplum/medplum-server:latest, MCP enabled).
#    First boot runs DB migrations — verified healthy in ~75s on a laptop.
cd medplum-eval-bundle && docker compose up -d
for i in $(seq 1 40); do curl -s http://localhost:8103/healthcheck | grep -q '"ok":true' && break; sleep 5; done
#    -> {"ok":true,"version":"5.1.21-...","postgres":true,"redis":true}
#    If it never goes healthy (~2 min), check: docker compose logs -f medplum-server

# 2. [verified] bare-PKCE admin token + confirm the MCP server advertises the generic baseline tool
python3 scripts/get_token.py | head -c 16   # 695-char JWT for admin@example.com / medplum_admin
#    GET 401 unauth / POST tools/list 200 -> {search, fetch, fhir-request}  (fhir-request = the baseline arm)

# 3. [⚠️ NOT smoke-verified on this path] Load the MIMIC-IV-on-FHIR demo (8 gold resource types; needs
#    wget; ~1h EC2-measured, slower on a laptop — use W=4 there; idempotent — PUTs UUID ids so the
#    benchmark's true_fhir_ids match). It hard-errors (not silent) if the download yields 0 files.
bash scripts/load_mimic.sh
#    sanity: curl -s "http://localhost:8103/fhir/R4/Patient?_summary=count" -H "Authorization: Bearer $(python3 scripts/get_token.py)"  # ~100
cd ..

# 4. [⚠️ NOT smoke-verified on this path] Project cost first (a real run is a decision, not a surprise),
#    then run the full ablation matrix + score. Run from the REPO ROOT (steps 1–3 cd'd into the bundle).
export ANTHROPIC_API_KEY=...   # opus arms      export OPENAI_API_KEY=...   # gpt arms + the judge
EVAL_GPT_MODEL=gpt-5.5-2026-04-23 python run_matrix.py --pilot 3            # prints projected $, exits
EVAL_GPT_MODEL=gpt-5.5-2026-04-23 python run_matrix.py --n 25 --n-med 40 --cap 100 --out-dir runs/tier1
python score_taxonomy.py runs/tier1            # raw + answerable-set acc, by-cause taxonomy, paired McNemar
python robustness_analysis.py medplum-eval/results   # judge-free re-score + MDE + Holm (no re-run, no spend)
```

**cwd note:** `get_token.py` / `load_mimic.sh` live in `medplum-eval-bundle/scripts/` (steps 1–3 run from
inside the bundle); `run_matrix.py` / `score_taxonomy.py` / `robustness_analysis.py` run from the **repo
root** (step 4). `run_matrix.py` skips any cell whose output JSON already exists in `--out-dir`, so use a
fresh `--out-dir` to re-run from scratch.

Knobs (all env-overridable, defaults shown): `EVAL_RUN=gpt` (or `opus` — selects the GPT-5.5 staircase
[RUN-2] vs the Opus medication-slice + cap-factorial [RUN-1]; run each into a **separate** `--out-dir` and
score separately), `EVAL_OPUS_MODEL=claude-opus-4-8`, `EVAL_GPT_MODEL=gpt-5`, `EVAL_JUDGE_MODEL=gpt-5-mini`,
`EVAL_STOCK_CAP=32000` / `EVAL_RAISED_CAP=100000` (the cap-factorial), `EVAL_WORKERS=6`. `run_matrix.py`
spawns `treatment_mcp_server.py` per cell with the right `TOOL_SUBSET` (preset
`control`/`cat2`/`cat4`/`validated5`/`arm_ref`/`arm_full8`, or a comma-list) and points the agent at it via
`MEDPLUM_MCP_URL` (the local treatment server at `127.0.0.1:8765/mcp`) — **not** Medplum's own
`8103/mcp/stream` surface, which is only the smoke-test target. Don't override `MEDPLUM_MCP_URL` by hand or
you'll silently measure the wrong server. `--cap` is a **dollar budget** enforced by a shared ledger: it
stops submitting new questions the moment the cap trips, so overspend is bounded to the in-flight batch
(≤ `EVAL_WORKERS` questions) — hard *between and within* cells, not just between them. Two footguns:
(1) the judge defaults to `gpt-5-mini` — if that model id 404s for your account, `score_taxonomy` now
**fails closed** (raises if a cell's judging totally fails, and excludes judge-errored questions from the
denominator rather than scoring them 0), so set `EVAL_JUDGE_MODEL` if needed; (2) the cost projector has
no built-in rate for `gpt-5.5`, so it
falls back to `gpt-5`'s rate — set `EVAL_RATES` for a precise projection.

## Where this was actually run — reproducibility status

**The eval results in [REPORT.md](docs/REPORT.md) were produced on AWS EC2, not on this laptop Docker path, and
most of them are not recomputable from committed artifacts.** The full pipeline (the ~1h MIMIC load + the
multi-hour Opus and GPT-5.5 ablation runs) ran on ephemeral EC2 boxes (`t3.xlarge`, us-east-2) that have
since been torn down. Be precise about what survives:

**Reproducible or preserved from this repo:**
- The Docker **boot path** (steps 1–2): containers up, Medplum healthy, bare-PKCE token, FHIR read/write
  round-trip, MCP advertises the generic `fhir-request` tool — smoke-verified on macOS
  ([`medplum-eval-bundle/SMOKE_TEST.md`](medplum-eval-bundle/SMOKE_TEST.md)).
- The **GPT-5.5 tool-curve summaries and frozen labels**: deterministic re-score outputs, overflow taxonomy,
  LLM-judge accuracies, paired stats, and per-question judge labels are committed as derived artifacts
  (`medplum-eval/results/*.judged.json` + `_scores.csv`/`_paired.json`). For the **trustworthy re-grade**, the
  committed artifacts are the aggregate summary (`medplum-eval/full409_summary.json`) and the per-question
  answer backup (`medplum-eval/full409_answers.json`); the per-question panel/deterministic labels
  (`runs/full409/det_labels.json`, `panel_votes*.json`, codex votes, `human_review.csv`) are local-only and
  gitignored. Exact answer-level
  recomputation still needs the raw answer dumps when a script reads them directly.

**NOT reproducible (lost with a torn-down box):**
- **The entire Opus run** (including the cap-factorial and the headline "only robust effect,"
  cap-on-`arm_ref` p_holm=0.005). The raw per-question data was never pulled off the box before teardown —
  there is **no committed Opus data at all** — so the Opus numbers are reconstructed from console output.

So treat the bundle as a faithful, boot-smoke-verified **recipe** of the EC2 environment. The load-bearing,
verifiable claims are the **null** and the **GPT-5.5 curve** as frozen derived artifacts; exact answer-level
recomputation needs the raw dumps. The **Opus cap finding** remains credible-but-unverifiable. Re-running the
Opus arm on Docker (the scorer now persists per-question labels, so it can't be lost the same way) is still an
open reproducibility item.
(`robustness_analysis.py` prints a provenance banner separating computed-from-committed-data vs reconstructed.)

## Caveats — this is experimental

- **Badly underpowered** (n=25–30/cell): the minimum detectable effect at this n is **~34–46pp** (REPORT
  §9.2), so a commercially-decisive 5–10pp lift is structurally invisible. The honest null is "no tool
  effect larger than ~the MDE," not "definitively none."
- **Uncalibrated judge**: gpt-5-mini LLM-as-judge, no human gold set, κ unmeasured. Mitigated — not
  replaced — by a judge-free deterministic re-score that reproduces the same flat curve (REPORT §9.1).
- **Family-wise correction matters**: after Holm-Bonferroni over all 10 comparisons, **only the
  context-cap overflow effect survives** (p_holm=0.005); every tool-count/tool-design comparison is null
  (REPORT §9.3). No pre-registration; single seed/cell.
- **Incomplete in places**: the 8-tool GPT endpoint never ran (API quota exhausted mid-experiment), so the
  strong "too many tools *hurt*" hypothesis is untested; the 1→6 curve is flat.
- **Narrow scope**: one benchmark, single-patient retrieval only (MIMIC-IV-on-FHIR demo, 100 patients).
  Says nothing about multi-patient cohort/aggregate queries.
- **Control is a faithful re-implementation**, not Medplum's production MCP tool path (description copied
  byte-for-byte; see the harness table).
- **Replication, not discovery**: the generic-vs-typed null was already reported by the parent paper
  (0.25 vs 0.22); "token economics dominate" is established context-bottleneck literature. The novelty is
  the method bundle + the manipulated-cap finding, not the headline number.
- **Single-attempt accuracy** (no τ-bench `pass^k` reliability), and the **1→8 staircase isn't
  chance-corrected** (a random baseline grows with tool count) — read the curve as directional only.

Full limitations + reproducibility status: [REPORT.md](docs/REPORT.md) §1 and §8–§9.

## Repo layout (this fork's additions)

```
treatment_mcp_server.py      # catalog-driven FastMCP server (the arms)
run_matrix.py                # ablation runner: staircase + cap-factorial + $-budget ledger
score_taxonomy.py            # answerable-set accuracy + by-cause taxonomy + paired McNemar/bootstrap
robustness_analysis.py       # judge-free re-score + MDE power sim + Holm-Bonferroni
eval_budget.py               # token-cost ledger with a hard cap
experiment_witness.py        # signed monotonic call inventory
experiment_executor.py       # sealed, crash-conservative trusted call executor
trusted_codex_driver.py      # fixed-environment native Codex runtime adapter
experiment_executor_bootstrap.py # root-owned -I/-B/-S service import bootstrap
experiment_executor_service.py # fixed-bundle, no-raw-egress one-request service
config.yml.example           # template for the upstream GCP agents (cp -> config.yml; gitignored)
agent/mcp_agent.py           # agent that retrieves via an MCP server
agent/ai_agent.py            # agent that routes via Medplum's in-FHIR $ai op
docs/                        # findings, reports, and figures
  ├── FINDINGS.md            # start here: the capstone conclusion
  ├── REPORT.md              # full honest synthesis (numbers, stats, limitations)
  ├── CODE_EXPERIMENT.md     # the code-interpreter result
  ├── FINAL_REPORT.md        # red-teamed A0 / A0' / A5 three-arm control
  ├── TRUSTWORTHY_REGRADE.md # judge-reliability finding + trustworthy re-grade
  ├── results/A11_RESULT.md                   # final V/T/E result, economics, and limits
  ├── results/A11_FORENSIC_AUDIT.md           # post-result leakage and failure analysis
  ├── results/A11_RESULT.json                 # exact finalized aggregate result
  ├── results/a11-artifacts/                  # preserved grading/panel/result manifests
  ├── results/QT4_VALID374_RESULT.md          # confirmatory vocabulary/traversal result + economics
  ├── results/QT4_VALID374_FORENSIC_AUDIT.md # no-cheating and answer-level mechanism audit
  ├── RELATED_WORK.md, ROADMAP.md
  └── images/                # SVG figures
scripts/                     # data setup + run + judge-panel shell scripts
  ├── setup_data.sh          # downloads MIMIC-IV demo + EHRSQL (upstream)
  ├── run_full.sh, run_pilot.sh, run_409.sh   # ablation run drivers
  └── codex_panel*.sh, progress409.sh, ...    # judge-panel + progress helpers
medplum-eval-bundle/         # docker compose substrate + MIMIC loader + smoke test
  ├── docker-compose.yml
  ├── scripts/{get_token,bulk_load,load_mimic}
  ├── README.md              # Docker runbook
  └── SMOKE_TEST.md          # verified boot path
medplum-eval/                # design docs, results data, robustness output
  ├── results/               # committed GPT-5.5 per-question answers + judge labels (*.judged.json) + _scores.csv/_paired.json
  └── ROBUSTNESS_ANALYSIS.txt
```

The original benchmark's code (dataset construction, the upstream agent implementations, `evaluation_metrics.py`,
`fhir_client.py`, etc.) is unchanged from upstream and documented in their materials.

## Data licensing

The committed evaluation data (`final_dataset/*.csv` and derived answer artifacts such as
`medplum-eval/full409_answers.json`) is derived from the
[MIMIC-IV Clinical Database Demo on FHIR](https://physionet.org/content/mimic-iv-fhir-demo/2.1.0/)
(PhysioNet, open access — the fully de-identified 100-patient demo, not credentialed MIMIC-IV),
which is distributed under the
[Open Data Commons Open Database License (ODbL) v1.0](https://opendatacommons.org/licenses/odbl/1-0/).
The same question+answer files are published upstream by the benchmark authors in
[glee4810/FHIR-AgentBench](https://github.com/glee4810/FHIR-AgentBench). This repo's
[LICENSE](LICENSE) (CC BY 4.0, matching upstream) covers the fork's code and documentation; the
ODbL notice and the PhysioNet-required citations for the data are in
[NOTICE-DATA.md](NOTICE-DATA.md).

## Attribution & citation

This repository is a **fork of [glee4810/FHIR-AgentBench](https://github.com/glee4810/FHIR-AgentBench)**
(licensed **CC BY 4.0** — see [`LICENSE`](LICENSE)). The benchmark, dataset, and upstream agent
implementations are the work of the original authors — a joint research effort between **Verily Life
Sciences, KAIST, and MIT**. Everything in [Repo layout](#repo-layout-this-forks-additions) above is this
fork's addition on top of their work.

If you use FHIR-AgentBench, cite the paper:

> Gyubok Lee, Elea Bach, Eric Yang, Tom Pollard, Alistair Johnson, Edward Choi, Yugang Jia, Jong Ha Lee.
> **"FHIR-AgentBench: Benchmarking LLM Agents for Realistic Interoperable EHR Question Answering."**
> ML4H 2025. arXiv:[2509.19319](https://arxiv.org/abs/2509.19319).

```bibtex
@inproceedings{lee2025fhiragentbench,
  title     = {FHIR-AgentBench: Benchmarking LLM Agents for Realistic Interoperable EHR Question Answering},
  author    = {Lee, Gyubok and Bach, Elea and Yang, Eric and Pollard, Tom and
               Johnson, Alistair and Choi, Edward and Jia, Yugang and Lee, Jong Ha},
  booktitle = {Proceedings of Machine Learning for Health (ML4H)},
  year      = {2025},
  eprint    = {2509.19319},
  archivePrefix = {arXiv},
  url       = {https://arxiv.org/abs/2509.19319}
}
```
