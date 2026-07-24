#!/usr/bin/env bash
set -euo pipefail

: "${LANGFUSE_BASE_URL:?required}"
: "${LANGFUSE_PUBLIC_KEY:?resolve from scoped Paperclip secret reference}"
: "${LANGFUSE_SECRET_KEY:?resolve from scoped Paperclip secret reference}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
trace_id="beaaa-17702-synthetic-trace"
auth="${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}"
fixture="${here}/synthetic-trace.json"
trace_response="$(mktemp)"
trap 'rm -f "${trace_response}"' EXIT

jq -e '
  [.. | objects | keys[]]
  | all(. != "email" and . != "phone" and . != "customer_id"
        and . != "customer_name" and . != "pii")
' "${fixture}" >/dev/null
jq -e '
  .batch[0].body.metadata.data_class == "synthetic-only"
  and .batch[0].body.metadata.customer_data == false
  and .batch[0].body.metadata.authoritative_evidence == false
  and (.batch[0].body.metadata.evidence_sha256 | test("^[0-9a-f]{64}$"))
' "${fixture}" >/dev/null

curl --fail-with-body --silent --show-error \
  -u "${auth}" -H 'Content-Type: application/json' \
  --data-binary "@${fixture}" \
  "${LANGFUSE_BASE_URL%/}/api/public/ingestion" >/dev/null

trace_ready=false
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error -u "${auth}" \
      "${LANGFUSE_BASE_URL%/}/api/public/traces/${trace_id}" \
      >"${trace_response}" 2>/dev/null &&
    jq -e --arg id "${trace_id}" '
      .id == $id
      and .metadata.data_class == "synthetic-only"
      and .metadata.customer_data == false
      and (.metadata.evidence_sha256 | test("^[0-9a-f]{64}$"))
    ' "${trace_response}" >/dev/null; then
    trace_ready=true
    break
  fi
  sleep 1
done
if [[ "${trace_ready}" != true ]]; then
  echo "synthetic trace was not readable within 30 seconds" >&2
  exit 1
fi

curl --fail-with-body --silent --show-error \
  -u "${auth}" -H 'Content-Type: application/json' \
  --data-binary "$(jq -nc --arg id "${trace_id}" '{
    id:"beaaa-17702-feedback", traceId:$id, name:"eventual_outcome",
    value:1, comment:"synthetic fixture accepted"
  }')" \
  "${LANGFUSE_BASE_URL%/}/api/public/scores" >/dev/null

feedback_ready=false
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error -u "${auth}" \
      "${LANGFUSE_BASE_URL%/}/api/public/traces/${trace_id}" \
      >"${trace_response}" 2>/dev/null &&
    jq -e '.scores[] | select(.name == "eventual_outcome" and .value == 1)' \
      "${trace_response}" >/dev/null; then
    feedback_ready=true
    break
  fi
  sleep 1
done
if [[ "${feedback_ready}" != true ]]; then
  echo "synthetic feedback was not readable within 30 seconds" >&2
  exit 1
fi

unauthorized_code="$(
  curl --silent --output /dev/null --write-out '%{http_code}' \
    -u 'lf_pk_invalid:lf_sk_invalid' \
    "${LANGFUSE_BASE_URL%/}/api/public/traces/${trace_id}"
)"
case "${unauthorized_code}" in
  401|403) ;;
  *) echo "unauthorized request returned ${unauthorized_code}, expected 401 or 403" >&2; exit 1 ;;
esac

jq -n \
  --arg endpoint "${LANGFUSE_BASE_URL%/}" \
  --arg project_id "beaaa-17702" \
  --arg trace_id "${trace_id}" \
  --arg unauthorized_http_status "${unauthorized_code}" \
  --arg retention_end "2026-07-29T23:59:59Z" \
  '{
    result:"PASS",
    endpoint:$endpoint,
    project_id:$project_id,
    trace_id:$trace_id,
    auth_method:"HTTP Basic with scoped Langfuse project public/secret keys",
    data_class:"synthetic-only",
    unauthorized_http_status:$unauthorized_http_status,
    retention_end:$retention_end
  }'
