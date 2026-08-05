# Phase-two threat model

The staged surface is hostile. It may attempt traversal, hidden-file access, symlink or device escape, remote requests, script execution, browser persistence, oversized output, resource exhaustion, or source mutation. The patch plan is also hostile and may target undeclared files, stale preimages, ambiguous matches, or excessive changes.

The host runner accepts only a digest-pinned image and one of two fixed commands. Docker provides a read-only root filesystem and input bind, network namespace `none`, empty explicit non-secret environment, bounded tmpfs output/temp storage, dropped capabilities, no-new-privileges, fixed UID, CPU/memory/pid/deadline limits, and unconditional removal. The renderer rejects scripts and remote/executable URLs before browser launch and aborts every non-`file:` browser request. It produces evidence only; it never publishes or writes the source mount.

The preview path verifies a declared regular-file path and SHA-256 preimage, requires each literal replacement to match exactly once, and caps files and bytes. Its only output is a reviewable JSON diff. There is no apply or publish entrypoint.

Residual risk: Chromium is a large trusted computing base. Docker daemon compromise and kernel escape are outside this pack's boundary. Brand quality, genericness, factual claims, and subjective visual hierarchy require blind human/agent review; the automated audit does not approve them.
