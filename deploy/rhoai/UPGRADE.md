# Upgrade the RHOAI deployment

Use `occ installation upgrade` from the reviewed release checkout; do not
reapply bootstrap. The CLI ships with openclaw/openclaw-enterprise#6504
(commit `81428d441`); `migration apply`, for a separate database migration
pause, needs openclaw/openclaw-enterprise#6514 (commit `aacfa5baf`). Releases
and published images predating the CLI use the
[`scripts/upgrade` fallback](#releases-without-the-upgrade-cli) below.

From this repository (the tooling checkout with `deploy/rhoai`), point
`OCE_CHECKOUT` at a separate clean release checkout or `git worktree` of this
repository, resolve the published images, then check out their source revision
in that release checkout so the command, chart, and images share one commit.
Keep this tooling checkout at its current revision: a published revision that
predates this branch would remove `deploy/rhoai/scripts/prepare-upgrade-profile`
from its working tree.

```bash
export OCE_CHECKOUT="<clean release checkout or git worktree>"
deploy/rhoai/scripts/resolve-images &&
  OCC_REV="$(jq -er .oceCommit "$OCC_INPUT_DIRECTORY/published-images.json")" &&
  export OCC_REV &&
  git -C "$OCE_CHECKOUT" checkout "$OCC_REV" &&
  deploy/rhoai/scripts/build-occ &&
  deploy/rhoai/scripts/prepare-upgrade-profile
```

The chain stops at the first failure: `resolve-images` writes its files only
after every check passes, so continuing after a failure would read the previous
release's receipt.

Nothing here needs carrying into the release checkout.

`prepare-upgrade-profile` renders the owner-only
`$OCC_INPUT_DIRECTORY/upgrade-profile.json` from the saved kubeconfig,
`values.yaml`, `installation.yaml`, `images.env`, and `published-images.json`;
it writes nothing else and needs no cluster access. Its `OCC_BIN` defaults to
`deploy/rhoai/bin/occ`.

Export the [step 6](README.md#6-build-the-cli-and-sign-in) client settings
from the repository root, and `OCC_CA_BUNDLE` when needed. `apply` and
`preflight` require them; `migration check` does not.

Bind the Installation Secret once per installation before the first `apply` or
`preflight`, or the upgrade refuses a Secret without the
`openclaw.dev/installation-id` annotation. Never overwrite a different ID:
reconcile the cluster and bootstrap records. See
[Bind the Installation once](../../docs/guides/deploy/production-upgrade.md#bind-the-installation-once).

```bash
export OCC_CLI="$PWD/deploy/rhoai/bin/occ"
export OCC_PROFILE="$OCC_INPUT_DIRECTORY/upgrade-profile.json"
export OCC_URL="https://$OCC_CONSOLE_HOST"
export OCC_SERVICE_KEY_FILE="$OCC_INPUT_DIRECTORY/initial-admin-service-key.json"
export KUBECONFIG="$OCC_INPUT_DIRECTORY/kubeconfig"
(
  set -o pipefail
  OCC_INSTALLATION_ID=$("$OCC_CLI" installation get --output json | jq -ser '
    select(length == 1) | .[0].id | select(type == "string") |
    select(test("\\Ains_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\\z"))
  ') &&
  OCC_INSTALLATION_SECRET=$(yq -o=json '.' "$OCC_INPUT_DIRECTORY/values.yaml" | jq -ser '
    select(length == 1) | .[0] | objects | .installation.secretName |
    if . == null then "occ-installation-startup" else . end |
    select(type == "string" and length <= 253) |
    select(test("\\A[a-z0-9]([-a-z0-9]*[a-z0-9])?(\\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*\\z"))
  ') || { printf '%s\n' 'Invalid inputs; annotation unchanged.' >&2; exit 1; }
  oc --namespace openclaw-system annotate secret "$OCC_INSTALLATION_SECRET" \
    openclaw.dev/installation-id="$OCC_INSTALLATION_ID"
)
```

Then run the upgrade commands from `$OCE_CHECKOUT`, which the CLI requires as
its working directory:

```bash
cd "$OCE_CHECKOUT"
"$OCC_CLI" installation upgrade plan --profile "$OCC_PROFILE"
"$OCC_CLI" installation upgrade migration check --profile "$OCC_PROFILE"
"$OCC_CLI" installation upgrade apply --profile "$OCC_PROFILE"
```

Each fresh run prints its evidence directory. Keep it: `status`,
`migration apply`, `resume`, and `cleanup-preflight` read the original RUN. If
`apply` pauses for a separate database migration and exits 3, set `RUN` to the
printed evidence directory. A checkout at or after `aacfa5baf` applies and
verifies the migration, then resumes:

```bash
export RUN="<evidence directory printed by apply>"
"$OCC_CLI" installation upgrade migration apply "$RUN" --profile "$OCC_PROFILE" &&
  "$OCC_CLI" installation upgrade resume "$RUN" --profile "$OCC_PROFILE" -- --migration-history-checked
```

Run the two commands separately only after `migration apply` reports the
migration verified and its Jobs and Pods gone; resuming before that can
switch the Installation Secret while the schema is incomplete. Repeat every
expert option the original `apply` received after `--` on both recovery
commands; an immutable-Secret run repeats
`-- --candidate-values "$OCC_INPUT_DIRECTORY/candidate-values.yaml"`, for
example. A mismatch refuses the resume while the writers are stopped.

`resume` compares the full recorded parameter set, so keep using the exact
profile at its original path in `$OCC_INPUT_DIRECTORY` for `apply`,
`migration apply`, and `resume`. Relative fields in the profile resolve from
its own directory, so a copy inside a run's evidence directory cannot find the
kubeconfig. Do not re-render `upgrade-profile.json` over an interrupted run: a
runtime upgrade writes the candidate Installation before Helm submission, so a
re-render omits `images.runtime` and cannot resume the run that stopped the
API and worker. Re-render only after the interrupted run is finished or
abandoned.

On an older checkout at or after `81428d441` but before `aacfa5baf`, the CLI has
only `migration check`; follow the selected-image migration procedure named in
the exit-3 message, then resume.

Keep the saved `images.env`, Installation Secret, database Secrets, and signing
key across releases, and pass the dashboard and mTLS values and post-renderer
on gateway upgrades. The flow requires `repositoryCredentials.enabled: false`
(the prepared default); enabling it needs a broker digest this profile cannot
supply. Before the first `apply`, also check the values' external references:
the release chart mounts `repositoryCredentials.registryConfigMapName`
whenever it is set, even with credentials disabled, so the named ConfigMap
must exist. A dangling reference fails the startup preflight with
`startup preflight child Pod disappeared`; the FailedMount cause appears only
in cluster events. See
[Run production image upgrades with the CLI](../../docs/guides/deploy/production-upgrade-command.md).

## Immutable Installation Secrets

When the Installation Secret is immutable and the upgrade changes Installation
data (any runtime image change does), `apply` refuses during preparation and
names the successor flow. Follow
[Upgrade with an immutable Installation Secret](../../docs/guides/deploy/immutable-installation-upgrade.md):
stage a versioned successor Secret carrying the candidate Installation, set
`installation.secretName` to it in a candidate values copy, and pass that file
with `-- --candidate-values`. Keep the baseline `values.yaml` and
`installation.yaml` unchanged; the helper switches the Helm reference and
retains the original Secret for recovery.

## Releases that retire Credential Gateway

Release checkouts at or after `105ffee99`
(openclaw/openclaw-enterprise#7224) retire `drivers.credential_gateway`. The
controller refuses an Installation that selects it, and the startup preflight
reports `drivers contains unsupported option credential_gateway` before any
writer stops. The RHOAI Installation fragment selects Credential Gateway for
OpenShell tool sources, so migrating the Installation to TokenService and
Credential Proxy is required before upgrading past that revision. Follow
[Upgrade an installation to TokenService and Credential
Proxy](../../docs/guides/deploy/credential-proxy-upgrade.md); apply the
Installation change through the [separate chart
operation](../../docs/guides/deploy/production-installation-changes.md) from
the installed controller's checkout, because the image upgrade's candidate
validation does not admit driver retirements. The installed controller must
be at or after `03db2ee93`, which accepts a TokenService and Credential Proxy
Installation; an older controller refuses the new Installation after the
Secret is already patched, so upgrade the controller first and plan the
Installation change as its own step.

## Helm 4 on release checkouts predating 45178960c

Release checkouts predating `45178960c`
(openclaw/openclaw-enterprise#6593) can refuse under Helm 4 with `the Helm
release changed before submission` after they stop the API and worker, because
Helm 4's `helm status` includes live resources. The CLI and the fallback below
both run `scripts/upgrade-production-images`, so a checkout at or after
`81428d441` but before `45178960c` carries the bug even though it has the
upgrade CLI. Put a Helm 3 binary first on `PATH` for every release checkout
predating `45178960c`. A refused run can leave the Installation Secret
switched with the old controller, so the console stays down until restoration.
To recover, confirm with `helm history` that the release revision is unchanged
(no other upgrade ran), restore the Installation Secret from the run's evidence
directory, then scale the API and worker back up.

## Releases without the upgrade CLI

`deploy/rhoai/scripts/upgrade` delegates to
`$OCE_CHECKOUT/scripts/upgrade-production-images`; follow
[upgrading OCE on Kubernetes](../../docs/guides/deploy/production-upgrade.md).

## Removing the retired gateway.workspaceMode

For a saved Installation that still carries
`drivers.sandbox.configuration.gateway.workspaceMode` (retired by
openclaw/openclaw-enterprise#6641), use the `scripts/upgrade` wrapper with a
release checkout of this repository at `38b077125` or later but before
`105ffee99`: the fork's native helper accepts a candidate Installation that
omits only that key, while the CLI path rejects the same candidate as a
protected change. Do this before the Credential Gateway migration, because
every Installation that still carries the key also selects Credential
Gateway, which checkouts at or after `105ffee99` refuse: remove
`workspaceMode` first, then follow
[Releases that retire Credential Gateway](#releases-that-retire-credential-gateway),
then upgrade past `105ffee99`.
