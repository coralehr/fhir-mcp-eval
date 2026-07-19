# A0-prime overflow failure decomposition

Scope: the 262 questions where A0 raw overflowed.

| category | count |
|---|---:|
| correct | 58 |
| still overflow | 97 |
| fit but wrong | 107 |
| cap-drop language | 82 |
| earliest/first among fit-but-wrong | 40 |
| repeated-resource overflow | 54 |

The three primary outcomes (correct, still overflow, fit but wrong) are mutually exclusive. The final three categories are diagnostic subsets.

## Single-block token check

Among 97 still-overflow questions, 0 contain an individual tool block over 32,000 `cl100k_base` tokens; the maximum is 24,815 tokens.

## Code-arm recovery

- Cap-drop-language cases recovered by A5: 55/82.
- Still-overflow cases recovered by A5: 64/97.

## Deterministic definitions

- `cap_drop_language`: fit-but-wrong answer contains cannot find, cannot determine, or truncat (case-insensitive).
- `earliest_or_first`: fit-but-wrong question contains the whole word earliest or first.
- `repeated_resource_overflow`: still-overflow trace requests the same resource_type more than once through get_resources_by_patient_fhir_id.
- `single_tool_block_tokens`: maximum cl100k_base token count among string contents of trace messages whose role is tool.

Score artifact SHA-256: `b0bc19c605aea20ada713613ee1f8d1e1bfb1d814f6bba38a4e77637b3ddc242`.

The companion JSON contains every question ID and its category flags.
