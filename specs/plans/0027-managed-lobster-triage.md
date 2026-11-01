---
rfc: ../rfcs/0027-managed-lobster-triage.md
---

# Implementation plan: managed Lobster triage

- **ID:** RFC-0027
- **Delivery status:** Planned
- **Authority:** [Managed Lobster triage proposal](../rfcs/0027-managed-lobster-triage.md)
- **Source baseline:** `eae0575670a1fd691bc1d3e8933f9c43e62cd367`

## Outcome and scope baseline

Implement the RFC's manual, one-Bug workflow through the regular Agent lifecycle.
The connected result includes actual Lobster execution, one authenticated triage
Agent invocation, captured evidence, structured validation, pilot approval, Jira
publication, label reconciliation, and restart-safe receipts. All behavior is
unimplemented at this baseline. This plan does not reduce the delivery to an
RFC, catalog entry, or test-only adapter.

Changes stay in the active contracts, OCC, controller, managed runtime, and
synthetic test fixtures. No other role Agents, polling, fix execution, new
orchestration platform, or live-service writes are included. Driver changes are
a required supporting change, not independent refactoring. The source tracing
in the RFC explains why the existing lifecycle does not admit this operation.

## Prerequisite and interfaces

Submit RFC-0027 for human review before Driver implementation. Resolve any
specific hold; otherwise implementation and RFC review proceed in parallel,
without waiting for final RFC acceptance. Current source has no managed
workflow dispatch or authenticated completion contract. The proposed Compute
facet delegates exact revision-scoped operations to Sandbox and never accepts
shell commands or arbitrary workflow paths. Review these interfaces rather than
calling concrete OpenShell management methods from core.

Use the existing PostgreSQL transaction and worker claim owners for checkpoints,
approval consumption, publication intents, and receipts. Add repository methods
and constraints, not a second persistence mechanism. Jira credentials need an
explicit reviewed route; neither model credentials nor repository credentials
authorize Jira writes.

## Ordered implementation

1. Extend `packages/contracts/src/drivers/compute/index.ts` and
   `packages/contracts/src/drivers/sandbox/index.ts` with the reviewed invocation,
   observation, resume, and cancellation contract. Curate exports in the package
   root. Add admission denials for unsupported or embedded placements and
   mismatched runtime/workflow pins. Exercise real controller callers in tests.
2. Add workflow state to `packages/occ/src/state/platform-state.ts`,
   `ports/platform-unit-of-work.ts`, `state/postgres-state.ts`, and
   `state/postgres-schema.ts`. Include scope constraints, unique publication
   keys, transactional approval consumption, and work-claim fencing. Match the
   in-memory implementation for fixture use, but prove restart and competing
   claims against disposable PostgreSQL.
3. Integrate the reviewed adapter through
   `apps/controller/src/drivers/compute/kubernetes/index.ts`, the OpenShell
   Sandbox implementation, and `composition/production.ts`. Pin Lobster and the
   image-owned versioned workflow under `deploy/runtime/`; record resolved
   integrity and image digest. Keep workflow files outside the checkout and
   close the engine registry to approved operations. Prove run, pause, restore,
   resume, budget enforcement, output limits, and cancellation with the actual
   pinned engine inside the worker path.
4. Add the triage validator, evidence snapshot, and fixed Agent instructions in
   their owning workflow/runtime modules. Implement configured eligibility and
   disposition labels with synthetic examples. Verify exact captured references,
   every disposition, and `BLOCKED` on unavailable prerequisites. Establish the
   selected runtime's authenticated Agent invocation and terminal completion;
   fail admission if this cannot be demonstrated. Do not substitute an invented
   endpoint or a direct model call.
5. Add the manual workflow handlers under `apps/controller/src/http/`, their
   schemas under `packages/contracts/src/api/`, and OCC lifecycle methods. Wire
   handlers through the actual server composition and queue dispatch. Authorize
   exact Agent operations; derive approval identity from authenticated context.
   Add the reviewed Jira credential and operation adapter under controller
   Drivers/Backends, with least-privilege Secret/CredentialSource references.
   Persist intents before effects and reconcile uncertain comments and label
   outcomes. Do not retry an uncertain comment create blindly.
