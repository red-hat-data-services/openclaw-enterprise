# RHOAI 3.6 deployment guide

Installs OpenClaw Enterprise (OCE) and its OpenClaw Control Plane (OCC) on an
existing Red Hat OpenShift AI (RHOAI) 3.6 cluster. It fixes known failures in
this checkout and is a development profile, not a production qualification.

This profile was verified on OpenShift 4.22.5 (ROSA, two `m5.2xlarge` workers,
no GPU), RHOAI 3.6.0, cert-manager `v1.20.1`, OpenShift-owned Gateway API
`v1.4.1`, and default StorageClass `gp3-csi`. Cluster apps domain:
`apps.rosa.acp-lobster-pot.mwon.p3.openshiftapps.com`.

RHOAI owns the Gateway API CRDs and GatewayClass. Keep them; install only the
Envoy CRDs later. Run every command from the repository root; see
[the deployment guide](README.md) and
[the Kustomize stages](manifests/openshift/README.md).

## Version pins

Use the repository's `mise.toml` pins. Do not substitute newer tools.

- Helm **3.19.2**. Helm 4 fails the OCC upgrade with
  `the Helm release changed before submission`.
- Node.js **24.22.1**. `validate-installation` imports controller TypeScript
  and fails on older releases.
- pnpm **11.15.1** from `package.json`, installed without Corepack.
- Prebuilt `linux/amd64` images only. Do not compile the runtime or Node.js
  from source.

From the M1/Apple Silicon guide, keep the pins and the prebuilt-image rule; its
Podman and k3d sections do not apply.

## Phase 0: workstation and private inputs

Install mise, activate it, and let `mise.toml` provide every pin; skip the first
line if `mise --version` works.

```bash
curl https://mise.run | sh
echo 'eval "$(mise activate bash)"' >> ~/.bashrc

cd /path/to/openclaw-enterprise
export OCE_CHECKOUT="$PWD"
mise trust mise.toml
eval "$(mise activate bash)"
mise install
mise current
```

`mise install` fetches the pins, including pnpm from `package.json`, so Corepack
is not involved. New shells need re-activation; scripts can use `mise exec --`.

```bash
node --version
helm version --short
pnpm --version
pnpm install --frozen-lockfile
```

`pnpm install --frozen-lockfile` provides the dependencies that
`validate-installation` imports.

Keep all private state outside the checkout:

```bash
umask 077
export OCC_INPUT_DIRECTORY="$HOME/.local/state/openclaw-enterprise/rhoai"
install -d -m 700 "$OCC_INPUT_DIRECTORY"
oc config view --raw --minify --flatten > "$OCC_INPUT_DIRECTORY/kubeconfig"
export KUBECONFIG="$OCC_INPUT_DIRECTORY/kubeconfig"
oc whoami --show-server
```

## Phase 1: cluster preflight and node labels

```bash
oc get clusterversion
oc get nodes -o wide
oc get crd gateways.gateway.networking.k8s.io \
  -o jsonpath='{.metadata.annotations.gateway\.networking\.k8s\.io/bundle-version}{"\n"}'
oc get gatewayclass
oc get gateway -A
```

Expect Gateway API `v1.4.1`, an Accepted `data-science-gateway-class`, and a
Programmed `data-science-gateway`; do not delete or replace them.

Label the two workers; control and Agent workloads need different nodes.

```bash
export CONTROL_NODE='ip-10-0-1-136.us-east-2.compute.internal'
export AGENT_NODE='ip-10-0-1-213.us-east-2.compute.internal'
test "$CONTROL_NODE" != "$AGENT_NODE"
oc label node "$CONTROL_NODE" oce-role=control --overwrite
oc label node "$AGENT_NODE" oce-role=agent --overwrite
```

Prepare the Console Route and the admission stage:

```bash
export CLUSTER_APPS_DOMAIN="$(oc get ingresses.config.openshift.io cluster \
  -o jsonpath='{.spec.domain}')"
export OCC_CONSOLE_HOST="occ.$CLUSTER_APPS_DOMAIN"
install -m 600 deploy/rhoai/manifests/openshift/base/control-plane/console-route.yaml \
  "$OCC_INPUT_DIRECTORY/console-route.yaml"
yq -i '.spec.host = strenv(OCC_CONSOLE_HOST) |
  .spec.tls.termination = "edge" |
  .spec.tls.insecureEdgeTerminationPolicy = "Redirect"' \
  "$OCC_INPUT_DIRECTORY/console-route.yaml"

oc apply -k deploy/rhoai/manifests/openshift/overlays/test/admission
oc get scc oce-fixed-uid-1000
```

