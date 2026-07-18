# A11b post-result forensic analysis plan

Status: prepared before the first A11b model call.

This analysis is separate from registered grading. It cannot change a label,
contrast, confidence interval, safety gate, or promotion decision. Its purpose
is to decide whether the registered result is reliable and what mechanism the
result actually supports.

## Required inputs

1. The exact `a11-controller-v4` controller and SHA-256 sidecar.
2. The root-owned sealed service `bundle.json`, containing all 1,152 exact
   prompt byte strings.
3. A post-completion `ExperimentExecutor.export_completed_run()` document,
   retaining signed receipts and captured event streams.
4. The immutable final-result directory written by `a11b_postprocess.finalize`.

The raw audit emits counts and hashes only. It never writes prompts, answers,
resource IDs, question IDs, or event text into its report.

## No-cheating gate

`a11b_forensic_analysis.py raw-audit` fails closed unless:

- all 1,152 sealed prompts hash to their controller schedule entries;
- prompt scans find zero audit-only fields, gold phrases, or arm identities;
- every schedule slot has exactly one accepted attempt;
- accepted stderr is empty;
- every accepted event stream is valid JSONL, contains exactly one usage
  receipt, and contains only thread/turn markers plus one agent message;
- event-level usage exactly matches executor usage, and accepted/all-attempt
  per-arm token totals exactly match the final registered result;
- no tool, shell, file, web, MCP, or non-message event appears;
- every attempt has complete provider usage; and
- reserved and closed model-call counts reconcile exactly.

## Registered-result replay

`a11b_forensic_analysis.py final-report` verifies the final manifest and result
receipt, exact 384-by-3 coverage, all-attempt economics, safety comparisons,
and replays the registered promotion function from the result's contrasts and
behavior counts. It refuses to produce a passing report without the separate
raw no-cheating audit.

## Mechanism interpretation

- `promote_e1`: grouping improved correctness beyond identical evidence,
  temporal aids, and answerability receipt, without worse unsupported answers,
  citation failures, or temporal-binding errors.
- `promote_t1`: deterministic aids helped, but event grouping did not earn an
  accuracy claim.
- `do_not_promote`: stop event grouping as an answer-accuracy thesis. Retain it
  only for a separately registered auditability, compression, usability, or
  latency study.

The report must still describe where discordances live by family, depth,
temporal policy, difficulty, and answerability. Those breakdowns are
descriptive and cannot override the registered decision.

## Remaining human review

After both machine checks pass, an authorized reviewer should inspect only the
discordant question set and a stratified sample of agreements. The review asks
whether visible evidence summaries support the coded error class and whether
any gain is concentrated in a single family or artifact. Hidden
chain-of-thought is neither available nor required.
