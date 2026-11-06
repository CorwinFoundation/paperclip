# Host-local fixed-workflow diagnostic issuance

Source candidate only. Production identity values must be established by the
execution owner before invocation. Synthetic test identities grant no authority.

The operator entry point accepts no arguments. It reads only
`/etc/paperclip/fixed-workflow-diagnostic/binding.json` and writes only
`/etc/paperclip/fixed-workflow-diagnostic/credential.json`.
Both files must be regular root-owned files with mode 0600 and one hard link.
Every ancestor must be a root-owned directory without group/world write access;
symlink ancestors and symlink files are rejected. The destination must initially
be absent. Provision the enclosing directory as root with mode 0700.

Binding schema (strict JSON object; no extra fields):

- `version`: integer `1`
- `companyId`: explicitly authorized company UUID
- `agentId`: explicitly authorized existing agent UUID in that company
- `parentIssueId`: explicitly authorized existing issue UUID in that company
- `workflowId`: exactly `0b845d09083c12076c3877efbf7a50ba`

The source does not derive company or agent identity from the workflow ID.
The operator must use reviewed source and its matching dependencies, with
`DATABASE_URL` supplied through the protected operator environment for the
explicit intended instance. The command does not initialize a database, run
migrations, change API authorization, or activate an agent.

Run as root from the reviewed source checkout, without command-line options:

```sh
node server/node_modules/tsx/dist/cli.mjs scripts/provision-fixed-workflow-diagnostic.ts
```

The command prints no credential. Its protected destination contains
`{ "binding": <validated binding>, "keyId": <native key UUID>, "token": <secret> }`.
Keep this file secret; do not attach it to issues or place it in source control.
The native key is always `fixed_workflow_read_only`, named
`host-local-fixed-workflow-diagnostic-v1`. Native services validate company,
agent membership/status, and parent company before issuance. Existing middleware
limits methods and routes; this command does not introduce an API auth exception.

A successful rerun verifies the receipt's exact binding, active native key ID,
agent/company, exact scope and token hash, then reuses the existing credential.
A mismatched, revoked, missing, duplicate or orphaned native key fails closed.
An exclusive destination prevents simultaneous issuers from both creating keys.
Interrupted runs can leave an empty receipt or a key without a complete receipt;
running again does not rotate or duplicate credentials. The execution owner must
inspect native key state through supported operator services, revoke the orphan
if appropriate, and remove the unusable protected receipt before an authorized
retry. Failed output or activity recording attempts revoke the newly created key.

Tests inject temporary paths and the current file owner only in the library test
harness. The operator entry point fixes root ownership and both protected paths;
it exposes no identity, path, scope or key-type override.

Rollback before activation: remove the unactivated entry point. This source task
performs no credential issuance or deployment. A later activated key must be
revoked through the native operator path before removal of its secret receipt.