This creates `openclaw-system`, the `oce-fixed-uid-1000` SCC, and the OCC
ServiceAccount grants; the Agent Gateway needs its own in Phase 10.

## Phase 2: install OpenShell

```bash
install -d -m 700 "$OCC_INPUT_DIRECTORY/openshell-charts"

curl -fL https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.5.2/sandbox.yaml \
  -o "$OCC_INPUT_DIRECTORY/agent-sandbox-v0.5.2.yaml"
printf '%s  %s\n' \
  230ee446d6035f631577e1c6b857f6973a8f09a0a853675d3cc34ebfe47abd6b \
  "$OCC_INPUT_DIRECTORY/agent-sandbox-v0.5.2.yaml" | sha256sum -c
oc apply -f "$OCC_INPUT_DIRECTORY/agent-sandbox-v0.5.2.yaml"
oc -n agent-sandbox-system rollout status \
  deployment/agent-sandbox-controller --timeout=5m

helm pull oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.1.6 --destination "$OCC_INPUT_DIRECTORY/openshell-charts"
helm pull oci://ghcr.io/nvidia/openshell/openshell-workspace \
  --version 0.1.6 --destination "$OCC_INPUT_DIRECTORY/openshell-charts"
printf '%s  %s\n' \
  0533ecf84f777954aaf6113a0813fbc9781ed2a5e6574fb807cebe9a818a1bd4 \
  "$OCC_INPUT_DIRECTORY/openshell-charts/helm-chart-0.1.6.tgz" \
  10452d43dcf24ff795239dc5daa0fffd509a16ff57739019f0ec084bb7ca37c5 \
  "$OCC_INPUT_DIRECTORY/openshell-charts/openshell-workspace-0.1.6.tgz" \
  | sha256sum -c

oc create namespace openshell-system --dry-run=client -o yaml | oc apply -f -
oc apply -f deploy/rhoai/manifests/openshift/base/openshell/openshell-gateway-network-policy.yaml
oc apply -f deploy/rhoai/manifests/openshift/base/openshell/control-plane-openshell-egress.yaml
helm upgrade --install openshell-gateway \
  "$OCC_INPUT_DIRECTORY/openshell-charts/helm-chart-0.1.6.tgz" \
  --namespace openshell-system \
  -f deploy/rhoai/manifests/openshift/openshell-gateway-values.yaml \
  --wait --timeout 7m
```

The egress policy is mandatory: default-deny otherwise blocks Namespace
provisioning. Verify:

```bash
oc -n openshell-system rollout status statefulset/openshell-gateway --timeout=3m
oc -n openshell-system get pod openshell-gateway-0 \
  -o jsonpath='{.metadata.annotations.openshift\.io/scc}{"\n"}'
oc -n openshell-system get pvc
```

Expect SCC `restricted-v2` and `Bound`.

Keep `workspaceMode: operator` on the OpenShell server; delete
`drivers.sandbox.configuration.gateway.workspaceMode` from the Installation.

## Phase 3: resolve the OCE images

```bash
deploy/rhoai/scripts/resolve-images
source "$OCC_INPUT_DIRECTORY/images.env"
```

It pins immutable `linux/amd64` digests and checks commits. If the checkout
moved past the image commit, confirm the chart and CLI did not change:

```bash
export OCC_REV="$(jq -er .oceCommit "$OCC_INPUT_DIRECTORY/published-images.json")"
git -C "$OCE_CHECKOUT" diff --stat "$OCC_REV"..HEAD -- \
  deploy/helm apps/controller/src cmd/occ
```

A non-empty diff means deploy from a clean checkout at `OCC_REV`.

## Phase 4: PostgreSQL

```bash
oc apply -f deploy/rhoai/manifests/openshift/base/operators/postgres-operator.yaml
oc -n openclaw-postgres wait \
  --for=jsonpath='{.status.installedCSV}'=postgresoperator.v5.8.9 \
  subscription/crunchy-postgres-operator --timeout=5m
oc -n openclaw-postgres wait \
  --for=jsonpath='{.status.phase}'=Succeeded \
  csv/postgresoperator.v5.8.9 --timeout=5m
oc wait --for=condition=Established \
  crd/postgresclusters.postgres-operator.crunchydata.com --timeout=5m
oc apply -f deploy/rhoai/manifests/openshift/base/database/postgres-cluster.yaml
until oc -n openclaw-postgres get pod \
  -l postgres-operator.crunchydata.com/cluster=oce-postgres,postgres-operator.crunchydata.com/role=master \
  -o name | grep -q .; do sleep 3; done
oc -n openclaw-postgres wait --for=condition=Ready pod \
  -l postgres-operator.crunchydata.com/cluster=oce-postgres,postgres-operator.crunchydata.com/role=master \
  --timeout=8m
```

