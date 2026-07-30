# Fleet Database Credential Isolation

Status: implementation and canary contract

Security decision: [BEAAA-19603](/BEAAA/issues/BEAAA-19603#document-security-decision)

## Runtime environment policy

Paperclip removes database credentials copied unchanged from the server process
before launching an agent, remote execution target, sandbox target, or workspace
runtime service. The protected set includes the runtime and migration connection
variables and libpq connection/credential variables.

A deliberately supplied value that differs from the server value is retained.
This is the compatibility boundary for a routine application workload using its
own unique, least-privilege database role. It is not a fallback path for the
Paperclip server role. Secret bindings remain the delivery and audit boundary.

The policy is centralized in `@paperclipai/adapter-utils`:

- built-in local/session adapters pass through the common child-process runner;
- SSH and sandbox targets pass through the common remote-environment sanitizer;
- workspace runtime services sanitize their inherited base environment before
  adding explicit service configuration.

Sentinel tests use non-credential values and prove that runtime, migration, and
libpq values copied from the server do not reach a child. A separate test proves
that a different workload-specific role remains usable.

## Isolated low-risk canary profile

Use one internal, non-customer-data agent that does not require Docker, Numbat
records, production access, or deployment authority. The canary environment is a
dedicated Kubernetes sandbox environment with:

- one pod and PID namespace per execution lease;
- `hostPID` and `hostNetwork` absent;
- a non-root user, runtime-default seccomp, no privilege escalation, and all
  Linux capabilities dropped;
- a read-only root filesystem plus private ephemeral workspace, home, cache, and
  temporary volumes;
- no host-path volumes, Docker socket, container-runtime socket, or service
  account token;
- a dedicated service account with no database or Docker control-plane grant;
- only Paperclip control-plane connectivity and explicitly required outbound
  endpoints;
- no database binding for the ordinary agent heartbeat.

Use the session-capable sandbox backend. Apply a configured gVisor or
Firecracker-backed runtime class when the cluster provides it. The stable
single-command Job fallback is not a substitute for a session-resume canary.

### Canary evidence

The exact candidate must demonstrate:

1. heartbeat, checkout, comment, artifact upload, cancellation, and session
   resume;
2. absence of every database sentinel in the agent process;
3. inability to enumerate or read a peer workload environment;
4. inability to reach the Docker/container-runtime control plane;
5. pod specification conformance with the profile above;
6. rollback to a known runnable workload-specific profile without restoring
   inherited credentials, peer-process access, or Docker authority.

Record only pass/fail results, candidate identity, environment/profile revision,
timestamps, and reviewer identity. Do not record commands, credential values,
process environments, sensitive query text, or Numbat records.

## Workload roles and connection lifecycle

Routine applications connect directly with a unique role scoped to the required
database objects and actions. Migration, diagnostics, repair, backup, restore,
and break-glass are distinct identities; none may reuse an ordinary application
or Paperclip server role.

Credential lifetime must be longer than an ordinary transaction and support an
overlap window. Renewal creates a new pool, validates it, switches new work to
that pool, drains the old pool to a bounded deadline, and then closes the old
sessions. Revocation terminates sessions for the revoked database identity;
password expiry alone is insufficient.

## Exceptional maintenance issuer

The maintenance broker is a credential/control-plane issuer, never an inline
database proxy or general SQL shell. Its deterministic policy binds:

- authenticated workload/operator identity;
- issue and approval reference;
- immutable candidate or maintenance window;
- target database and least-privilege role;
- purpose, issue/deny result, validity, renewal, revocation, session closure,
  and outcome.

Issuance is highly available and uses lease overlap with jittered renewal. The
emergency issuer is independently operable and audited, but fails closed unless
the required authorization is present. Logs never include the issued secret,
raw environment, or query text by default.

## Cohort rollback

Isolation and credential issuance are independently revisioned and reversible.
Rollback pauses only the affected cohort and returns it to the prior
workload-specific credential or brokered compatibility lease. It never restores:

- fleet-wide shared database credentials;
- peer procfs/environment visibility;
- Docker/container-runtime authority;
- an unscoped maintenance role.

Broker failure denies the maintenance request. Pool instability may return the
server to the previous server-only credential through the secret manager and a
controlled restart, while agent inheritance remains denied. Any candidate change
supersedes prior QA evidence and requires the canonical QA child to retest the
new immutable candidate.

Shared-fleet or production promotion requires exact-candidate QA approval and
Founder sign-off.
