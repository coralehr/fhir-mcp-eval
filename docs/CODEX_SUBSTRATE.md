# Codex substrate for A6-A9

This repo can now treat Codex CLI as a named eval substrate. The goal is not to hide the agent runtime behind the benchmark; the goal is to version it as part of the experiment.

The runner is `codex_harness.py`. It shells out to `codex exec`, writes the exact prompt per question, captures the Codex JSONL event stream, writes the final structured answer with `--output-schema`, and records a run manifest.

## What gets recorded

Every run writes:

- `manifest.json` with Codex CLI version, Python/platform version, git commit, dirty flag, run config, and SHA-256 hashes for input/schema/packet/skill files.
- `summary.json` with question IDs, prompt hashes, status, return code, answer paths, and event-log paths.
- `questions/<qid>/prompt.txt` with the exact prompt sent to Codex.
- `questions/<qid>/command.json` with the exact `codex exec` argv.
- `questions/<qid>/events.jsonl` with `--json` event output from Codex.
- `questions/<qid>/answer.json` with the schema-constrained final answer.

Generated runs live under `runs/`, which is gitignored.

## Dry-run smoke

This does not call Codex; it only proves prompts, commands, schema hashes, and manifests are generated.

```bash
python3 a6_packet_builder.py \
  --plan-only \
  --limit 1 \
  --output runs/codex-dryrun-packet.jsonl \
  --manifest runs/codex-dryrun-packet-manifest.json

python3 codex_harness.py \
  --mode packet \
  --input final_dataset/full_test409.csv \
  --packet-json runs/codex-dryrun-packet.jsonl \
  --limit 1 \
  --out-dir runs/codex-dryrun \
  --dry-run
```

## A6/A7: frozen packet mode

A6 and A7 should produce one frozen packet per `question_id` before Codex runs. The packet JSON can be a JSON array, a JSON object keyed by question ID, or JSONL with `question_id` on each line.

Start by inspecting deterministic A6 plans without a live FHIR server:

```bash
python3 a6_packet_builder.py \
  --plan-only \
  --limit 20 \
  --output runs/a6_query_aware_plan.jsonl \
  --manifest runs/a6_query_aware_plan_manifest.json
```

Once Medplum is loaded, build the actual frozen A6 packets:

```bash
MEDPLUM_BASE_URL=http://localhost:8103 \
python3 a6_packet_builder.py \
  --output runs/a6_query_aware_packets.jsonl \
  --manifest runs/a6_query_aware_manifest.json
```

For A7, inspect the governed read-layer proxy first. This is a research approximation of Bonfire-style
selection/citation behavior, not a complete governed product contract:

```bash
python3 a7_packet_builder.py \
  --plan-only \
  --limit 20 \
  --output runs/a7_bonfire_plan.jsonl \
  --manifest runs/a7_bonfire_plan_manifest.json
```

Then build live A7 packets with primary query-aware fetches, deterministic reference resolution, terminology summaries, citations, and insufficiency metadata:

```bash
MEDPLUM_BASE_URL=http://localhost:8103 \
python3 a7_packet_builder.py \
  --output runs/a7_bonfire_packets.jsonl \
  --manifest runs/a7_bonfire_manifest.json
```

Then run Codex over those frozen packets:

```bash
python3 codex_harness.py \
  --mode packet \
  --input final_dataset/full_test409.csv \
  --packet-json runs/a6_query_aware_packets.jsonl \
  --out-dir runs/codex-a6-packet-pilot \
  --limit 20 \
  --substrate codex_subscription \
  --live
```

For A7, swap the packet path and output directory:

```bash
python3 codex_harness.py \
  --mode packet \
  --input final_dataset/full_test409.csv \
  --packet-json runs/a7_bonfire_packets.jsonl \
  --out-dir runs/codex-a7-packet-pilot \
  --limit 20 \
  --substrate codex_subscription \
  --live
```

Use A6 for metadata-assisted query-aware in-context projection and A7 for the governed read-layer proxy. The scoring code should treat Codex as an answering substrate, not as the selection layer; selection is frozen before the prompt.

## A8: skills-only falsification

A8 keeps the packet identical while varying the prompt condition. The matrix runner generates
length-matched neutral and placebo control files under the run directory, then delegates each arm to
`codex_harness.py`:

```bash
python3 a7_packet_builder.py \
  --plan-only \
  --limit 20 \
  --output runs/a7_bonfire_plan.jsonl \
  --manifest runs/a7_bonfire_plan_manifest.json

python3 run_a8_skill_matrix.py \
  --packet-json runs/a7_bonfire_plan.jsonl \
  --out-dir runs/a8_skill_matrix \
  --limit 20 \
  --dry-run
```

For a live Codex run, use real A7 packets, remove `--dry-run`, and add `--live`. This spends Codex
quota/time and is not protected by `eval_budget.py`:

```bash
python3 run_a8_skill_matrix.py \
  --packet-json runs/a7_bonfire_packets.jsonl \
  --out-dir runs/a8_skill_matrix \
  --limit 20 \
  --live
```

The four arms are:

1. `A8-F0`: base prompt.
2. `A8-FL`: neutral length-matched pad.
3. `A8-FP`: placebo work routine.
4. `A8-FS`: FHIR retrieval playbook.

Collect each arm back into the existing scorer shape:

```bash
python3 codex_collect_results.py \
  --run-dir runs/a8_skill_matrix/fhir_skill \
  --output runs/a8_score_inputs/codex.a8_fhir_skill.gpt.rep.c100k.json

python3 score_taxonomy.py runs/a8_score_inputs
```

Collector outputs include `true_answer` and `true_fhir_ids`; keep them under ignored `runs/` unless you
produce a redacted artifact package.

The decisive comparisons are skill vs neutral-length pad and skill vs placebo on the same packet hashes.

## A9: Codex + MCP/tools

A9 is the interface-real substrate: Codex uses a configured MCP tool surface instead of a frozen packet.
The current runner compares the generic tool with an expanded read-tool catalog proxy; it does **not** yet
test a full Bonfire governed read-contract MCP tool. Register the local eval MCP server once:

```bash
codex mcp add bonfire-eval --url http://127.0.0.1:8765/mcp
```

Then run the four-arm matrix. In `--dry-run` mode this only writes prompts, commands, and manifests. For
live runs, remove `--dry-run`, add `--live --start-server`, and make sure the Codex MCP registration points
at the same URL:

```bash
python3 run_a9_mcp_matrix.py \
  --out-dir runs/a9_mcp_matrix \
  --limit 20 \
  --dry-run
```

```bash
python3 run_a9_mcp_matrix.py \
  --out-dir runs/a9_mcp_matrix \
  --limit 20 \
  --live \
  --start-server
```

A9 compares:

1. Generic FHIR MCP.
2. Generic FHIR MCP + FHIR retrieval skill.
3. Expanded read-tool catalog MCP proxy.
4. Expanded read-tool catalog MCP proxy + FHIR retrieval skill.

Collect each arm with `codex_collect_results.py` and score the resulting JSON files with
`score_taxonomy.py`, as in A8.

## Claim boundary

Codex subscription runs are reproducible when the agent substrate is versioned, but they are not the cleanest source of dollar-denominated token cost. Use them for pilots, substrate comparisons, and ecological A9 evidence. Keep a smaller API-key anchor run for the final headline table when exact provider billing is load-bearing.