Create roles, database, CA, and URLs:

```bash
openssl rand -hex 24 > "$OCC_INPUT_DIRECTORY/occ-migrator-password"
openssl rand -hex 24 > "$OCC_INPUT_DIRECTORY/occ-application-password"
openssl rand -hex 32 > "$OCC_INPUT_DIRECTORY/occ-auth-secret"
export POSTGRES_POD="$(oc -n openclaw-postgres get pods \
  -l postgres-operator.crunchydata.com/cluster=oce-postgres,postgres-operator.crunchydata.com/role=master \
  -o jsonpath='{.items[0].metadata.name}')"
(
  printf "\\set migrator_password '%s'\n" "$(cat "$OCC_INPUT_DIRECTORY/occ-migrator-password")"
  printf "\\set application_password '%s'\n" "$(cat "$OCC_INPUT_DIRECTORY/occ-application-password")"
  cat deploy/rhoai/manifests/openshift/postgres-bootstrap.sql
) | oc -n openclaw-postgres exec -i "$POSTGRES_POD" -c database -- \
  psql -U postgres -d postgres -X -v ON_ERROR_STOP=1 -f -
oc -n openclaw-postgres get secret oce-postgres-cluster-cert \
  -o jsonpath='{.data.ca\.crt}' | base64 --decode > "$OCC_INPUT_DIRECTORY/occ-database-ca.pem"
python3 - "$OCC_INPUT_DIRECTORY" <<'PY'
from pathlib import Path
from urllib.parse import quote
import sys

root = Path(sys.argv[1])
for role, password_file, output_file in (
    ('occ_migrator', 'occ-migrator-password', 'occ-migration-url'),
    ('occ_app', 'occ-application-password', 'occ-application-url'),
):
    password = quote((root / password_file).read_text().strip(), safe='')
    authority = role + ':' + password + '@oce-postgres-primary.openclaw-postgres.svc:5432'
    url = 'postgresql://' + authority + '/openclaw_enterprise?sslmode=verify-full&sslrootcert=/etc/openclaw/database-ca/ca.pem'
    (root / output_file).write_text(url)
PY
```

## Phase 5: private inputs, schema fixes, writer Role

```bash
source "$OCC_INPUT_DIRECTORY/images.env"
export OCC_ADMIN_EMAIL='occ-admin@example.test'
export CLUSTER_APPS_DOMAIN="$(oc get ingresses.config.openshift.io cluster \
  -o jsonpath='{.spec.domain}')"
export OCC_CONSOLE_HOST="occ.$CLUSTER_APPS_DOMAIN"
deploy/rhoai/scripts/prepare-inputs
```

`prepare-inputs` renders both files and snapshots router, API, and PostgreSQL
addresses; ignore its `bootstrap-pvc.yaml`.

Apply the corrections. They repair input directories seeded from older
examples; a fresh bootstrap from this checkout needs none of them:

```bash
# Helm rejects sharedCookieDomain. The launcher stays disabled, but the
# domain must still be the cluster apps domain.
yq -i '.agentNativeAdmin.enabled = false |
  .agentNativeAdmin.domain = strenv(CLUSTER_APPS_DOMAIN) |
  del(.agentNativeAdmin.sharedCookieDomain)' \
  "$OCC_INPUT_DIRECTORY/values.yaml"

# The current sandbox schema rejects workspaceMode and runtimeClassName.
yq -i 'del(.drivers.sandbox.configuration.gateway.workspaceMode) |
  (.. | select(has("runtimeClassName")) |
   select(path[0] == "drivers" and path[1] == "sandbox")) |= del(.runtimeClassName)' \
  "$OCC_INPUT_DIRECTORY/installation.yaml"
```

`sharedCookieDomain` must be absent, not empty.

```bash
yq -e '.agentNativeAdmin | has("sharedCookieDomain") | not' "$OCC_INPUT_DIRECTORY/values.yaml"
yq -e '.agentNativeAdmin.domain == strenv(CLUSTER_APPS_DOMAIN)' "$OCC_INPUT_DIRECTORY/values.yaml"
yq -e '.drivers.sandbox.configuration.gateway | has("workspaceMode") | not' "$OCC_INPUT_DIRECTORY/installation.yaml"
yq -e '[.. | select(has("workspaceMode") or has("runtimeClassName"))] | length == 0' \
  "$OCC_INPUT_DIRECTORY/installation.yaml"
```

`(.path // [])[] |=` creates `agentResourceOverrides: null`, which fails
validation.

Apply egress, the RuntimeClass, and the writer ClusterRole:

```bash
oc apply -f "$OCC_INPUT_DIRECTORY/control-plane-postgres-egress.yaml"
oc apply -f "$OCC_INPUT_DIRECTORY/control-plane-kube-api-egress.yaml"
oc apply -f deploy/rhoai/manifests/openshift/base/openshell/openshell-runtimeclass.yaml
oc apply -f deploy/rhoai/manifests/openshift/base/openshell/openshell-workspace-writer-clusterrole.yaml
```

Apply the writer ClusterRole unchanged. The worker applies the workspace Role
through a named server-side PATCH, and Kubernetes carries the object name into
the `escalate` check, so the rule's `resourceNames` scope matches.

Render the workspace resources into the Installation:

```bash
export OPEN_SHELL_CHART="$OCC_INPUT_DIRECTORY/openshell-charts/openshell-workspace-0.1.6.tgz"
export OPEN_SHELL_FRAGMENT=deploy/rhoai/manifests/openshift/openshell-installation-fragment.yaml
export OPEN_SHELL_WORKSPACE_RESOURCES="$OCC_INPUT_DIRECTORY/openshell-workspace-resources.json"

helm template openshell-workspace "$OPEN_SHELL_CHART" \
  --namespace openclaw-workspace-template \
  -f deploy/rhoai/manifests/openshift/openshell-workspace-values.yaml \
  > "$OCC_INPUT_DIRECTORY/openshell-workspace-rendered.yaml"
yq eval-all -o=json -I=0 '[.] | del(.[].metadata.namespace)' \
  "$OCC_INPUT_DIRECTORY/openshell-workspace-rendered.yaml" \
  > "$OPEN_SHELL_WORKSPACE_RESOURCES"
yq -i '. *= load(strenv(OPEN_SHELL_FRAGMENT))' "$OCC_INPUT_DIRECTORY/installation.yaml"

export OPENSHELL_SERVICE_IP="$(oc -n openshell-system get service openshell-gateway \
  -o jsonpath='{.spec.clusterIP}')"
yq -i '.drivers.compute.configuration.network.providerHarness.address = strenv(OPENSHELL_SERVICE_IP) |
  .drivers.sandbox.configuration.gateway.operatorWorkspaceResources = load(strenv(OPEN_SHELL_WORKSPACE_RESOURCES))' \
  "$OCC_INPUT_DIRECTORY/installation.yaml"
yq -e '.drivers.sandbox.configuration.gateway.operatorWorkspaceResources | length == 4' \
  "$OCC_INPUT_DIRECTORY/installation.yaml"
yq -e '[.drivers.sandbox.configuration.gateway.operatorWorkspaceResources[].kind] |
  sort | join(",") == "NetworkPolicy,Role,RoleBinding,ServiceAccount"' \
  "$OCC_INPUT_DIRECTORY/installation.yaml"
```

The fragment carries no `workspaceMode` or `runtimeClassName`. Validate the
merged Installation:

```bash
yq -e '[.. | select(has("workspaceMode") or has("runtimeClassName"))] | length == 0' \
  "$OCC_INPUT_DIRECTORY/installation.yaml"
deploy/rhoai/scripts/validate-installation "$OCC_INPUT_DIRECTORY/installation.yaml"
```

Expect `Installation configuration valid` under Node 24.22.1.

## Phase 6: bootstrap Secret and Helm install

This checkout uses Secret `occ-bootstrap-credentials` via
`bootstrap.credentials.secretName`; do **not** run `prepare-bootstrap-volume`,
create a bootstrap PVC, or apply the bootstrap SCC (stale README sections).

```bash
node --input-type=module <<'JS'
import { randomBytes } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
const dir = process.env.OCC_INPUT_DIRECTORY;
const key = `occ_${randomBytes(32).toString('base64url')}`;
writeFileSync(join(dir, 'admin-password'), randomBytes(32).toString('base64url'), { mode: 0o600, flag: 'wx' });
writeFileSync(join(dir, 'admin-service-key'), key, { mode: 0o600, flag: 'wx' });
writeFileSync(join(dir, 'initial-admin-service-key.json'), JSON.stringify({ data: { key } }), { mode: 0o600, flag: 'wx' });
JS

oc -n openclaw-system create secret generic occ-bootstrap-credentials \
  --from-file=admin-password="$OCC_INPUT_DIRECTORY/admin-password" \
  --from-file=admin-service-key="$OCC_INPUT_DIRECTORY/admin-service-key"
```

The chart projects both keys into the bootstrap Job on install only; keep the
raw files as the only copy. `initial-admin-service-key.json` wraps the key for
`OCC_SERVICE_KEY_FILE`.

Create the Secrets and install:

