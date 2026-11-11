# OpenShell mTLS profile

Require a client certificate for the OpenShell gateway, keep the dashboard on
OIDC, and give OCE its own administrator certificate. OCE keeps connecting to
the gateway Service directly; its NetworkPolicy still restricts that path.

## Prerequisites

- cert-manager and the existing `openshell-gateway` release with its CA.
- The dashboard OIDC overlay, applied as described in
  [the dashboard guide](../../dashboard/README.md). Its private directory has
  `dashboard-values.yaml` and `gateway-values-before.yaml`.
- `OCE_CHECKOUT` matching the installed controller.
- A maintenance window. The gateway restart pauses OCE, dashboard, and Agent
  connections; back up the gateway PVC before the cutover.

```bash
export DASHBOARD_INPUT_DIRECTORY="$HOME/.local/state/openclaw-enterprise/openshell-dashboard"
export DASHBOARD_CHART=deploy/rhoai/manifests/dashboard/chart
test -s "$DASHBOARD_INPUT_DIRECTORY/dashboard-values.yaml"
test -s "$DASHBOARD_INPUT_DIRECTORY/gateway-values-before.yaml"
```

## 1. Stage the CA and Issuer before requesting a client certificate

The default gateway install has cert-manager-managed PKI disabled. Render the
mTLS overlay to a file, then stage only its cert-manager resources. The running
gateway keeps its current transport until the cutover.

```bash
helm template openshell-gateway \
  "$OCC_INPUT_DIRECTORY/openshell-charts/helm-chart-0.1.6.tgz" \
  -n openshell-system --is-upgrade --set agentSandbox.preflight.enabled=false \
  -f "$DASHBOARD_INPUT_DIRECTORY/gateway-values-before.yaml" \
  -f deploy/rhoai/manifests/openshift/openshell-mtls/gateway-values.yaml \
  --post-renderer deploy/rhoai/scripts/openshell-mtls-post-render \
  > "$DASHBOARD_INPUT_DIRECTORY/gateway-mtls-rendered.yaml"

yq 'select(.apiVersion == "cert-manager.io/v1") |
  .metadata.annotations."meta.helm.sh/release-name" = "openshell-gateway" |
  .metadata.annotations."meta.helm.sh/release-namespace" = "openshell-system"' \
  "$DASHBOARD_INPUT_DIRECTORY/gateway-mtls-rendered.yaml" \
  > "$DASHBOARD_INPUT_DIRECTORY/gateway-pki.yaml"

oc apply -f "$DASHBOARD_INPUT_DIRECTORY/gateway-pki.yaml"
oc -n openshell-system wait --for=condition=Ready \
  certificate/openshell-gateway-ca --timeout=3m
oc -n openshell-system wait --for=condition=Ready \
  certificate/openshell-gateway-server --timeout=3m
oc -n openshell-system wait --for=condition=Ready \
  certificate/openshell-gateway-client --timeout=3m
```

Apply the client certificate only after the Issuer exists, then copy the leaf
credential to OCE and the public CA to the dashboard. Never copy the CA private
key.

```bash
oc apply -k deploy/rhoai/manifests/openshift/openshell-mtls
oc -n openshell-system wait --for=condition=Ready \
  certificate/oce-openshell-admin --timeout=3m

oc -n openshell-system get secret oce-openshell-admin -o json |
  jq '{apiVersion: "v1", kind: "Secret", type: "kubernetes.io/tls",
    metadata: {name: "oce-openshell-admin", namespace: "openclaw-system"},
    data: {"ca.crt": .data["ca.crt"], "tls.crt": .data["tls.crt"],
           "tls.key": .data["tls.key"]}}' |
  oc apply --server-side --field-manager=oce-mtls-secret-sync -f -
oc -n openshell-system get secret oce-openshell-admin -o json |
  jq '{apiVersion: "v1", kind: "Secret", type: "Opaque",
    metadata: {name: "openshell-dashboard-gateway-ca",
               namespace: "openshell-system"},
    data: {"ca.crt": .data["ca.crt"]}}' |
  oc apply --server-side --field-manager=oce-mtls-secret-sync -f -
```

## 2. Point OCE at mTLS

Set the client Secret in the private Helm values:

```yaml
backend:
  openshell:
    clientSecretName: oce-openshell-admin
```

Change the Installation backend to HTTPS with the client certificate, and remove
`insecureTransport`:

```yaml
backend:
  - id: openshell
    configuration:
      endpoint: https://openshell-gateway.openshell-system.svc.cluster.local:8080
      auth:
        mode: mtls
        certificatePath: /etc/openclaw/openshell/tls.crt
        privateKeyPath: /etc/openclaw/openshell/tls.key
      rootCertificatePath: /etc/openclaw/openshell/ca.crt
```

Validate the Installation, recompute the checksum, and publish the edited
Installation Secret. Restarting Pods does not publish a local YAML change.

```bash
: "${OCE_CHECKOUT:?Set the checkout matching the installed controller}"
export OCC_INSTALLATION_CHECKSUM
OCC_INSTALLATION_CHECKSUM=$(sha256sum "$OCC_INPUT_DIRECTORY/installation.yaml" |
  cut -d ' ' -f 1)
yq -i '.controlPlane.installationChecksum = strenv(OCC_INSTALLATION_CHECKSUM)' \
  "$OCC_INPUT_DIRECTORY/values.yaml"
deploy/rhoai/scripts/validate-installation "$OCC_INPUT_DIRECTORY/installation.yaml"
oc -n openclaw-system create secret generic occ-installation-startup \
  --from-file=installation.yaml="$OCC_INPUT_DIRECTORY/installation.yaml" \
  --dry-run=client -o json | jq '{data: .data}' |
  oc -n openclaw-system patch secret occ-installation-startup \
    --type=merge --patch-file=/dev/stdin
```

## 3. Cut over gateway, OCE, and dashboard

Apply the gateway values overlay so the gateway requires mTLS and authenticated
users, then upgrade OCE and the dashboard in the same window.

```bash
helm upgrade openshell-gateway \
  "$OCC_INPUT_DIRECTORY/openshell-charts/helm-chart-0.1.6.tgz" \
  --namespace openshell-system \
  -f "$DASHBOARD_INPUT_DIRECTORY/gateway-values-before.yaml" \
  -f "$DASHBOARD_INPUT_DIRECTORY/gateway-oidc-values.yaml" \
  -f deploy/rhoai/manifests/openshift/openshell-mtls/gateway-values.yaml \
  --post-renderer deploy/rhoai/scripts/openshell-mtls-post-render \
  --wait --timeout 7m

helm upgrade oce "$OCE_CHECKOUT/deploy/helm/openclaw-enterprise" \
  --namespace openclaw-system -f "$OCC_INPUT_DIRECTORY/values.yaml" \
  --wait --timeout 7m

export KEYCLOAK_HOST="$(yq -r '.oidc.issuer | sub("^https://"; "") | split("/")[0]' \
  "$DASHBOARD_INPUT_DIRECTORY/dashboard-values.yaml")"
export ROUTER_IP="$(oc -n openshift-ingress get svc router-default \
  -o jsonpath='{.spec.clusterIP}')"
test -n "$KEYCLOAK_HOST" && test -n "$ROUTER_IP"
helm upgrade openshell-dashboard "$DASHBOARD_CHART" \
  --namespace openshell-system \
  -f "$DASHBOARD_INPUT_DIRECTORY/dashboard-values.yaml" \
  -f deploy/rhoai/manifests/openshift/openshell-mtls/dashboard-values.yaml \
  --post-renderer deploy/rhoai/scripts/dashboard-oidc-post-render \
  --wait --timeout 7m
```

The dashboard overlay switches its gateway URL to `grpcs://` and mounts
`openshell-dashboard-gateway-ca`; the post-renderer keeps Keycloak OIDC on the
allowed router path. Without the overlay, sign-in can succeed while gateway
operations fail.

## Verify and renew

- Sign in to the dashboard through Keycloak and perform a permitted read. Deploy
  or verify an Agent revision and complete a model turn; that proves OCE and the
  sandbox accepted the new transport.
- cert-manager renews the OCE certificate 360h before expiry and updates the
  Secret in `openshell-system`. After each rotation, repeat the leaf-copy command
  and restart the API and worker: the mounted files change in place, but OCE
  reads them at startup.
