# Trusted experiment executor

Status: no-model protocol core implemented and adversarial tests in progress.
There is no production Codex driver, deployed service, A11b controller, or live
answer/panel call yet.

## Public trust boundary

The run account has only three operations:

- `execute_next(run_id, expected_head)`;
- `status(run_id)`; and
- `fetch_artifact(run_id, opaque_artifact_ref)`.

It cannot submit a prompt, descriptor, model, reasoning setting, runtime,
credential, environment, outcome, retry class, token count, or artifact
commitment. `experiment_executor.py` loads those inputs from a sealed schedule
whose domain-separated keyed commitments must equal the signed witness schedule.

The production executor must run with the witness key, commitment key, Codex
credential, and pinned native runtime under a principal the experiment account
cannot mutate. The current fake drivers contain no credential and make no model
calls.

## Durable call sequence

For each scheduled attempt the executor holds one run-wide filesystem lock and
persists these monotonic states:

1. `opened.json`: the signed witness reservation and caller-retained prior head;
2. `spawn_intent.json`: the no-return point before process creation;
3. `capture.json`: an atomic snapshot of raw JSONL, answer, stderr, exit status,
   and the executor-derived native-runtime digest;
4. `bundle.json`: the raw capture plus decoder-derived outcome and token receipt;
5. `close_request.json`: the exact idempotent signed-close arguments; and
6. `result.json`: both signed receipts and the opaque artifact reference.

Every journal publication is staged, file-synced, atomically linked into a
never-replaced final path, and directory-synced. Reads use `O_NOFOLLOW`, bind the
opened descriptor to the path inode, require executor ownership, and verify
canonical JSON. Signed receipts are rechecked against the public chain before a
cached result is returned.

## Derivation rules

An attempt is accepted only when all of the following are true:

- the pinned runtime is executor-opened and hashed before and after invocation;
- the event log is complete UTF-8 JSONL with exactly one clean thread, turn, and
  `turn.completed`, and contains no failed turn, error, or tool event;
- `stderr.log` exists and is empty;
- `answer.json` validates against the sealed schema bytes; and
- input, cached, output, reasoning, and total tokens are all present,
  nonnegative, and reconciled.

Only the exact registered four-event answerless provider-error stream is
retryable. Every other complete-but-invalid capture is terminally
`contaminated`. A durable spawn intent with no durable capture is signed closed
as `indeterminate`, with explicitly unavailable token usage, and aborts the run.

## Crash claim

Without provider idempotency, an external process cannot be proven exactly once
across arbitrary host failure. The executor therefore claims exactly one signed
reservation and **at most one spawn**. Pre-spawn crashes may resume. After
`spawn_intent.json`, missing capture always aborts; it never triggers a second
process. Captured attempts and lost close/result acknowledgements are finalized
idempotently without respawning.

## Remaining deployment gate

Before A11b can spend a token:

1. implement and review the production driver that constructs the pinned Codex
   command and fixed environment internally;
2. deploy witness and executor under the Mac mini `aanishsachdev` principal,
   with `cory` unable to mutate or sudo into it;
3. resolve and pin the Mac mini Node/native-Codex launch path;
4. seal and independently approve the A11b controller, schedule, public key,
   runtime, and executor build; and
5. rerun the fake-driver suite plus a zero-model end-to-end dry run from a fresh
   directory.