```bash
oc -n openclaw-system create secret generic occ-installation-startup \
  --from-file=installation.yaml="$OCC_INPUT_DIRECTORY/installation.yaml"
oc -n openclaw-system create secret generic occ-database \
  --from-file=application-url="$OCC_INPUT_DIRECTORY/occ-application-url" \
  --from-file=migration-url="$OCC_INPUT_DIRECTORY/occ-migration-url"
oc -n openclaw-system create secret generic occ-database-ca \
  --from-file=ca.pem="$OCC_INPUT_DIRECTORY/occ-database-ca.pem"
oc -n openclaw-system create secret generic occ-auth \
  --from-file=secret="$OCC_INPUT_DIRECTORY/occ-auth-secret"

yq -i '.worker.maxAttempts = 30 | .worker.leaseDurationMs = 30000' \
  "$OCC_INPUT_DIRECTORY/values.yaml"
helm template oce "$OCE_CHECKOUT/deploy/helm/openclaw-enterprise" \
  --namespace openclaw-system -f "$OCC_INPUT_DIRECTORY/values.yaml" \
  > "$OCC_INPUT_DIRECTORY/oce-rendered.yaml"
oc apply --dry-run=server -f "$OCC_INPUT_DIRECTORY/oce-rendered.yaml"
helm upgrade --install oce "$OCE_CHECKOUT/deploy/helm/openclaw-enterprise" \
  --namespace openclaw-system -f "$OCC_INPUT_DIRECTORY/values.yaml" \
  --wait --timeout 7m
```

Do not add `-n openclaw-system`; the chart renders its own
`oce-installation-secrets-*` Namespace. The dry run cannot create that Namespace,
so four `NotFound` lines for it are expected; any other error is fatal.

Expose and check the console:

```bash
oc apply -f "$OCC_INPUT_DIRECTORY/console-route.yaml"
curl --fail --silent --show-error "https://$OCC_CONSOLE_HOST/healthz" >/dev/null
curl --fail --silent --show-error "https://$OCC_CONSOLE_HOST/console/login" >/dev/null
```

## Phase 7: CLI and authentication

```bash
export OCC_URL="https://$OCC_CONSOLE_HOST"
export OCC_SERVICE_KEY_FILE="$OCC_INPUT_DIRECTORY/initial-admin-service-key.json"
deploy/rhoai/scripts/build-occ
deploy/rhoai/bin/occ installation get
deploy/rhoai/bin/occ namespace list
```

Sign in at `https://$OCC_CONSOLE_HOST/console/login` with `OCC_ADMIN_EMAIL` and
the password in `$OCC_INPUT_DIRECTORY/admin-password`.

Bootstrap's `default` has no tenant bindings, so it converges to `failed` after
about 15 minutes and is never retried. Delete it
(`occ namespace delete <ID from namespace list>`) and use Phase 9's Namespace.

## Phase 8: private Envoy routing

Pull and digest-check the pinned charts:

```bash
export ENVOY_CHARTS="$OCC_INPUT_DIRECTORY/envoy-charts"
install -d -m 700 "$ENVOY_CHARTS"
helm pull oci://docker.io/envoyproxy/gateway-crds-helm --version v1.7.5 \
  --destination "$ENVOY_CHARTS" >"$ENVOY_CHARTS/crds.pull" 2>&1
test "$(sed -n 's/^Digest: //p' "$ENVOY_CHARTS/crds.pull")" = \
  sha256:f952368c9543948a9637178ddf37d0f90fda2560e4e0f54b3489b700a7b817fd
helm pull oci://docker.io/envoyproxy/gateway-helm --version v1.7.5 \
  --destination "$ENVOY_CHARTS" >"$ENVOY_CHARTS/gateway.pull" 2>&1
test "$(sed -n 's/^Digest: //p' "$ENVOY_CHARTS/gateway.pull")" = \
  sha256:94a3efa254e19731a0e8093f563221c98f34d102b8fcfaf5cc37dd2f3e52739d
```

Install only Envoy's CRDs:

```bash
oc create namespace envoy-gateway-system --dry-run=client -o yaml | oc apply -f -
oc apply -f deploy/rhoai/manifests/openshift/base/routing/envoy-controller-scc-rbac.yaml
helm template eg-crds "$ENVOY_CHARTS/gateway-crds-helm-v1.7.5.tgz" \
  --set crds.gatewayAPI.enabled=false \
  --set crds.envoyGateway.enabled=true \
  > "$OCC_INPUT_DIRECTORY/envoy-gateway-crds.yaml"
oc apply --server-side -f "$OCC_INPUT_DIRECTORY/envoy-gateway-crds.yaml"
```

