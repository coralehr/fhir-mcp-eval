# A11b successor zero-model build

Status: **fresh source and development-only corpus independently reproduced;
efficacy unopened**.

Date: 2026-07-17

Answer-model and judge calls: **0**

## Source-generation result

Two independently staged clean roots on the Mac mini reproduced byte-identical
generation specs and receipts:

- generation spec SHA-256:
  `2c4ecf7ea44f42452799576da7b0d0814ddf933cbb054311d555d56bc20261d4`;
- generation receipt SHA-256:
  `acb5ad3ba2ba8032507d69afc8375d181dc49376392e39564343490f718df0d8`;
- raw-output content SHA-256:
  `57830fe09242d215fac90a7ccdebba24188af290f11290ce1f9cb66a99ab27b4`;
- Patient manifest SHA-256:
  `76c3ffba9f4d2703e4e24b754ac850db9852f04637b38f0717de836a590c8844`;
- patient-partition manifest SHA-256:
  `ac08e626576706d53a5c28cbaca02df1c14b50d820968d61680327701531e3eb`;
- 448 unique synthetic Patients and 1,398,733,725 raw output bytes.

The raw-content and generation-receipt hashes differ from the spent A11b r3
generation, and the successor compiler rejects either spent identity.

This proves a distinct source tree and 448 unique Patients within the successor.
It does not prove zero synthetic Patient-ID overlap with r3: the historical raw
identifier manifest was intentionally not retained, and its keyed cluster hashes
cannot be compared under the successor key. The confirmatory protection is that
r3 packets cannot be reopened and the successor's development and reserved
efficacy assignments are patient-disjoint within this newly pinned population.

## Quarantined infrastructure rehearsal

The first unsealed root produced 448 bundles but failed receipt compilation
because one bundle was 72,770,068 bytes, above the historical 64 MiB per-file
bound. It was not frozen, assigned, or used to build a corpus. No model call or
answer inspection existed. The successor spec was versioned to a still-bounded
128 MiB per-file limit, and two new roots were staged from scratch. Those new
roots are the only candidate source artifacts.

## Development boundary

The successor builder constructs only the 64-Patient development split under
the categorical v2 answer contract. It records that 384 efficacy Patients are
reserved, but writes no efficacy question, packet, gold, or audit artifact.
Before any development answer call, two clean source roots must produce
byte-identical public and audit development trees. Both clean roots passed:

- development public manifest SHA-256:
  `9bf09379d93db80c430b59a59ca79f522e185de6baef048bed40f29017f3e74d`;
- development audit manifest SHA-256:
  `b233b4bdfe9411ccf2720acd3e7850a01f340b73bce46bdc07adda0260362dcc`;
- 64 records in each T0, T1, and E1 packet file;
- 64 physically separate gold and audit records; and
- no `efficacy` path in either public or audit tree.

The registered development gate requires at least one correctness-discordant
pair in both E1-versus-T1 and T1-versus-T0 across the complete 64-question,
three-arm development probe. Direction and magnitude are ignored. A zero in
either contrast prohibits opening the efficacy split.

## Remaining boundary

This artifact is not an efficacy seal and licenses no model call. The next
authorized answer work is a separately anchored 192-answer development probe
with the pinned v2 schema, runtime, witness, executor, deterministic grader, and
complete accepted/all-attempt token accounting. Only a passing content-free
development gate can unlock an independently reproduced efficacy build and new
controller seal.
