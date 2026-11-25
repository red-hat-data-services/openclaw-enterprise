# Deploy the first OpenShift Agent

Deploy one dedicated Codex Agent on the OpenShift cluster after OCC is installed
and private Agent routing is enabled. The Agent uses the OpenShell runtime
provider; ServicePrincipal credentials stay disabled because OpenShell cannot
project the Agent identity.

Run from the repository root with the private kubeconfig and the variables from
the installation.

## Select the tenant

The OCC Namespace ID and the Kubernetes tenant namespace are different values.

```bash
umask 077
export OCC_INPUT_DIRECTORY="$HOME/.local/state/openclaw-enterprise/rhoai"
export KUBECONFIG="$OCC_INPUT_DIRECTORY/kubeconfig"
export OCC_URL="https://$OCC_CONSOLE_HOST"
export OCC_SERVICE_KEY_FILE="$OCC_INPUT_DIRECTORY/initial-admin-service-key.json"

export OCC_NAMESPACE="$(deploy/rhoai/bin/occ namespace list -o json |
  jq -r '.[] | select(.name == "default") | .id')"
export TENANT_NAMESPACE="$(oc get namespaces \
  -l "openclaw.dev/namespace=$OCC_NAMESPACE" -o json |
  jq -er '.items | if length == 1 then .[0].metadata.name
    else error("expected one tenant namespace") end')"
export GATEWAY_NAMESPACE="$TENANT_NAMESPACE"

deploy/rhoai/bin/occ namespace get "$OCC_NAMESPACE"
```

Expect the Namespace to be `ready`. Attach the tenant to the private Gateway;
the Envoy NetworkPolicy admits supervisor Pods only from attached namespaces,
and the workspace node cannot pair without this label.

```bash
oc get namespace "$TENANT_NAMESPACE" -o json |
  jq -e --arg id "$OCC_NAMESPACE" '
    .metadata.labels["openclaw.dev/namespace"] == $id and
    .metadata.annotations["openclaw.dev/namespace-id"] == $id'

export ROUTING_LABEL="$(oc -n openclaw-system get gateway oce-agent-gateways -o json |
  jq -er '.spec.listeners[] | select(.name == "https") |
    .allowedRoutes.namespaces.selector.matchLabels["openclaw-enterprise.io/gateway"]')"
oc label namespace "$TENANT_NAMESPACE" \
  "openclaw-enterprise.io/gateway=$ROUTING_LABEL" --overwrite
```

## Register the model credential

Use an OpenAI API key authorized for the model in the Agent Configuration. The
selected TokenService keeps the credential and pushes a delivery copy to the
OpenShell gateway as a provider profile. This profile's gateway connection is
plaintext, so keep the cluster single-tenant. Keep the key out of shell history,
command arguments, and repository files. Source names are unique per Namespace
and share the Agent name's 200-character limit, so this guide derives them from
the Agent's display name; keep it short enough that `$AGENT_NAME-openai` fits.

```bash
# REQUIRED: display name for this Agent and its OpenClaw assistant.
export AGENT_NAME='<Agent display name>'
umask 077
read -r -s -p 'OpenAI API key: ' OPENAI_API_KEY
printf '\n'
printf '%s' "$OPENAI_API_KEY" |
  jq -Rs --arg name "$AGENT_NAME-openai" '{name: $name, type: "static",
    config: {provider_preset: "openai"}, secret: gsub("\n"; "")}' \
  > "$OCC_INPUT_DIRECTORY/credential-source.json"
unset OPENAI_API_KEY

export SOURCE_ID="$(deploy/rhoai/bin/occ credential-source create \
  --file "$OCC_INPUT_DIRECTORY/credential-source.json" -o json | jq -r .id)"
rm -f "$OCC_INPUT_DIRECTORY/credential-source.json"
test -n "$SOURCE_ID" && test "$SOURCE_ID" != null
deploy/rhoai/bin/occ credential-source get "$SOURCE_ID"
```

Expect `STATE` to be `ready`. To replace the stored key, send the new value as
`secret` to `occ credential-source update "$SOURCE_ID" --file ...`. An omitted
`config` keeps its saved value. `occ credential-source rotate` requests an OAuth
refresh from the Source's TokenService owner and does not replace a static key.

