# Implementation plan: default console branding in the fork

- **ID:** TASK-0048
- **Delivery status:** In progress
- **Owner:** Fork maintainers
- **Authority:** Existing [console theme contract](../../docs/reference/console/theming.md)
- **Source baseline:** `d265e167a860a27f793d68f7b42567c5d6a7550e` (`redhat/main`)

## Outcome and scope

Every controller image built from this fork selects the committed RedClaw theme
by default, for both the development and runtime Dockerfile targets. Compose,
Helm, and CLI installations using those images inherit the same default without
additional mounts. Existing images need rebuilding; deployments that select
upstream images retain their image's defaults. Direct host-process execution
uses the documented environment setting.

Keep branding data in a fork-owned packaging directory. Retain the upstream
console, loader, validation, and deployment interfaces unchanged. An explicit
operator-supplied theme still replaces the image default. This packages an
existing capability; it introduces no Driver, API, or Agent lifecycle behavior.

## Contract and source touchpoints

- `deploy/branding/redclaw/` owns the manifest and three public raster assets.
  It contains no credentials or deployment state.
- The root `Dockerfile` copies that directory into both final controller stages
  and sets the existing `OCC_CONSOLE_THEME_DIR` variable. Root-owned files remain
  readable by the existing nonroot image user and read-only deployments.
- `api-startup.mjs:startApi` continues to load one validated snapshot before
  listening. Invalid or unreadable operator overrides fail startup as before.
- Workers and bootstrap/migration commands share the image, but only the API
  startup path loads the theme. Agent/runtime images are unaffected.
- The upstream Compose and Helm files need no fork-specific patches. Theme
  ConfigMaps or bind mounts can replace the bundled directory using their
  existing configuration.

## Implementation

1. Commit the four required theme files under the fork-owned packaging path.
   Keep image editing inputs and unrelated local files outside the repository.
2. Add adjacent `COPY` and `ENV` instructions before `USER node` in the
   development and runtime stages. Keep build and publication callers intact
   so existing image creation paths inherit the default.
3. Document rebuilding, direct-process use, explicit replacement, and recovery
   in the packaging README. Add the ownership link to the layout guide and
   identify the image default in the existing console flow.
4. Add a fork-owned verification script that exercises a running image's real
   HTTP routes and checks its default selection and exact served asset bytes.
   Exercise both built targets against a disposable PostgreSQL-backed API;
   check that an invalid explicit replacement still refuses startup.
5. Review the carry diff against `redhat/main`. During upstream updates,
   preserve both Dockerfile hooks and rerun image verification. New stages or
   alternate build recipes require an explicit branding check.

## Verification

| Required outcome                                  | Real check and prerequisites                                                                                               | Result or remaining proof                           |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| Both controller targets include the default       | Build both targets; inspect image configuration; run PostgreSQL-backed API without theme mounts or environment overrides   | Passed locally on ARM64                             |
| API serves the packaged bundle                    | Run the image verifier against both containers; compare HTML, CSS, logo, favicon, and touch icon with the committed bundle | Passed locally on ARM64                             |
| Explicit overrides preserve the upstream contract | Run with a neutral replacement bundle; reject an invalid replacement before listening                                      | Neutral override and invalid-startup refusal passed |
| Fork carry is small and documented                | Inspect diff; run documentation, formatting, workspace, and script checks                                                  | Passed                                              |
| Native image requirements before merge            | Exact-head native image workflow: controller and runtime on AMD64 and ARM64                                                | Pending; local ARM64 proof does not replace this    |

No model-runtime behavior changes. Packaging verification does not claim model
execution, real-cluster deployment, or image publication. Published UI evidence
shows the actual RedClaw image default.

## Delivery record

Both controller targets built successfully with the approved immutable Node base.
Their default themes passed HTTP checks against an isolated PostgreSQL-backed
development API, without theme mounts or environment overrides. The API user
cannot write the image-owned bundle. An invalid explicit replacement refused
startup with `CONSOLE_THEME_INVALID`. The production target was exercised with
development composition; this does not establish production-mode deployment.

A neutral replacement passed real browser sign-in, authenticated Namespace read,
mobile rendering, and sign-out without page errors. The same browser workflow
also passed with the actual RedClaw default, without a theme override or mount.
Screenshots and an interaction video remain outside the checkout and show that
default. No Agent execution was exercised.

Lint, workspace boundaries, full workspace formatting, scoped bundle/script/plan
formatting, documentation links and length, and console flow validation passed.
Existing installed verification tools were reused without dependency installation.

Native image verification remains required at the final PR head. The fork lacks
runner and base-image configuration for that workflow; repository settings remain
unchanged. Local ARM64 proof does not satisfy the native multi-architecture gate.
Image publication remains pending and is not authorized by verification alone.

The [packaging README](../../deploy/branding/README.md) owns current operator and
fork-maintenance instructions. Publication remains a separate authorized step.

## Manual Notes

[keep this for the user to add notes. do not change between edits]

## Changelog

- 2026-10-22: Replace neutral PR captures with the actual image default at the
  user's request; retain the separate operator-override check.
- 2026-10-22: Plan and begin the fork-owned default theme carry against the
  recorded source baseline.
