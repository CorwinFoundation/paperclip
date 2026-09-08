# OTLP emitter contract v1: authorization decision and span outcome

The vendor-local OTLP capture path reads span status and two BackBond-namespaced
authorization attributes. OTel GenAI semantic conventions do not define an
authorization-decision attribute.

Customer emitters may provide:

| Attribute | Accepted value |
| --- | --- |
| `backbond.authz.decision` | `allow`, `deny`, `escalate`, or `not_required` (case-normalized) |
| `backbond.authz.approval_path` | Identifier matching `[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}` |

Both attributes are optional. Values outside these constraints reject the full
batch. They must not contain reasons, transcripts, tool parameters, or other
free text.

The mapper retains the normalized OTLP status code. If a status message exists,
it retains only its SHA-256 digest. It derives `payload.outcome_status` as:

| Condition | Outcome |
| --- | --- |
| decision is `deny` | `denied` |
| decision is `escalate` | `escalated` |
| status code is `ERROR` without either decision above | `failed` |
| otherwise | `completed` |

`failed` describes transport or execution failure and does not manufacture an
authorization fact. Existing content attributes remain rejected because
Evidence Profile v1 has no approved representation for them.
