# Post-QT-4 / A11 execution plan

Status: **revised after completed A11b r3 exploratory preview**

Updated: 2026-07-17

Inputs:

- [QT-4 valid374 result](results/QT4_VALID374_RESULT.md)
- [QT-4 forensic audit](results/QT4_VALID374_FORENSIC_AUDIT.md)
- [A11 result](results/A11_RESULT.md)
- [A11 forensic audit](results/A11_FORENSIC_AUDIT.md)
- [A11b r3 unregistered exploratory result](results/A11B_R3_UNREGISTERED_EXPLORATORY_RESULT.md)

## Decision

The program has resolved three questions:

1. **Promote fixed, question-only vocabulary selection.** It passed the untouched
   QT-4 holdout and reduced both packet size and answer tokens.
2. **Keep bounded typed traversal as an available evidence mechanism, not as a
   general accuracy claim.** It recovered linked evidence in QT-4 and completely
   solved A11's deliberately path-required cases, but its incremental correctness
   effect on the natural holdout was unresolved.
3. **Do not promote event grouping yet.** A11 had one E-over-T correctness flip,
   and E bundled event grouping with temporal selection and an answerability
   receipt. The causal feature remains unidentified.
4. **Do not promote from the A11b r3 preview.** Its normalized artifacts tie at
   288/384, but the forensic amendment shows that normalization erased 219
   structured insufficiency reasons. Raw T1/E1 answers used one of two explicit
   insufficiency prefixes on all 96 unsupported cases versus 26 for T0. This
   supports a fresh
   prospective T1 test, not retroactive promotion; E1 still added no observable
   benefit beyond T1. Supported cases were ceilinged, and the preview was
   explicitly unregistered because the sealed response schema was incompatible
   with the backend.

No experiment selected a persistent graph database. Storage-engine work remains
an engineering benchmark until it changes an agent-visible packet or a measured
production constraint.

## Execution order

```text
completed evidence
  QT-4: vocabulary promoted; traversal not promoted
  A11: path retrieval works; event grouping not promoted
  A11b r3 preview: strict tie; contract-erased T1 insufficiency signal
                 |
                 v
P0  fix transport/abstention contract and build a fresh discriminating corpus
                 |
                 v
P1  require nonzero development discordance, then seal a fresh T0/T1/E1 holdout
                 |
       +---------+----------+
       |                    |
       v                    v
P2 research              P2 product engineering
   A12 error fidelity       query-time vs materialized-edge
   then A14 authz           byte-equivalence + latency benchmark
```

The next P0 work consumes no answer-model quota. Do not start another A11b or
cross-API answer call until the schema needs no normalization, the unsupported
behavior contract passes development probes, and a separate development set
shows nonzero paired discordance. The 384 efficacy Patients used by the r3
preview are spent for future confirmatory claims.

The witness threat model, interface, crash rules, and deployment gate are in
[EXPERIMENT_WITNESS_PROTOCOL.md](EXPERIMENT_WITNESS_PROTOCOL.md).

The successor development release's single authoritative completed/pending
checklist, exact artifact hashes, approval blocker, and interpretation boundary
are in
[A11B_SUCCESSOR_DEVELOPMENT_SEAL.md](results/A11B_SUCCESSOR_DEVELOPMENT_SEAL.md).
The component checklist below is retained as implementation history; it does
not authorize a model call.

## P0: harden the protocol before another answer run

Implementation status as of 2026-07-15:

- [x] A11 v2 binds and rechecks the actual native Codex executable.
- [x] A11 v3 emits a canonical public digest request and refuses answer or
  panel calls without byte-identical publication in a GitHub-verified,
  commit-pinned external anchor merged to `main` after exact-head approval by
  an independent allowlisted repository member. The verifier also proves the
  reviewed PR changed the exact anchor path, re-fetches its bytes at the
  approved head SHA, and pins approvers by stable GitHub account ID.
