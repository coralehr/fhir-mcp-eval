# Provider-neutral direct API contract

Status: **zero-model contract implemented with OpenAI-shaped and
Anthropic-shaped fake adapters; no provider SDK, credential, network request,
or model call is included**.

`provider_api_contract.py` defines one narrow seam for future direct-API
replication:

```text
sealed prompt + output schema + provider profile + idempotency key
                               |
                               v
                   one source-pinned adapter call
                               |
                               v
exact request/response bytes + normalized answer/usage
                               |
                               v
            canonical content-only receipt + answer bytes
```

The contract calls an adapter exactly once and never retries. Any adapter
exception after authorization becomes terminally indeterminate because the
request may already have reached the provider. Retry policy remains owned by
the independently witnessed experiment executor.

## Sealed provider profile

Every request binds canonical JSON bytes containing:

- provider, credential-free HTTPS endpoint, API surface, and exact model;
- adapter name/version/source SHA-256;
- uniquely sorted SDK/runtime dependency versions and artifact SHA-256s;
- the complete provider-specific request parameters with tools forbidden;
- usage-mapping version and accepted finish reasons;
- pricing-snapshot SHA-256; and
- explicit provider idempotency support, same-key-only retry policy, and the
  exact HTTP statuses eligible for retry.

The profile rejects credential-shaped fields recursively. Credentials belong
only inside a future trusted adapter process and may not enter the sealed
profile, prompt, receipt, repository, or test fixtures.

## Accepted exchange

An accepted adapter exchange must bind the exact profile and hashed
idempotency key, return nonempty bounded JSON request/response bytes, and
provide:

- a canonical normalized answer matching the sealed output schema;
- a successful HTTP status, provider response ID, and provider request ID;
- the exact registered model and an accepted finish reason;
- zero tool calls;
- bounded latency; and
- complete nonnegative input/cached/output/reasoning/total usage with exact
  reconciliation and a `provider.api` source marker.

The result receipt contains hashes and byte counts, not raw prompt, answer,
request body, response body, or idempotency key. It binds normalized usage,
latency, response identifiers, adapter/dependency receipts, the usage-mapping
version, and the pricing snapshot. A retained raw exchange can be replayed
through `verify_provider_result` without invoking an adapter.

A definite non-2xx provider response uses a separate failure exchange. It
retains the exact request/response hashes, HTTP status, provider request ID,
error code, latency, and complete or explicitly unknown usage. Its retryable
bit must agree with the profile status registry; the contract still does not
retry. Transport exceptions without a definite response remain indeterminate.

## Fail-closed boundary

The implementation rejects profile/source mismatch before the call and rejects
after-call drift in idempotency, returned model, finish reason, HTTP outcome,
response identifiers, answer schema, tool count, usage reconciliation,
failure retry classification, latency, or strict JSON. It makes no claim that
an arbitrary adapter really sent the bytes it reports: that trust comes from
the separately reviewed,
hashed adapter implementation and the restricted witnessed executor.

This module is not yet wired into `ExperimentExecutor`, and it intentionally
does not implement real OpenAI, Anthropic, Google, or other provider adapters.
Doing so before the A11b corpus and causal result are sealed would mix a
transport/model change into the treatment experiment.

## Promotion sequence

1. Finish independent exact-head approval and merging of the no-model A11b
   stack.
2. Pin and double-build the 448-Patient Synthea source, then seal the A11b
   corpus/controller and run T0/T1/E1 first on the existing Codex substrate.
3. Use the registered A11b meaningful-effect, statistical, and safety result to
   decide which contrast, if any, earns cross-model replication.
4. Implement each real provider adapter in its own reviewed slice with exact
   SDK/runtime artifacts, model snapshot, parameters, idempotency behavior,
   usage mapping, and pricing snapshot.
5. Run within-model paired contrasts on the same sealed inputs. Treat the
   direct-API result as a transport/model-family sensitivity analysis, not as
   the same substrate as Codex CLI.

## Verification

```bash
python3 -m unittest tests.test_provider_api_contract -q
python3 -m py_compile provider_api_contract.py tests/test_provider_api_contract.py
```
