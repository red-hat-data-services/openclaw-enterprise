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
Kubernetes Compute delivers that key as a Secret environment variable, which the
OpenShell Sandbox Driver rejects, so the Presets deploy only on an Installation
whose Sandbox Driver supplies Secret environment variables. On the profile in
this directory, create Agents with the credential-source procedure in
[the Agent guide](../agents/README.md#register-the-model-credential) instead.

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