## Create the Configuration and Agent

The dedicated Codex Configuration selects `codex/gpt-6-luna` with a fail-closed
provider URL. The authenticated Codex process reaches the model through
OpenShell. Do not put the API key in the Configuration.

```bash
export CONFIGURATION_FILE=deploy/rhoai/manifests/openshift/codex-luna-configuration.json

jq --arg name "$AGENT_NAME" \
  '.values.agents.entries.main.name = $name |
   .values.agents.entries.main.identity.name = $name' \
  "$CONFIGURATION_FILE" > "$OCC_INPUT_DIRECTORY/named-configuration.json"
export CONFIGURATION_ID="$(deploy/rhoai/bin/occ configuration create \
  --file "$OCC_INPUT_DIRECTORY/named-configuration.json" -o json | jq -r .id)"

jq -n --arg name "$AGENT_NAME" --arg configuration "$CONFIGURATION_ID" \
  --arg source "$SOURCE_ID" \
  '{name: $name, configurationId: $configuration,
    executionMode: "dedicated",
    harnessAuth: {method: "credential_source", sourceId: $source},
    credentialSources: [{sourceId: $source}]}' \
  > "$OCC_INPUT_DIRECTORY/agent.json"
deploy/rhoai/bin/occ agent create --file "$OCC_INPUT_DIRECTORY/agent.json" -o json \
  > "$OCC_INPUT_DIRECTORY/agent-response.json"

export AGENT_ID="$(jq -r .id "$OCC_INPUT_DIRECTORY/agent-response.json")"
export AGENT_PRINCIPAL="$(jq -r .servicePrincipalId \
  "$OCC_INPUT_DIRECTORY/agent-response.json")"
export AGENT_SA="agent-$(printf '%s' "$AGENT_ID" | sha256sum |
  awk '{print substr($1, 1, 12)}')"
export GATEWAY_SA="gateway-${AGENT_SA#agent-}"
printf 'Agent ID: %s\nAgent ServiceAccount: %s\nGateway ServiceAccount: %s\n' \
  "$AGENT_ID" "$AGENT_SA" "$GATEWAY_SA"
```

Grant the Agent service principal `operate` on the exact credential source, not
on the whole Namespace.

```bash
jq -n '{name: "Use an OpenShell credential source",
  permissions: [{action: "operate", resourceKind: "credential_source"}]}' \
  > "$OCC_INPUT_DIRECTORY/credential-source-role.json"
export ROLE_ID="$(deploy/rhoai/bin/occ iam role create \
  --file "$OCC_INPUT_DIRECTORY/credential-source-role.json" -o json | jq -r .id)"

jq -n --arg principal "$AGENT_PRINCIPAL" --arg role "$ROLE_ID" \
  --arg source "$SOURCE_ID" \
  '{subjectKind: "identity", subjectId: $principal, roleId: $role,
    resourceKind: "credential_source", resourceId: $source}' \
  > "$OCC_INPUT_DIRECTORY/credential-source-binding.json"
deploy/rhoai/bin/occ iam access-binding create \
  --file "$OCC_INPUT_DIRECTORY/credential-source-binding.json"
```

Allow the Agent Gateway and sandbox identities to pull the runtime image. The
published runtime comes from public `quay.io`, so this grant is unused today;
apply it anyway so a future in-cluster build keeps working.

```bash
oc -n openclaw-system create rolebinding "oce-runtime-pull-${AGENT_SA#agent-}" \
  --clusterrole=system:image-puller \
  --serviceaccount="$GATEWAY_NAMESPACE:$GATEWAY_SA" \
  --serviceaccount="$TENANT_NAMESPACE:openshell-sandbox" \
  --dry-run=client -o yaml | oc apply -f -
```

## Allow the model endpoint for the Agent

The Configuration keeps the Codex provider at the unreachable
`http://127.0.0.1:9`, and the Codex Harness calls `api.openai.com` over a route
the Agent admits. Deployment needs exactly one grant for the bundled Codex
executable to `api.openai.com` port 443.

The Installation fragment already delegates that grant through its top-level
`sandboxNetworkPolicyCeiling` (`codex-model-egress` for the `linux/amd64`
runtime; add the arm64 `aarch64-unknown-linux-musl` Codex path there on arm64
nodes). Save the matching addition on the Agent before you deploy it. Policy
editing needs Installation and Agent `administer`; saving an addition needs
Agent `manage_policy` and SandboxPolicy `update`.

