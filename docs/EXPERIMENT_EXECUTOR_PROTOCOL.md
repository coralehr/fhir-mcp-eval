# Trusted experiment executor

Status: no-model protocol core, production Codex driver, fixed-bundle loader,
restricted one-request transport, and A11 v4 external-anchor schema implemented.
The driver is independently adversarially reviewed. The external-approval cache
is checker-signed with a verification key pinned in immutable service code.
There is no installed service bundle, forced-command transport, externally
approved A11b controller, or live answer/panel call yet.

## Public trust boundary

The executor core has three operations:

- `execute_next(run_id, expected_head)`;
- `status(run_id)`; and
- `fetch_artifact(run_id, opaque_artifact_ref)`.

The restricted run-account transport must expose only `execute_next` and
content-free `status`. Raw `fetch_artifact` access is reserved for the trusted
checker/admin path: process streams are retained for audit, but must not become
an egress channel from the credential-owning principal.

It cannot submit a prompt, descriptor, model, reasoning setting, runtime,
credential, environment, outcome, retry class, token count, or artifact
commitment. `experiment_executor.py` loads those inputs from a sealed schedule
whose domain-separated keyed commitments must equal the signed witness schedule.

`experiment_executor_service.py` makes this boundary executable. It accepts one
canonical UTF-8 JSON request of at most 4 KiB followed by EOF. Every request
must carry the exact service kind/version and be either `execute_next` with a
run ID plus expected witness head or `status` with only a run ID. Duplicate
keys, alternate serialization, trailing data, extra fields, unknown operations,
and all caller-supplied paths/model inputs fail before dispatch. Status is a
minimal projection without attempts or receipts; execution returns signed
receipts, reconciled economics, outcome, and the artifact commitment but never
the artifact reference or raw capture. All errors are fixed canonical codes
without exception text, paths, prompts, or tracebacks.

The loader reads a fixed admin-owned directory, not request, argv, environment,
or `SSH_ORIGINAL_COMMAND` configuration. Production startup additionally
requires isolated Python `-I -B`, an exact scrubbed environment, fixed working
directory/private `TMPDIR`, and root-owned non-writable source files with no
loaded bytecode cache. It descriptor-validates a mode-0400
private `bundle.json`, exact 32-byte commitment key, mode-0600 single-link
witness key, dedicated Codex home, scratch/state roots, code identities,
Python, ssh-keygen, native runtime, sandbox executable/profile, controller,
commit-pinned anchor locator, and checker-signed verification receipt. The
checker signing key remains on a separate host; only its exact public key is
present in the bundle/controller and independently pinned in code. It derives the
witness public identity from the private key, recomputes every invocation
commitment, requires each invocation's model/reasoning/timeout and native runtime
to equal the controller and anchored public service binding, and only then
constructs the driver, ledger, and executor. `bundle.json` is committed first;
the separate immutable A11 v4 controller binds that HMAC, avoiding a
self-referential controller/bundle seal.

The production executor must run with the witness key, commitment key, Codex
credential, and pinned native runtime under a principal the experiment account
cannot mutate. `trusted_codex_driver.py` implements the credential-mediating
native runtime adapter; tests use only sentinel credentials and fake native
processes, so its implementation and verification have made no model calls.

## Production driver boundary

The driver executes only the absolute `runtime_path` already bound into the
sealed invocation, under an independently pinned macOS `sandbox-exec` policy
that denies process creation. It never invokes Node, a JavaScript launcher, a
shell, or a `PATH` lookup. Its command fixes approvals to `never`, sandboxing to `read-only`,
uses a fresh empty working directory, ignores user config and rules, disables
ambient shell-environment inheritance, emits JSONL, and passes the prompt as
exact bytes on stdin. Model and reasoning effort come only from the sealed
invocation; reasoning effort is restricted before a witness call opens.

The child receives only `HOME`, `CODEX_HOME`, a private `TMPDIR`, a fixed system
`PATH`, locale, and `NO_COLOR`. The driver requires an executor-owned mode-0700
Codex home and scratch root plus an executor-owned, single-link, mode-0600
regular `auth.json`, rechecking those properties before every spawn. It reads
credential bytes only into ephemeral memory to reject exact credential/token
material in raw process outputs; it never logs, copies, or persists those
values. Stdout and stderr are created exclusively with no symlink following,
the child uses umask 077, a new process session, and an inherited hard
`RLIMIT_FSIZE` per-file capture cap. Every exceptional path kills and drains the
process group, normal exits reject surviving descendants, and capture
files/directories are synced before control returns.

The restricted service must execute one request in one isolated, single-threaded
Python process. The driver installs `RLIMIT_FSIZE` with `preexec_fn`; it must not
be embedded in a long-lived threaded Python server. A forced-command transport
may start a fresh process for each request, load the sealed bundle, execute once,
return a canonical response, and exit.

The Mac mini containment executable is root-owned `/usr/bin/sandbox-exec`,
SHA-256
`2f447acfae7a954ccb5a1bcf3a1955f69b1a8549c434ddff643df98fd3cb33ba`,
with the exact profile `(version 1)(allow default)(deny process-fork)`. A live
zero-model probe proved that the policy rejects a child process while the pinned
Codex native binary still returns `codex-cli 0.144.1` with empty stderr. The
service must bind and recheck the sandbox path, digest, and profile on every
call.

The Mac mini runtime candidate is the admin-owned native `codex-cli 0.144.1`
binary with SHA-256
`29915529b97697def1a957b0505e770aa6a45744435d62fc263e98d7619e167a`.
The older NVM-installed binary is explicitly ineligible: macOS AMFI kills it
because its Developer ID certificate is revoked. A11b must bind the valid native
path and digest, never the rejected launcher tree.

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

1. build and independently review the actual sealed A11b bundle/controller and
   publish its v4 anchor from a separate host;
2. install the fixed one-request wrapper and localhost-only forced-command SSH
   key under a dedicated Mac mini executor principal, with no raw-fetch route and
   with the run account unable to mutate or sudo into it;
3. create a private dedicated `CODEX_HOME`, validate its credential metadata,
   and require a clean zero-model runtime probe;
4. have the separate checker verify GitHub exact-head approval and sign the
   offline receipt, then prove the installed code, Python, ssh-keygen, runtime,
   sandbox, witness, and schedule match the externally approved binding; and
5. rerun the fake-driver suite plus a zero-model end-to-end dry run from a fresh
   directory.