6. Extend regular Agent integration through those handlers, real composition,
   durable worker, and repositories using synthetic Jira evidence and approved
   disposable infrastructure. Register new TypeScript tests in
   `scripts/ci/test-suites/`. Update owning references, the worker/OpenShell
   flows, runtime instructions, API generation, permissions/database/configuration
   cheat sheets, and documentation navigation with implemented behavior only.

## Verification contracts

Every row is required for implementation, not claimed by this plan.

| Contract                      | Required proof                                                                                                                                                          | Current result                         |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| Real consumer and engine      | Manually invoke through the actual HTTP/controller/worker registration, execute pinned Lobster, observe one Agent completion and durable receipt                        | Not implemented                        |
| Dispositions and evidence     | Synthetic `AUTO_FIX`, `NEEDS_INFO`, `NOT_FIXABLE`; missing access, missing mapping, opt-out, malformed result, and references outside captured commits                  | Not implemented                        |
| Authentic approval            | Authoritative approver, denied principal, expiry, changed issue/comments, changed workflow/runtime/source/result, and replay have the specified effects                 | Not implemented                        |
| Restart and races             | Disposable PostgreSQL; restart at pause and each publication transition; competing claims and consumed approval cannot duplicate effects                                | Not implemented                        |
| Uncertain publication         | Transport loss before/after comment creation and label update; reconciliation finds only the approved marker and preserves unrelated labels                             | Not implemented                        |
| Containment                   | Real engine rejects arbitrary paths/commands and exceeds no budgets; exact scope/egress denials, credential redaction, owned cancellation                               | Not implemented                        |
| Required report accounting    | Report all selected required cases, fail on missing or skipped required outcomes; fixture success is not live-runtime evidence                                          | Not implemented                        |
| Installed isolation and model | Authorized disposable OpenShell/Kubernetes environment, digest-pinned images, provider-file lifecycle, caller identity, allowed/denied egress, authenticated model turn | Not run; qualification blockers remain |

Use the current `package.json` commands: `pnpm check:workspace`, `pnpm lint`,
`pnpm format:check`, `pnpm typecheck`, `pnpm cli:check`, `pnpm cli:test`,
`pnpm openapi:check`, `pnpm test:conformance`, and `pnpm test:integration`.
Start implementation testing with the new registered workflow files via the
repository's `node --test tests/integration/<name>.test.ts` convention, then
broaden in the separate validation phase. Exact new filenames are assigned when
the reviewed interfaces establish their owning modules.

For PostgreSQL and installed-runtime verification, follow
[PostgreSQL testing](../../docs/testing/postgresql.md),
[Kubernetes testing](../../docs/testing/kubernetes.md), and
[OpenShell testing](../../docs/testing/openshell.md). Use a separately authorized
disposable installation, limited application database role, explicit enforcing
cluster selector, and authorized model credentials. No ticket or live Jira
environment is authorized by this plan. Use synthetic external-service fixtures
until a specific test issue and permission for writes are supplied.

## Delivery and review sequence

Submit the RFC and plan as a documentation-only review. After submission, develop
the implementation separately and link its PR to the RFC review. Keep the RFC
consistent with the implementation, resolve specific holds, and merge linked PRs
in dependency order, as required by
[Contributing](../../CONTRIBUTING.md#prepare-a-pull-request). Do not add runtime
code to the RFC-only review without an explicit scope decision.

Use the supported Node version, exact pnpm version, and frozen dependency graphs
required by [Contributing](../../CONTRIBUTING.md#set-up-a-development-checkout).
Run documentation checks for the proposal; do not run runtime tests solely for
documentation. Keep full implementation validation and publication as separate
steps. Mark this plan Completed only when the implementation contracts are proved.

## Manual Notes

## Changelog

- 2026-11-01: Record source consumers, RFC submission gate, and unimplemented delivery contracts.
