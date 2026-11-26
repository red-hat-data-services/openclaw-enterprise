# Upgrade the RHOAI deployment

Use `occ installation upgrade` from the reviewed release checkout; do not
reapply bootstrap. The CLI ships in current published releases
(openclaw/openclaw-enterprise#6504); `migration apply`, for a separate
database migration pause, needs openclaw/openclaw-enterprise#6514.

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

A runtime image bump changes the Codex binary path, and the image upgrade
cannot change the Installation: the candidate validation refuses protected
settings, the model-egress ceiling among them. Raise the ceiling first, then
plan, then stop the fleet only for `apply`:

- **Raise the model-egress ceiling through the separate Installation
  change**, not the image upgrade: append the new Codex binary entry, with
  a distinct id, to `.sandboxNetworkPolicyCeiling` in
  `$OCC_INPUT_DIRECTORY/installation.yaml`. Do not re-apply the fragment
  with the README's deep merge: it resets the merged workspace resources
  and harness address. Then follow
  [change the Installation](../../docs/guides/deploy/production-installation-changes.md)
  (validate, recompute the checksum, patch the Installation Secret, and
  restart the API and worker). Keep the saved `installation.yaml` this
  profile renders from in sync with the live Installation, or `apply`
  refuses the candidate as a protected change. If the Installation Secret
  is immutable, the in-place patch cannot apply: stage a successor Secret
  carrying the ceiling-raised Installation, copy the predecessor's
  `openclaw.dev/installation-id` annotation onto it as the
  [immutable upgrade flow](../../docs/guides/deploy/immutable-installation-upgrade.md)
  does, and switch `installation.secretName` to it, keeping that change
  outside the image-upgrade window.

Then run the planning checks from `$OCE_CHECKOUT`, which the CLI requires
as its working directory. `plan` is offline. `migration check` creates a
temporary Job, applies no SQL, and confirms the Job's cleanup before
reporting; `cleanup-preflight` reconciles an interrupted run. `preflight`
compares the saved baseline against the live Installation Secret, so a
stale `installation.yaml` surfaces here, without an outage, instead of at
`apply` after the fleet has stopped. None of them needs stopped Agents,
so run them while the fleet still serves:

```bash
cd "$OCE_CHECKOUT"
"$OCC_CLI" installation upgrade plan --profile "$OCC_PROFILE"
"$OCC_CLI" installation upgrade migration check --profile "$OCC_PROFILE"
"$OCC_CLI" installation upgrade preflight --profile "$OCC_PROFILE"
```

- **Stop every running Agent.** Agents deployed through this profile's
  managed static Source flow hold an active staged revision, and the
  controller refuses to deploy over one ("Complete Stop before deploying
  again"), so the upgrade's automatic redeploy cannot complete. Record
  each Namespace, Agent, and active revision to an owner-only file: the
  stop clears the active revision, and the redeploy reuses the recorded
  revision's Harness overrides. The stop is asynchronous: it clears the
  active revision only after the Agent's runtime shuts down, so confirm
  every targeted Agent reports `stopped` with no active revision and no
  deployment in progress before `apply`. The inventory cannot see queued
  stop work for an Agent that never completed a first deployment (it
  holds runtime resources without an active revision); for such an Agent,
  confirm its stop has finished before `apply`.

  ```bash
  "$OCC_CLI" installation deployment-inventory --output json |
    jq -r '.namespaces[] | .id as $ns | .agents[] |
      select(.desiredRuntimeState == "running") |
      "\($ns) \(.id) \(.activeRevisionId)"' \
    > "$OCC_INPUT_DIRECTORY/stopped-agents.txt"
  while read -r stopped_namespace stopped_agent stopped_revision; do
    "$OCC_CLI" agent stop --namespace "$stopped_namespace" "$stopped_agent"
  done < "$OCC_INPUT_DIRECTORY/stopped-agents.txt"
  "$OCC_CLI" installation deployment-inventory --output json |
    jq -e '[.namespaces[].agents[] |
      select((.desiredRuntimeState == "running") or
             (.desiredRuntimeState == "stopped" and .activeRevisionId != null) or
             (.deploymentInProgress == true))] | length == 0'
  ```

Then run `apply`:

```bash
"$OCC_CLI" installation upgrade apply --profile "$OCC_PROFILE"
```

Each fresh run prints its evidence directory. Keep it: `status`,
`migration apply`, `resume`, and `cleanup-preflight` read the original RUN. If
`apply` pauses for a separate database migration and exits 3, set `RUN` to the
printed evidence directory. The current CLI applies and verifies the
migration, then resumes:

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

After the upgrade, re-save and redeploy each stopped Agent: re-save its
model-egress policy addition with the new binary path (the save is refused
while the addition sits outside the live ceiling), then deploy it again and
verify a model turn. Follow
[Allow the model endpoint for the Agent](manifests/agents/README.md#allow-the-model-endpoint-for-the-agent).
The recorded revision drives the retention; the CLI skips it silently when
the flag value is empty, so deploy every recorded Agent from the file:

```bash
while read -r stopped_namespace stopped_agent stopped_revision; do
  "$OCC_CLI" agent deploy --namespace "$stopped_namespace" "$stopped_agent" \
    --retain-harness-overrides "$stopped_revision"
done < "$OCC_INPUT_DIRECTORY/stopped-agents.txt"
```

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