The binary path embeds the runtime's Codex version. When a runtime image bumps
that version, update the ceiling in the Installation fragment and re-save this
addition with the new path on every deployed Agent; the offline test suite
fails on a fragment that no longer matches the runtime Dockerfile's pinned
version. The update replaces the whole additions list, so include any saved
additions in the same request when an Agent already has them.

```bash
export CODEX_BINARY='/app/node_modules/openclaw/node_modules/.pnpm/@openai+codex@0.164.0-linux-x64/node_modules/@openai/codex/vendor/x86_64-unknown-linux-musl/bin/codex'
deploy/rhoai/bin/occ agent policy editing "$AGENT_ID" --enabled=true
export POLICY_VERSION="$(deploy/rhoai/bin/occ agent policy get "$AGENT_ID" -o json |
  jq -er .policy.version)"
jq -n --argjson version "$POLICY_VERSION" --arg binary "$CODEX_BINARY" '{
  expectedVersion: $version,
  additions: [{id: "model-egress", binary: $binary, host: "api.openai.com",
    ports: [{from: 443, to: 443}]}]}' \
  > "$OCC_INPUT_DIRECTORY/model-egress.json"
deploy/rhoai/bin/occ agent policy update "$AGENT_ID" \
  --file "$OCC_INPUT_DIRECTORY/model-egress.json"
```

## Admit the Agent Gateway

The Agent Gateway runs as UID/GID 1000 in the tenant namespace. Grant its exact
ServiceAccount use of the existing `oce-fixed-uid-1000` SCC before requesting a
revision. Without it, OpenShift rejects the Gateway Pod and its
`WaitForFirstConsumer` PVC stays pending.

```bash
oc -n "$GATEWAY_NAMESPACE" create role oce-fixed-uid-1000-agent-gateway \
  --verb=use --resource=securitycontextconstraints.security.openshift.io \
  --resource-name=oce-fixed-uid-1000 \
  --dry-run=client -o yaml | oc apply -f -
# Name the RoleBinding for this Agent's Gateway ServiceAccount: applying a
# same-named binding for another Agent would replace its subjects and strip
# this Gateway's SCC grant.
oc -n "$GATEWAY_NAMESPACE" create rolebinding "oce-fixed-uid-1000-$GATEWAY_SA" \
  --role=oce-fixed-uid-1000-agent-gateway \
  --serviceaccount="$GATEWAY_NAMESPACE:$GATEWAY_SA" \
  --dry-run=client -o yaml | oc apply -f -
oc auth can-i use scc/oce-fixed-uid-1000 -n "$GATEWAY_NAMESPACE" \
  --as="system:serviceaccount:$GATEWAY_NAMESPACE:$GATEWAY_SA"
```

Expect `yes`. The provider Harness keeps the workspace chart's
`openshell-sandbox` ServiceAccount under `restricted-v2`; do not replace it with
the Agent ServiceAccount.

## Deploy and verify

```bash
deploy/rhoai/bin/occ agent deploy "$AGENT_ID"
```

Record the returned revision ID, then check its status and pairing evidence:

```bash
export DEPLOYMENT_ID='<revision ID returned by deploy>'
deploy/rhoai/bin/occ agent deployment-status "$AGENT_ID" "$DEPLOYMENT_ID"
oc -n "$TENANT_NAMESPACE" get secrets -o json |
  jq -e --arg prefix "workspace-node-${AGENT_SA#agent-}-" \
    '[.items[] | select(.metadata.name | startswith($prefix)) |
      select(.data.deviceId != null)] | length == 1'
```

Wait for `succeeded`; the `jq` check prints `true`. These results prove
deployment and pairing, not a model turn. Continue with the
[Control UI Route](../openshift/README.md#control-ui-route) and complete a real
model turn.

## Add initial Agent files

Open the Agent's **Workspace files** tab in OCC. Bootstrap may already provide
`AGENTS.md`, `SOUL.md`, `IDENTITY.md`, and `USER.md`. Create only missing files
and edit existing instructions deliberately. Verify the Agent can read them from
`/sandbox/enterprise` during a model turn.
