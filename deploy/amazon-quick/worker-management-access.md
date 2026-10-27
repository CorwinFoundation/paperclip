# Quick pilot worker management access

BackBond technical note, 27 October 2026. This template change is a candidate until CI and actual Run Command delivery are verified. It does not by itself establish customer-pilot readiness.

The worker execution role separates management from the Quick runtime role. The execution role can read/write only its client evidence prefix and assume only the exact pilot role with its ExternalId. The runtime role controls the three registered users and isolated chat-log access.

`backbond-client-execution.template.json` replaces `AmazonSSMManagedInstanceCore` with an explicit Run Command transport policy. The AWS-managed policy includes Parameter Store reads across resources; this pilot does not need them. The replacement permits the instance heartbeat plus the four `ssmmessages` and six legacy `ec2messages` channel operations. Every management grant expires at `PilotExpiresAt` and is restricted to the deployment region.

The worker role has no Parameter Store, SSM document, association, inventory or compliance-data reads/writes, and cannot initiate `SendCommand` or `StartSession`. State Manager, patch/inventory collection and customer-owned SSM documents are outside this worker's management contract. Check that an existing worker has no configured associations requiring those capabilities before replacing its policy. This note makes no claim about unrelated roles in the AWS account.

The operator that submits a Run Command can execute with root permissions on the host. Those operator permissions are separate from this instance profile. The scanner container still runs as UID 10001 with a read-only application filesystem, and the worker has no inbound ports. Permission expiry does not stop an already running process or prove immediate session termination; use the independent timed stop and verify actual shutdown.

Before promoting the change: verify CI, inspect the current role/trust/associations, prove that effective Parameter Store reads are denied, run one credential-free command under the reduced role, and confirm a stopped worker with unchanged scan ledger. Preserve the exact template and readback hashes. Do not change an active scan's role or evidence.

Sources: [AWS managed policy](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AmazonSSMManagedInstanceCore.html), [AWS minimal host-management example](https://docs.aws.amazon.com/eks/latest/best-practices/protecting-the-infrastructure.html), [message endpoint reference](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-setting-up-messageAPIs.html), [Run Command root access](https://docs.aws.amazon.com/systems-manager/latest/userguide/ssm-agent-restrict-root-level-commands.html).