`crds.gatewayAPI.enabled=false` keeps the OpenShift/RHOAI Gateway API;
Envoy's copy replaces the managed CRDs.

```bash
helm upgrade --install eg "$ENVOY_CHARTS/gateway-helm-v1.7.5.tgz" \
  -n envoy-gateway-system --skip-crds --wait --timeout 7m
oc apply -f deploy/rhoai/manifests/openshift/base/routing/envoy-gateway-class.yaml
oc get gatewayclass eg
```

OpenShell's resolver needs the full cluster DNS name.

```bash
test -e "$OCC_INPUT_DIRECTORY/gateway-api-key" || \
  (umask 077; python3 -c 'import secrets; print(secrets.token_hex(32), end="")' \
    > "$OCC_INPUT_DIRECTORY/gateway-api-key")
oc -n openclaw-system create secret generic occ-private-gateway-key \
  --from-file=occ="$OCC_INPUT_DIRECTORY/gateway-api-key"
export CLUSTER_DNS_DOMAIN="$(oc -n openclaw-system exec deployment/openclaw-enterprise-api -- \
  cat /etc/resolv.conf | awk '/^search / { for (i = 2; i <= NF; i++) if ($i ~ /^svc[.]/) { sub(/^svc[.]/, "", $i); print $i; exit } }')"
export ENVOY_SERVICE="occ-gateway-$(printf '%s' 'openclaw-system/oce-agent-gateways' | \
  sha256sum | cut -c1-12)"
export PRIVATE_GATEWAY_HOST="$ENVOY_SERVICE.envoy-gateway-system.svc.$CLUSTER_DNS_DOMAIN"
yq -i '.gatewayRouting.enabled = true |
  .gatewayRouting.gatewayClassName = "eg" |
  .gatewayRouting.apiKeySecretName = "occ-private-gateway-key" |
  .gatewayRouting.hostname = strenv(PRIVATE_GATEWAY_HOST)' \
  "$OCC_INPUT_DIRECTORY/values.yaml"
helm upgrade --install oce "$OCE_CHECKOUT/deploy/helm/openclaw-enterprise" \
  --namespace openclaw-system -f "$OCC_INPUT_DIRECTORY/values.yaml" \
  --wait --timeout 7m
```

Grant `nonroot-v2` to the generated data plane and verify:

```bash
export ENVOY_DEPLOY="$(oc -n envoy-gateway-system get deployments \
  -l gateway.envoyproxy.io/owning-gateway-namespace=openclaw-system,gateway.envoyproxy.io/owning-gateway-name=oce-agent-gateways \
  -o jsonpath='{.items[0].metadata.name}')"
export ENVOY_SA="$(oc -n envoy-gateway-system get deployment "$ENVOY_DEPLOY" \
  -o jsonpath='{.spec.template.spec.serviceAccountName}')"
oc -n envoy-gateway-system create rolebinding eg-private-dataplane-nonroot \
  --role=eg-nonroot-v2 --serviceaccount="envoy-gateway-system:$ENVOY_SA"
oc -n envoy-gateway-system rollout status deployment/"$ENVOY_DEPLOY" --timeout=3m
oc wait --for=condition=Programmed gateway/oce-agent-gateways \
  -n openclaw-system --timeout=5m
```

Switch Compute to the private Gateway:

```bash
export POD_NETWORK_CIDR="$(oc get network.config.openshift.io cluster \
  -o jsonpath='{.spec.clusterNetwork[0].cidr}')"
yq -i '.drivers.compute.configuration.gatewayRouting = {
  "gatewayName":"oce-agent-gateways","gatewayNamespace":"openclaw-system",
  "envoyNamespace":"envoy-gateway-system","hostname":strenv(PRIVATE_GATEWAY_HOST)} |
  del(.drivers.compute.configuration.network.gatewayClients) |
  .drivers.compute.configuration.network.gatewayTrustedProxyCidrs = [strenv(POD_NETWORK_CIDR)]' \
  "$OCC_INPUT_DIRECTORY/installation.yaml"
yq -i 'del(.drivers.sandbox.configuration.gateway.workspaceMode) |
  (.. | select(has("runtimeClassName")) |
   select(path[0] == "drivers" and path[1] == "sandbox")) |= del(.runtimeClassName)' \
  "$OCC_INPUT_DIRECTORY/installation.yaml"
deploy/rhoai/scripts/validate-installation "$OCC_INPUT_DIRECTORY/installation.yaml"
oc -n openclaw-system create secret generic occ-installation-startup \
  --from-file=installation.yaml="$OCC_INPUT_DIRECTORY/installation.yaml" \
  --dry-run=client -o json | jq '{data:.data}' | \
  oc -n openclaw-system patch secret occ-installation-startup \
    --type=merge --patch-file=/dev/stdin
oc -n openclaw-system rollout restart \
  deployment/openclaw-enterprise-api deployment/openclaw-enterprise-worker
oc -n openclaw-system rollout status deployment/openclaw-enterprise-api --timeout=4m
oc -n openclaw-system rollout status deployment/openclaw-enterprise-worker --timeout=4m
```

