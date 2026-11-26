# Team Agent presets

These installation defaults provide three dedicated Codex starting points:

| Preset              | Purpose                                              | Workspace sandbox |
| ------------------- | ---------------------------------------------------- | ----------------- |
| Chief of Staff      | Priorities, briefings, decisions, and follow-through | Read-only         |
| Software Eng Team   | Investigation, implementation, and code review       | Workspace write   |
| Question and Answer | Grounded answers with source references              | Read-only         |

All retain Standard Codex's on-request approvals and network configuration.
Users supply an Agent name, model, and model credential during creation. Role
instructions live in each template's initial `AGENTS.md`. These presets do not
provision integrations, credentials, additional team agents, or workloads.

These Presets authenticate with a model API key (`harnessAuth.method: api_key`).
On the profile in this directory, which selects the OCC TokenService, entering
a new key when launching a Preset enrolls it as a static TokenService Source
and saves the Agent as a draft; the Console does not deploy it. A Console-saved
draft has no Configuration step, so take the tenant setup from
[Select the tenant](../agents/README.md#select-the-tenant) (including the
routing-label attachment), but set `OCC_NAMESPACE` to the Namespace you
selected in the Console; that snippet's `default` selection is only an
example. Then derive the identifiers the remaining steps use:

```bash
export OCC_NAMESPACE='<Namespace ID selected in the Console>'
export TENANT_NAMESPACE="$(oc get namespaces \
  -l "openclaw.dev/namespace=$OCC_NAMESPACE" -o json |
  jq -er '.items | if length == 1 then .[0].metadata.name
    else error("expected one tenant namespace") end')"
export GATEWAY_NAMESPACE="$TENANT_NAMESPACE"
export AGENT_NAME='<Agent display name used in the Console>'
export AGENT_ID="$(deploy/rhoai/bin/occ agent list -o json |
  jq -er --arg name "$AGENT_NAME" '[.[] | select(.name == $name)] |
    if length == 1 then .[0].id else error("expected one matching Agent") end')"
export CONFIGURATION_ID="$(deploy/rhoai/bin/occ agent get "$AGENT_ID" -o json |
  jq -r .configurationId)"
export AGENT_SA="agent-$(printf '%s' "$AGENT_ID" | sha256sum |
  awk '{print substr($1, 1, 12)}')"
export GATEWAY_SA="gateway-${AGENT_SA#agent-}"
```

Complete
[Allow the model endpoint](../agents/README.md#allow-the-model-endpoint-for-the-agent),
[Admit the Agent Gateway](../agents/README.md#admit-the-agent-gateway), and
[Deploy and verify](../agents/README.md#deploy-and-verify) before use; the
Control UI Route procedure needs the `CONFIGURATION_ID` derived above. The
new-key path needs `secret:create` and `credential_source:create` on the
Namespace, and the preset chooser disables new-key entry for a caller denied
`operate` on any readable Secret. The preset flow offers no Source picker, so
such a caller cannot use these Presets: follow the manual flow from
[Register the model credential](../agents/README.md#register-the-model-credential)
through
[Create the Configuration and Agent](../agents/README.md#create-the-configuration-and-agent),
which binds the enrolled Source directly. The Control UI Route procedure the
deploy steps lead into supports one Route per tenant Namespace.
Secret-backed bindings are refused before the Agent is saved
("Enroll this model credential as a TokenService Source and select it for
Harness authentication"): the Console's provisioning request, and direct API
Agent creation, both reject a Secret binding at submission. A preset file also
cannot declare a `credential_source` binding directly: preset admission
accepts only `runtime`, `api_key`, and `codex_pat` harness authentication, and
seeding such a template stops controller startup before the API is created.
Remove an invalid preset file, its `presets.files` entry, and its projected
key from the api, worker, and initialization mount patches in the same update:
a `presets.files` entry without its file, or a projected key without its file,
each stops startup.

## Install across Namespaces

Merge `installation-fragment.yaml` into the existing private Installation YAML,
appending the three `presets.files` entries to any existing files. Preserve the
current `includeDefaults` choice if bundled defaults are not wanted. Relative
paths resolve beside the mounted Installation YAML.

Create a new immutable startup Secret outside Git containing the updated
`installation.yaml`, the three JSON files, and any existing Secret data keys.
Preserve the previous Secret for rollback. Never save rendered Installation
inputs or Secret manifests in this repository.

The Helm chart projects only its selected Installation key. Its API and worker
Deployments, and the pre-upgrade initialization Job that validates the
Installation before Helm updates them, also need the three JSON keys projected
into that same volume. `api-mounts.yaml`, `worker-mounts.yaml`, and
`initialization-mounts.yaml` are strategic merge patches for that purpose. The
Job patch targets `oce-initialization`, the release name in this profile. They
assume the Installation key is `installation.yaml`; adjust it, and the Job name,
if using another. Apply the Job patch only on upgrades: a fresh install's Job
does not read the Installation, and the chart renders no volume for it. Apply
all three through the release's Kustomize/Helm post-render workflow, preserving
all other projected items if the installation has any.
Keep the new Secret name in private Helm `installation.secretName` values and
refresh `controlPlane.installationChecksum`. Future Helm upgrades must retain
these projection patches and the three JSON files in any replacement Secret.

Validate with the selected OCE release's startup validator and render/admission
checks before changing the selected cluster. Updating controller startup restarts
API and worker and can briefly interrupt control-plane access. It does not require
runtime fleet redeployment. Verify the three names in each eligible Namespace
before removing rollback inputs.

In the console, select a Namespace and open **Agents → Create Agent → Preset
template**. Ordinary Preset read and Agent creation permissions still apply.
The defaults seed existing and new Namespaces as independent copies; changing a
Preset does not change existing Agents. Changing these JSON files does not
replace already saved custom same-name copies. Use the Preset API to update
those copies deliberately.