- [ ] Publish the actual A11b request from a separate host after its controller
  is sealed; the protocol exists, but no A11b anchor exists before the corpus
  and controller do.
- [x] Implement the PHI-free signed monotonic witness core, including anchored
  schedules, attempt caps, hash-chain replay, durable sequence writes, public-
  key verification, and fail-closed crash/idempotency rules.
- [x] Implement the no-model trusted executor core: caller-proof sealed inputs,
  atomic journals, executor-derived runtime/schema/outcome/token validation,
  signed terminal indeterminate closes, and at-most-one-spawn recovery.
- [x] Implement the production Codex driver with a direct native command, fixed
  environment, private credential gate, pinned macOS no-fork sandbox,
  process-group timeout, bounded durable raw captures, and fake-native
  end-to-end tests. It has made no model calls.
- [x] Independently adversarially review the production driver; its P0/P1 review
  is clean under the pinned no-fork sandbox and no-raw-egress service boundary.
- [x] Implement the admin-owned fixed-layout sealed-bundle loader, canonical
  one-request `execute_next`/content-free `status` transport, fixed redacted
  errors, no raw-artifact route, and A11 v4 anchor binding for the exact witness,
  schedule, build, model configuration, executables, native runtime, sandbox,
  and independently pinned checker key. Cached GitHub approval must carry that
  separate checker's valid signature. It has made no model calls.
- [ ] Bind the exact launcher, root-owned standalone Python tree, authorized key,
  effective sshd policy, flags/environment/cwd, and hidden non-admin executor
  principal into the next controller schema; v4 binds the service core only.
- [x] Implement a deterministic zero-model installation-package compiler for
  the fixed launcher, localhost-only forced key, sshd policy, root-owned source
  payloads, and standalone-Python receipt. This is a review artifact only: it
  has not created the executor account, installed files, reloaded sshd, or
  provisioned credentials on the Mac mini.
- [x] Build and independently reproduce the actual successor development
  bundle/controller and publish its sealed candidate with exact hashes. Exact-
  head approval, merge, commit-pinned external-anchor verification,
  installation, and a fresh zero-model RPC dry run remain pending; no model
  call is authorized by the candidate alone.
- [x] Implement and adversarially review the zero-model A11b T0/T1/E1
  representation compiler on physically separated synthetic development source
  and audit gold. It rederives the plan from the raw question, replays FHIR
  pointers, fails closed on temporal ambiguity and unavailable-reference
  leakage, proves evidence/path equivalence, and bounds all arms together. This
  is not the untouched corpus or a sealed efficacy run.
- [x] Implement and adversarially review the zero-model prospective power gate.
  The frozen exact design derives 384 one-question-per-patient efficacy
  clusters plus 64 development patients under an explicit 30% discordance
  ceiling. The ceiling is evidence-bound and remains an assumption; the spec
  and receipt still require independent exact-head approval before efficacy
  identifiers can be created. See
  [A11B_POWER_GATE.md](results/A11B_POWER_GATE.md).
- [x] Implement the zero-model Synthea generation-receipt verifier. It binds the
  power gate, release/commit, JAR, complete staged Java distribution and
  version probe, exact argv/environment, registered configuration/modules,
  exporter settings, and the complete 448-Patient raw-output tree. This is
  verifier infrastructure,
  not a completed source pin or generation; see
  [A11B_GENERATION_RECEIPT.md](A11B_GENERATION_RECEIPT.md).
- [x] Pin the real Synthea/JAR/Java/configuration successor spec, generate two
  clean 448-Patient roots, and require byte-identical generation receipts. Two
  independent development-only builds are also byte-identical and contain no
  efficacy path. Independent exact-head approval remains part of the
  development controller seal, not a reason to rerun generation.
- [ ] Deploy the witness/executor under a principal the run account cannot
  mutate, bind its public key and schedule into a new externally approved
  controller, and mediate the Codex credential before any A11b live call.
