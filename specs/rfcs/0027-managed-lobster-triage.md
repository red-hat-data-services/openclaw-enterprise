---
status: Proposed
implementation_status: Not implemented
author: redhat-chai-bot
---

# Proposal: managed Lobster triage in a dedicated worker

- **ID:** RFC-0027
- **Created:** 2026-11-01
- **Last updated:** 2026-11-01
- **Implementation plan:** [Managed triage delivery](../plans/0027-managed-lobster-triage.md)
- **Source baseline:** `eae0575670a1fd691bc1d3e8933f9c43e62cd367`

## Decision and delivery gate

Run the Lobster core engine inside an admitted dedicated Harness managed by
OpenShell. OCC owns manual invocation, authorization, durable checkpoints,
approval, and publication receipts. The first reviewed workflow is
`osac-triage/v1`, which handles one operator-selected Bug and invokes one triage
Agent. It does not implement other role Agents or start a fix.

This is a proposal, not an installed capability. Submit it for human review
before implementing its Driver changes, as required by
[AGENTS.md](../../AGENTS.md#submit-an-rfc-before-changing-a-driver-contract).
Submission permits parallel implementation and review unless a specific hold
requires waiting.

## Current consumers and missing contract

[Production composition](../../apps/controller/src/composition/production.ts)
constructs `PostgresPlatformState`, `OpenClawController`, and selected Compute,
Sandbox, credential, and Plugin Drivers. The
[worker lifecycle](../../docs/flows/controller-worker.md) owns queued effects.
The [Compute contract](../../packages/contracts/src/drivers/compute/index.ts)
manages Agent revisions; the
[Sandbox contract](../../packages/contracts/src/drivers/sandbox/index.ts)
provisions, exposes, observes, and removes Harnesses. Neither declares managed
workflow invocation, completion, checkpoint transfer, or cancellation.

The existing
[OpenShell implementation](../../apps/controller/src/drivers/sandbox/openshell.ts)
uses private `execSandbox` for workspace preparation. Its
[client](../../apps/controller/src/drivers/sandbox/openshell-gateway-client.ts)
returns an exit code, not a workflow result or an authenticated Agent completion.
Turning this into workflow execution changes required Driver behavior, even
without changing a signature. Calling this concrete client directly from OCC
would bypass the owning contract.

[PluginDriver](../../packages/contracts/src/drivers/plugin/index.ts) provides
catalog discovery and deployment validation, not this lifecycle. A catalog entry
alone cannot connect the feature. The
[native admin proxy](../../apps/controller/src/http/native-admin.ts) grants
interactive access under a separate pilot; it is not an exact-operation workflow
approval or a publication transport.

## Ownership and proposed interfaces

Extend the Compute contract with an optional managed-workflow facet. It admits
one immutable request bound to Installation, Namespace, Agent revision, run ID,
workflow digest, input digest, attempt, deadline, and a live worker claim.
Operations start or resume the run, observe its structured checkpoint or
completion, and cancel its owned work. The request accepts a reviewed workflow
ID and structured inputs, never a command, pipeline, path, URL, or caller-chosen
Sandbox identity. Unsupported Drivers refuse; they do not run on the controller.

Kubernetes Compute resolves the revision's placement and delegates through a
corresponding Sandbox facet. OpenShell verifies that the Sandbox belongs to that
revision and launches only the image-owned adapter. The adapter runs Lobster and
returns a size-limited envelope bound to the invocation. These are proposed
contracts, not existing native APIs. Driver owners review the types and transport
alongside implementation after RFC submission.

OCC owns workflow state through the existing
[repository transaction boundary](../../packages/occ/src/ports/platform-unit-of-work.ts)
and [PostgreSQL state](../../packages/occ/src/state/postgres-state.ts), not worker
temporary files or process-local Maps. Add a workflow repository and schema
constraints for exact scope, unique invocation keys, approval consumption,
publication intents, external IDs, and receipts. Commit queued operations and
audit records with state changes. The durable worker rechecks original authority,
revision identity, and its current claim before each effect.

## Runtime and credentials

Select `@clawdbot/lobster` version `2026.10.1` from the official
[tagged source](https://github.com/openclaw/lobster/tree/85307ff538c0052f989f50c1c94f2ea7f199971a).
Record the resolved package integrity and built runtime digest during delivery;
neither is established by this RFC. The source exports `runToolRequest` and
`resumeToolRequest` from `./core` and accepts `assertInvocationCurrent` there.
The generic SDK is not evidence of that hook. Lobster requires Node 22 or newer;
OCE's stricter toolchain requirements still apply.

Install the pinned engine, adapter, and workflow outside the writable checkout
in the reviewed runtime image. Replace the default command registry with only
the triage operations. Refuse shell execution, arbitrary pipelines, dynamic
workflow paths, plugin installation, and model-supplied egress destinations.
Untrusted issue, comment, and repository text remains delimited data. It cannot
change workflow steps, tool permissions, or Agent instructions.

The initial proposed budgets are 10 minutes of active execution per run,
1 MiB per captured text artifact, 8 MiB of retained evidence, a 256 KiB result,
one triage model invocation, and three transport attempts. Approval expires after
24 hours and does not keep a process alive. Limit exhaustion stops the run with
`BLOCKED`; capture is not silently truncated. Cancellation terminates only work
owned by this run and records any uncertain external effect.

Use the selected Harness's authenticated invocation and completion protocol,
with an exact invocation ID and model credential mediation. Do not assume
`openclaw.invoke` inherits authentication or invent an Agent HTTP endpoint.
Verify this path in the pinned runtime before admitting the feature. Missing
support returns `BLOCKED`, not an alternate unrestricted model call.

Jira access needs a reviewed adapter and credential route built on existing
Secret/CredentialSource ownership and selected credential delivery. Current
plugin routes do not establish a Jira route. Keep credential values out of
workflow inputs, Agent context, checkpoints, logs, and receipts. Enforce the
configured origin, project, issue key, and allowed operations at the effect
boundary; never supply a broad controller credential to the Agent.

The stock gateway-hosted Lobster plugin is not the execution path. Do not remove
its sandbox guard or enable embedded OpenClaw. This remains a development pilot:
the [OpenShell qualification blockers](../../docs/reference/drivers/openshell-sandbox.md#remaining-qualification-work)
for service/management separation, caller-to-Sandbox identity, and provider-file
lifecycle remain unresolved by this proposal.

## Workflow and result

1. Authenticate a manual caller and authorize the exact Agent operation. Capture
   the issue and comments, configured eligibility, and opt-out state. Refuse
   ineligible issues and missing access. No polling is required.
2. Gather approved source, tests, documentation, and related fixes. Capture each
   repository commit SHA and artifact digest; never resolve Agent references
   against a later checkout. Missing prerequisites produce `BLOCKED`.
3. Invoke one triage Agent with read-only evidence and fixed instructions. Require
   `AUTO_FIX`, `NEEDS_INFO`, or `NOT_FIXABLE`, evidence and diagnosis or explicit
   gaps, justified fix scope and steps, and a runnable verification plan or an
   explanation of why verification cannot yet be specified. Commands are plans,
   not executed issue text. Never claim a test ran without captured execution.
4. Validate the structured schema and each evidence reference against captured
   artifacts and source revisions. `AUTO_FIX` requires evidence, fix scope, and
   runnable verification. A confidence number is insufficient. Validation proves
   structure and traceability, not semantic truth; pilot approval covers diagnosis.
5. Persist the result and pause before publication. An authenticated, authorized
   human approves the exact snapshot. Publish only after rechecking it.
6. Publish one constructive Jira comment, reconcile the exclusive triage label,
   and return a durable receipt with result digest, approver audit reference,
   publication state, comment ID, and label outcome. Do not activate fix execution.

Require operator configuration mapping each disposition to one distinct triage
label, plus eligibility and opt-out rules. Missing mapping blocks invocation.
Examples and fixtures use synthetic labels. Preserve unrelated labels
using incremental operations and remove only other configured triage labels.
The configured no-autofix rule vetoes publication as an automatic-fix candidate.

## Approval, restart, and uncertain effects

Persist an approval digest over relevant issue content and comments, eligibility,
configuration, captured source revisions, result and plan, workflow and runtime
versions, exact Agent revision, and expiry. Derive the approver from OCC's
authenticated identity and exact-resource authorization, never Agent fields or
raw Lobster resume-token possession. Re-read the issue and authority before
resume and before each publication effect. Reject stale, unauthorized, expired,
or consumed approvals without new effects. An unchanged authorized retry returns
the stored receipt or current reconciliation state; it does not rerun the Agent.

Persist Lobster continuation state through the owning repository, or restore it
into private worker storage from that repository before resuming. Local engine
state cannot be the durable authority. Claim and consume approval transactionally;
do not hold a database transaction open across network calls.

Record a unique publication intent before sending. Bind a non-secret marker to
the approved result and reconcile comment IDs, authorship, and content after a
timeout or process restart. An uncertain create is not permission to create
again: stop for reconciliation if absence cannot be established. Serialize
publication for one issue; apply label deltas against a fresh read. Persist
comment and label outcomes separately so recovery does not duplicate the comment
or claim an incomplete label update succeeded. A stale snapshot after a partial
effect records the partial receipt and blocks remaining effects pending review.

## Delivery and alternatives

The [implementation plan](../plans/0027-managed-lobster-triage.md) connects the
manual caller, Driver adapter, pinned runtime, validator, PostgreSQL checkpoints,
approval, and Jira publisher in one delivery. Qualification requires actual engine
execution, authenticated model completion, and installed isolation checks;
source inspection and fixture coverage cannot establish those outcomes or live
Jira behavior.

Keep the current absence of managed workflows until this contract is submitted
for review. A standalone demo fails the caller requirement. Direct core calls to
OpenShell bypass Driver ownership; a gateway-hosted engine violates the selected
isolation boundary. An extra orchestration service is unnecessary when the
existing durable worker and platform repositories own these responsibilities.

Driver maintainers must resolve the invocation/completion transport and review
credential mediation before runtime admission. Security reviewers must assess
the approval and publication boundary. These reviews cannot be replaced by
Lobster tokens, synthetic fixtures, or a successful catalog installation.
