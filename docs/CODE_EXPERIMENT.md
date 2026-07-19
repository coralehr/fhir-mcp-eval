# Code-interpreter mechanism note — overflow avoidance, not a reasoning win

This note explains the mechanism and implementation contrast behind the
FHIR-AgentBench code-interpreter arm. The canonical A0/A0-prime/A5 result table,
paired statistics, and projection decomposition live in
[FINAL_REPORT.md](FINAL_REPORT.md). The canonical label-construction and judge
reliability evidence live in
[TRUSTWORTHY_REGRADE.md](TRUSTWORTHY_REGRADE.md).

## Setup

The paired comparison used the benchmark's reference agents unchanged:

- `multi_turn_resource` retrieves FHIR resources and serializes them into the
  model prompt.
- `multi_turn_code_resource` retrieves the same class of data, exposes it to
  `execute_python_code`, and gives the model a pointer rather than serializing
  the complete payload into context.

Both arms used GPT-5.5 on the 409-question held-out split. The code arm changes
three things together: interpreter access, a code-tailored prompt, and payload
routing. The benchmark harness's Python execution was in-process, not a hardened
isolation boundary.

## Mechanism

The pooled code advantage is concentrated in the 262 questions where the raw
resource arm exceeded the 32k context cap. When both arms produced real answers,
the code arm had a small, non-significant negative point estimate. On the
resource-real stratum it also paid a reliability tax from execution errors and
buggy generated code. Those exact estimates and their clustered/paired caveats
are intentionally not duplicated here; see the canonical final report.

This identifies an architectural effect: the code route lets a large FHIR
payload remain outside the prompt. It does not identify an intrinsic reasoning
or computation benefit. Interpreter, prompt, and routing remain confounded, so
a same-payload no-execution control is still required to isolate them.

The A0-prime follow-up tested a query-blind strip-and-recency-cap projection. It
recovered some overflow cases but discarded the earliest values many questions
requested and still accumulated repeated retrievals across turns. That arm is a
floor for blunt projection quality, not evidence that projection as a class
cannot match the code route. A query-aware in-context projection remains the
clean comparison.

## Why the interpretation changed

Adversarial review found three separate defects in sequence:

1. The first scorer sent overflow/error outputs to the judge instead of
   deterministically scoring them wrong.
2. The configured single small judge rejected many exact numeric answers.
3. The first deterministic repair misclassified boolean 0/1 gold as numeric.

After those fixes, deterministic grading where possible and independent
three-vote panels agreed on the same mechanism-level conclusion. The detailed
defect receipts and judge leaderboard remain in the grading audit.

## Cost and reproducibility

The recomputed agent cost ledger is in the
[README](../README.md#cost-and-token-accounting-for-the-final-409-question-run).
Judge-panel spend is separate. Aggregate results and a durable per-question
answer backup are committed under `medplum-eval/`; large raw dumps and generated
panel files under `runs/` are gitignored.

The original commands were:

```bash
bash scripts/run_409.sh
python build_labels.py && python final_grade.py
python judge_leaderboard.py
bash scripts/codex_panel.sh && python codex_judge_compare.py
```

They require the Medplum substrate, raw answer artifacts where noted, and funded
model credentials. Re-running them is a new experiment unless the runtime,
model snapshot, inputs, and grading contract are pinned.