## Phase 9: routing-native Namespace and safe grants

Create the Namespace after routing is enabled so provisioning adds the Gateway
label and `allow-gateway-ingress`; a ready one never picks them up.

```bash
deploy/rhoai/bin/occ namespace create agents
deploy/rhoai/bin/occ namespace list
export OCC_NAMESPACE='<ID of the new agents Namespace>'
TENANT_NAMESPACE=""
for _ in {1..120}; do
  TENANT_NAMESPACE="$(oc get namespaces -l "openclaw.dev/namespace=$OCC_NAMESPACE" -o json |
    jq -r '.items | if length == 1 then .[0].metadata.name else empty end')"
  [[ -n "$TENANT_NAMESPACE" ]] && break
  sleep 1
done
test -n "$TENANT_NAMESPACE"
export TENANT_NAMESPACE
export GATEWAY_NAMESPACE="$TENANT_NAMESPACE"
```

Bind the OpenShell writer first. Namespace provisioning starts when the tenant
namespace appears and treats a 403 as permanent, so this grant must exist
before the worker first applies the workspace Role.

```bash
oc -n "$TENANT_NAMESPACE" create rolebinding openclaw-enterprise-worker-openshell \
  --clusterrole=oce-openshell-workspace-writer \
  --serviceaccount=openclaw-system:openclaw-enterprise-worker \
  --dry-run=client -o yaml | oc apply -f -
```

Bind the API observer, Secrets, and Configuration identities:

```bash
for grant in \
  openclaw-enterprise-api-observer:oce-openclaw-gateway-observer \
  openclaw-enterprise-api-secrets:oce-openclaw-tenant-api \
  openclaw-enterprise-api-configuration:oce-openclaw-tenant-configuration; do
  oc -n "$TENANT_NAMESPACE" create rolebinding "${grant%%:*}" \
    --clusterrole="${grant##*:}" \
    --serviceaccount=openclaw-system:openclaw-enterprise-api \
    --dry-run=client -o yaml | oc apply -f -
done
```

Bind the ordinary runtime worker last:

```bash
oc -n "$TENANT_NAMESPACE" create rolebinding openclaw-enterprise-worker \
  --clusterrole=oce-openclaw-tenant-worker \
  --serviceaccount=openclaw-system:openclaw-enterprise-worker \
  --dry-run=client -o yaml | oc apply -f -
```

Verify the worker's Role permissions:

```bash
oc auth can-i create roles -n "$TENANT_NAMESPACE" \
  --as=system:serviceaccount:openclaw-system:openclaw-enterprise-worker
oc auth can-i bind roles/openshell-workspace-sandbox -n "$TENANT_NAMESPACE" \
  --as=system:serviceaccount:openclaw-system:openclaw-enterprise-worker
deploy/rhoai/bin/occ namespace get "$OCC_NAMESPACE"
```

Both must return `yes` and the Namespace must be `ready`. A `failed` Namespace
already hit the 403 and is not retried: apply the writer grant, then create a
new one (`agents2`).