- [ ] Complete the remaining receipt, panel-stream, stderr, nonce, grading,
  dry-run, and double-build gates below.

### Execution receipts

- Hash the actual native Codex executable in addition to the JavaScript launcher.
- Externally anchor the controller, preregistration, packets, native executable,
  and grader digests before the first answer call.
- Reserve and close every model call in an independently owned, signed monotonic
  witness before another call can begin. Local read-only mirrors are defense in
  depth only; they are not append-only against their filesystem owner.
- Retain and hash raw panel event streams under the same no-tool audit as answer
  calls.
- Freeze a benign-stderr classifier so provider warnings do not cause avoidable
  correctness-blind retries.
- Use random opaque question nonces or a keyed construction rather than a
  dictionary-recoverable deterministic question ID.

### Grading

- Route exact codes, displays, aliases, and registered abstentions through a
  deterministic grader first.
- Use an arm-blind panel only where deterministic grading cannot decide.
- Keep responses from the same question or patient out of the same panel batch.
- Add a cross-model-family sensitivity panel for every correctness contrast that
  depends on model judgment.
- Continue reporting accepted and all-attempt tokens separately, including
  discarded transport attempts and panel spend.

### P0 acceptance gate

The controller must fail closed before answering unless:

- every sealed input rehashes;
- the external pre-answer digest exists and matches;
- the native executable and model configuration are bound;
- the independent witness public key, run ID, exact schedule commitments, and
  credential-mediating executor are bound and reachable;
- a complete dry-run receipt can be replayed from a fresh temporary directory;
- gold-only fields are absent from all model-visible payloads; and
- two independent no-model builds produce byte-identical packets and manifests.

## P0: construct the untouched A11b holdout

Historical note: this section records the design requirements used for r3. The
resulting 384 efficacy Patients were consumed by the unregistered preview and
must not be reused as an untouched confirmatory holdout. Treat the requirements
below as a floor for a newly generated successor, with the additional nonzero
development-discordance gate stated above.

A11 left almost no error headroom: T was already 119/120. Repeating that corpus
cannot identify an event-group effect. A11b must be harder without using efficacy
answers to tune difficulty.

Required properties:

- At least three plausible events per question, not two templated roots.
- Tied, missing, date-only, timezone-shifted, and conflicting timestamps.
- Incomplete selected paths plus later or earlier complete distractor paths.
- Balanced first/latest, answerable/unanswerable, family, path depth, and failure
  mode cells.
- Multiple path families with useful-gold yield measured before model calls.
- Identical clinical resources and path receipts across all treatment arms.
- A development slice for packet and difficulty debugging, followed by an
  untouched patient-disjoint efficacy split.
- Sample size and minimum effect of interest locked from a patient-cluster power
  analysis before efficacy packet IDs or answers are opened.

The builder may use deterministic non-PHI augmentation, but no gold answer,
answerability label, selected terminal ID, or failure-mode field may enter the
answer-input materializer.

The development-only compiler contract and its remaining boundary are recorded
in [A11B_EVENT_COMPILER.md](A11B_EVENT_COMPILER.md).
The prospective power assumptions, exact receipt, and non-claims are recorded
in [A11B_POWER_GATE.md](results/A11B_POWER_GATE.md).
The generator/runtime/output trust boundary and remaining real-pin work are
recorded in [A11B_GENERATION_RECEIPT.md](A11B_GENERATION_RECEIPT.md).

## P1: A11b causal isolation

The r3 preview executed this arm structure but tied on every paired item under
the strict normalized endpoint. A
future registered execution may reuse the causal definitions below only with a
fresh holdout, a backend-compatible sealed schema, and demonstrated development
headroom.

Run three paired arms over the identical governed retrieval result:

| Arm | Model-visible treatment | What the contrast measures |
|---|---|---|
| **T0** | Flat bounded traversal with path citations | Reference condition |
| **T1** | T0 plus canonical temporal rank, selected-event marker, and deterministic answerability receipt | Value of explicit selection/completeness aids |
| **E1** | T1 aids plus typed event grouping; identical clinical evidence | Incremental value of event grouping |

The registered contrast family is:

1. **Primary: E1 minus T1.** This is the event-grouping test.
2. **Secondary: T1 minus T0.** This tests the aids as one pragmatic bundle;
   it does not separate temporal rank from the answerability receipt.

The preregistration must choose one multiplicity policy before answering, such
as Holm control or an explicit alpha split. It may not promote both contrasts
from two unadjusted 95% intervals.

Do not include the vocabulary-star V arm. QT-4 and A11 already established the
missing-terminal mechanism, and repeating V would spend quota without resolving
the remaining causal question.

### Outcomes

- Correctness with patient-cluster uncertainty and paired discordance counts.
- Temporal-binding errors, unsupported answers on insufficient evidence, false
  abstentions, and citation/path support.
- Answerability calibration and selected-event accuracy.
- Packet bytes, accepted/all-attempt tokens, wall time, compilation time, and
  retry yield.
- Results by path family, depth, temporal difficulty, and answerability, using
  only preregistered strata for decision gates.

### Promotion rules

- Promote E1 only if its point estimate is favorable, the patient-cluster
  interval excludes zero, and it does not increase unsupported answers or
  citation failures.
- If E1 is not promoted but T1 is, ship the explicit selection/completeness aids
  without claiming event-group efficacy.
- If neither contrast passes, stop answer-accuracy work on event grouping. Keep
  grouping only where it wins a separate usability, auditability, compression,
  or latency benchmark.

## P2: product engineering that does not wait for A11b

Build the graph-neutral context contract now:

```text
canonical versioned FHIR
          |
          v
compileEvidence(plan, principal, purpose, sourceVersion)
          |
          +--> bounded typed traversal receipts
          +--> citations and answerability state
          +--> deterministic model-visible packet
```

Implement explicit FHIR-reference extraction and a rebuildable materialized-edge
projection behind that contract. Compare query-time FHIR traversal with the
materialized projection using zero model calls.

Required engineering gates:

- byte-identical model-visible packets and path citations;
- identical policy denials and source-version receipts;
- correction, deletion, and rebuild behavior verified;
- no cross-patient, cross-practice, or cross-purpose path leakage;
- p50/p95 compilation latency, storage overhead, and rebuild cost reported.

Postgres remains the default implementation. Evaluate a native graph engine only
if the materialized Postgres projection misses a registered production latency or
scale target and an alternative produces identical governed packets.

## P2: next independent model experiment

After A11b, prioritize **A12 error fidelity**. It targets observed malformed and
silently ignored query failures, is independent of event grouping, and maps
directly to a production substrate guarantee. Start with zero-model server probes,
then preregister the smallest paired answer slice where the treatment can actually
change behavior.

Run **A14 principal/authorization** after A12 establishes explicit `denied` versus
`empty` semantics. A13 inference-time scaling and vector retrieval remain lower
priority until these substrate contracts are measured.

## Not in scope now

- A Neo4j, Neptune, or other native graph migration.
- Deeper generic traversal or a traverse-everything agent tool.
- Another V-versus-T path-required rerun with the same ceiling-saturated corpus.
- A bundled "Bonfire graph" accuracy arm that changes selection, retrieval,
  representation, authorization, and storage at once.
- Production or real-chart claims from the synthetic A11 corpus.

## Deliverables

1. P0 protocol-hardening PR and sealed dry-run receipt.
2. A11b dataset/preregistration PR with a zero-model independent replay report.
3. A11b controller PR, externally anchored before execution.
4. Frozen result, forensic audit, token ledger, and explicit promotion decision.
5. Separate materialized-edge equivalence/latency report, with no accuracy claim.