To reuse the `default` Namespace, repair it first:
[Enable routing for existing Namespaces and Agents](../../docs/guides/deploy/workspace-routing.md#enable-routing-for-existing-namespaces-and-agents).

## Phase 10: Dedicated Codex Agent and the Gateway SCC

The Agent Gateway runs as UID/GID 1000. Without the SCC grant, OpenShift rejects
its Pod, PVCs stay `Pending`, and the revision fails at the convergence deadline;
`OpenShell reports no such sandbox` is a symptom.

Register the model credential:

```bash
umask 077
read -r -s -p 'OpenAI API key: ' OPENAI_API_KEY
printf '\n'
printf '%s' "$OPENAI_API_KEY" |
  jq -Rs '{name: "openai-model-key", value: .}' \
  > "$OCC_INPUT_DIRECTORY/model-secret.json"
unset OPENAI_API_KEY
export SECRET_REF="$(deploy/rhoai/bin/occ secret create \
  --file "$OCC_INPUT_DIRECTORY/model-secret.json" -o json | jq -c .ref)"
rm -f "$OCC_INPUT_DIRECTORY/model-secret.json"
test -n "$SECRET_REF" && test "$SECRET_REF" != null
jq -n --argjson ref "$SECRET_REF" \
  '{name: "openai", type: "openai", secrets: {api_key: $ref}}' \
  > "$OCC_INPUT_DIRECTORY/credential-source.json"
export SOURCE_ID="$(deploy/rhoai/bin/occ credential-source create \
  --file "$OCC_INPUT_DIRECTORY/credential-source.json" -o json | jq -r .id)"
deploy/rhoai/bin/occ credential-source get "$SOURCE_ID"
```

Expect `STATE` and `GATEWAY STATUS` `ready`.

Create the Configuration and Agent without deploying:

```bash
export CONFIGURATION_FILE=deploy/rhoai/manifests/openshift/codex-luna-configuration.json
export AGENT_NAME='my-codex-agent'
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
export AGENT_PRINCIPAL="$(jq -r .servicePrincipalId "$OCC_INPUT_DIRECTORY/agent-response.json")"
export AGENT_SA="agent-$(printf '%s' "$AGENT_ID" | sha256sum |
  awk '{print substr($1, 1, 12)}')"
export GATEWAY_SA="gateway-${AGENT_SA#agent-}"
```

Grant `operate` on the exact credential source:

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

Grant the SCC **before** `agent deploy`:

```bash
oc -n "$GATEWAY_NAMESPACE" create role oce-fixed-uid-1000-agent-gateway \
  --verb=use --resource=securitycontextconstraints.security.openshift.io \
  --resource-name=oce-fixed-uid-1000 \
  --dry-run=client -o yaml | oc apply -f -
oc -n "$GATEWAY_NAMESPACE" create rolebinding oce-fixed-uid-1000-agent-gateway \
  --role=oce-fixed-uid-1000-agent-gateway \
  --serviceaccount="$GATEWAY_NAMESPACE:$GATEWAY_SA" \
  --dry-run=client -o yaml | oc apply -f -
oc auth can-i use scc/oce-fixed-uid-1000 -n "$GATEWAY_NAMESPACE" \
  --as="system:serviceaccount:$GATEWAY_NAMESPACE:$GATEWAY_SA"
```

Expect `yes`. Keep `openshell-sandbox` on `restricted-v2`.

Deploy and verify:

```bash
deploy/rhoai/bin/occ agent deploy "$AGENT_ID"
```

Record the revision ID:

```bash
export DEPLOYMENT_ID='<revision ID returned by deploy>'
deploy/rhoai/bin/occ agent deployment-status "$AGENT_ID" "$DEPLOYMENT_ID"
oc -n "$TENANT_NAMESPACE" get secrets -o json |
  jq -e --arg prefix "workspace-node-${AGENT_SA#agent-}-" \
    '[.items[] | select(.metadata.name | startswith($prefix)) |
      select(.data.deviceId != null)] | length == 1'
```

Wait for `succeeded`. The `jq` check prints `true`. Prove a turn with
[HTTP chat](manifests/openshift/README.md#verify-a-dedicated-codex-agent-turn).

The console **Create Agent** button deploys immediately; read the Agent ID from
the URL, grant the SCC with the derived `GATEWAY_SA`, then deploy a new version.
Check `oc -n "$GATEWAY_NAMESPACE" get pods` before chasing OpenShell errors.

## Phase 11: failure triage

| Symptom                                                                                             | Cause                                                 | Action                                               |
| --------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ---------------------------------------------------- |
| No Gateway Pod; PVC `Pending`; `OpenShell reports no such sandbox`; `CONVERGENCE_DEADLINE_EXCEEDED` | SCC rejected the Gateway Pod                          | Grant `use` on `oce-fixed-uid-1000` to `$GATEWAY_SA` |
| Namespace `failed`; 403 creating Role `openshell-workspace-sandbox`                                 | Writer RoleBinding missing when provisioning ran      | Apply the writer grant; create a new Namespace       |
| `RUNTIME_AUTHENTICATION_FAILED` with running Pods                                                   | Provider rejected the model key                       | Replace the credential source and deploy again       |
| Helm rejects `sharedCookieDomain`, `workspaceMode`, or `runtimeClassName`                           | Stale values or Installation                          | Reapply the Phase 5 removals                         |
| Upgrade fails with `the Helm release changed before submission`                                     | Helm 4                                                | Use Helm 3.19.2                                      |

## Optional: Control UI

After the Agent reports `succeeded`, follow
[Control UI Route](manifests/openshift/README.md#control-ui-route): grant the
intended OpenShift user `get` on the **exact** Gateway Service and add the HTTPS
origin to the Configuration before redeploying. Open `/<agent>/new`.

Dashboard, Keycloak, widgets, and mTLS are optional; see
[the deployment guide](README.md).
