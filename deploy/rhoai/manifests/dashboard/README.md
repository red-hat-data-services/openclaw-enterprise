# OpenShell dashboard and Keycloak

Add an authenticated OpenShell dashboard to the OpenShift deployment. The
dashboard runs in `openshell-system`; Keycloak and its operator run in
`openshell-auth`. Keycloak is used only by this dashboard: OCC sign-in and the
Agents' OpenShift OAuth login stay separate.

Start after OCC, OpenShell, and the PostgreSQL cluster are working. Use a
cluster-admin login and keep existing database credentials, PVCs, and OpenShell
Helm values. Keycloak gets a separate database and role on the existing
PostgreSQL instance and inherits its lack of backups and high availability.

The dashboard image is the Red Hat Konflux build of
`opendatahub-io/openshell-dashboard` published on `quay.io/opendatahub`, pinned
by digest in `dashboard-values.yaml`, like the gateway images. The chart is
vendored in `deploy/rhoai/manifests/dashboard/chart`; no external chart registry
or local dashboard build is needed.

## 1. Select inputs and install the operator

```bash
umask 077
: "${OCC_INPUT_DIRECTORY:?Set the existing private input directory}"
export DASHBOARD_INPUT_DIRECTORY="$HOME/.local/state/openclaw-enterprise/openshell-dashboard"
install -d -m 700 "$DASHBOARD_INPUT_DIRECTORY"
export CLUSTER_DOMAIN="$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')"
export KEYCLOAK_HOST="openshell-auth.$CLUSTER_DOMAIN"
export DASHBOARD_HOST="openshell-dashboard.$CLUSTER_DOMAIN"
oc apply -f deploy/rhoai/manifests/dashboard/keycloak-operator.yaml
```

Approve only the pinned Keycloak CSV. Future operator upgrades also require
explicit approval.

```bash
export KEYCLOAK_PLAN="$(oc -n openshell-auth get subscription rhbk-operator \
  -o jsonpath='{.status.installPlanRef.name}')"
oc -n openshell-auth get installplan "$KEYCLOAK_PLAN" -o json |
  jq -e '.spec.clusterServiceVersionNames == ["rhbk-operator.v26.6.7-opr.1"]'
oc -n openshell-auth patch installplan "$KEYCLOAK_PLAN" \
  --type=merge -p '{"spec":{"approved":true}}'
oc -n openshell-auth wait --for=jsonpath='{.status.phase}'=Succeeded \
  csv/rhbk-operator.v26.6.7-opr.1 --timeout=5m
```

## 2. Prepare the database and protected credentials

```bash
python3 - "$DASHBOARD_INPUT_DIRECTORY" <<'PY'
from pathlib import Path
import secrets, sys

root = Path(sys.argv[1])
for name in ('database-password', 'client-secret', 'user-password'):
    with (root / name).open('x') as output:
        output.write(secrets.token_hex(24))
# oauth2-proxy accepts a 16, 24, or 32 byte cookie secret.
with (root / 'cookie-secret').open('x') as output:
    output.write(secrets.token_hex(16))
(root / 'database-username').write_text('openshell_keycloak')
PY

export POSTGRES_POD="$(oc -n openclaw-postgres get pods \
  -l postgres-operator.crunchydata.com/cluster=oce-postgres,postgres-operator.crunchydata.com/role=master \
  -o jsonpath='{.items[0].metadata.name}')"
export PG_POD_CIDR="$(oc -n openclaw-postgres get pod "$POSTGRES_POD" \
  -o jsonpath='{.status.podIP}')/32"
export DNS_CIDR_0="$(oc -n openshift-dns get svc dns-default \
  -o jsonpath='{.spec.clusterIP}')/32"
(
  printf "\\set keycloak_password '%s'\n" "$(cat "$DASHBOARD_INPUT_DIRECTORY/database-password")"
  cat deploy/rhoai/manifests/dashboard/database.sql
) | oc -n openclaw-postgres exec -i "$POSTGRES_POD" -c database -- \
  psql -U postgres -d postgres -X -v ON_ERROR_STOP=1 -f -

oc -n openshell-auth create secret generic keycloak-database \
  --from-file=username="$DASHBOARD_INPUT_DIRECTORY/database-username" \
  --from-file=password="$DASHBOARD_INPUT_DIRECTORY/database-password"
oc -n openshell-auth create secret generic keycloak-database-ca \
  --from-file=ca.pem="$OCC_INPUT_DIRECTORY/occ-database-ca.pem"
oc -n openshell-auth create secret generic dashboard-realm-bootstrap \
  --from-file=client-secret="$DASHBOARD_INPUT_DIRECTORY/client-secret" \
  --from-file=user-password="$DASHBOARD_INPUT_DIRECTORY/user-password"
```

The SQL creates only the `openshell_keycloak` role and database; it does not
change OCC's database. Stop on an error instead of generating replacement
passwords for an existing database.

## 3. Start Keycloak and import the realm

Render only the hostname variables. The operator resolves the credential
placeholders from Secrets, and Keycloak HTTP ingress is restricted to the
default router Pods.

```bash
envsubst '${KEYCLOAK_HOST} ${PG_POD_CIDR} ${DNS_CIDR_0}' \
  < deploy/rhoai/manifests/dashboard/keycloak.yaml \
  > "$DASHBOARD_INPUT_DIRECTORY/keycloak.yaml"
for file in realm-import.yaml dashboard-values.yaml gateway-oidc-values.yaml; do
  envsubst '${KEYCLOAK_HOST} ${DASHBOARD_HOST}' \
    < "deploy/rhoai/manifests/dashboard/$file" \
    > "$DASHBOARD_INPUT_DIRECTORY/$file"
done
oc apply --dry-run=server -f "$DASHBOARD_INPUT_DIRECTORY/keycloak.yaml"
oc apply -f "$DASHBOARD_INPUT_DIRECTORY/keycloak.yaml"
oc -n openshell-auth wait --for=condition=Ready \
  keycloak/openshell-keycloak --timeout=5m
oc apply -f "$DASHBOARD_INPUT_DIRECTORY/realm-import.yaml"
oc -n openshell-auth wait --for=condition=Done \
  keycloakrealmimport/openshell-dashboard --timeout=5m
curl --fail --silent --show-error \
  "https://$KEYCLOAK_HOST/realms/openshell/.well-known/openid-configuration" >/dev/null
```

Use the Keycloak administrator console for later account changes. Its initial
administrator credentials are in the operator-generated Secret
`openshell-keycloak-initial-admin`; retrieve them through a protected
procedure. The dashboard user's credentials are not Keycloak administrator
credentials.

## 4. Add token validation to OpenShell

This preserves OCC's existing private-network connection mode. Adding OIDC to
the gateway does not secure every existing private-network client, and the
gateway remains private behind its ingress policy. The rolling restart briefly
interrupts gateway connections.

```bash
helm get values openshell-gateway -n openshell-system -o yaml \
  > "$DASHBOARD_INPUT_DIRECTORY/gateway-values-before.yaml"
helm upgrade openshell-gateway \
  "$OCC_INPUT_DIRECTORY/openshell-charts/helm-chart-0.1.6.tgz" \
  --namespace openshell-system \
  -f "$DASHBOARD_INPUT_DIRECTORY/gateway-values-before.yaml" \
  -f "$DASHBOARD_INPUT_DIRECTORY/gateway-oidc-values.yaml" \
  --wait --timeout 7m
```

Include `gateway-oidc-values.yaml` in later gateway upgrades so an OCE-only
values file does not remove the dashboard issuer configuration. For the
coordinated transport change, use the mTLS overlay and post-renderer instead;
they retain this OIDC configuration.

## 5. Install the dashboard

Only the authentication proxy port is exposed; the dashboard backend binds Pod
loopback. Use `-n openshell-system` even for dry runs. The chart is vendored in
`deploy/rhoai/manifests/dashboard/chart` (see
[PROVENANCE](chart/PROVENANCE.md)); the dashboard image is pinned to
`quay.io/opendatahub/odh-openshell-dashboard`.

```bash
export DASHBOARD_CHART=deploy/rhoai/manifests/dashboard/chart
export ROUTER_IP="$(oc -n openshift-ingress get svc router-default \
  -o jsonpath='{.spec.clusterIP}')"
export ROUTER_CIDR="$ROUTER_IP/32"
test -n "$ROUTER_IP"

oc -n openshell-system create secret generic openshell-dashboard-oidc \
  --from-file=client-secret="$DASHBOARD_INPUT_DIRECTORY/client-secret" \
  --from-file=cookie-secret="$DASHBOARD_INPUT_DIRECTORY/cookie-secret"
helm template openshell-dashboard "$DASHBOARD_CHART" \
  --namespace openshell-system -f "$DASHBOARD_INPUT_DIRECTORY/dashboard-values.yaml" \
  --post-renderer deploy/rhoai/scripts/dashboard-oidc-post-render \
  > "$DASHBOARD_INPUT_DIRECTORY/dashboard-rendered.yaml"
oc -n openshell-system apply --dry-run=server \
  -f "$DASHBOARD_INPUT_DIRECTORY/dashboard-rendered.yaml"
envsubst '${ROUTER_CIDR} ${DNS_CIDR_0}' \
  < deploy/rhoai/manifests/dashboard/dashboard-egress.yaml \
  > "$DASHBOARD_INPUT_DIRECTORY/dashboard-egress.yaml"
oc apply -f deploy/rhoai/manifests/dashboard/dashboard-gateway-access.yaml
oc apply -f "$DASHBOARD_INPUT_DIRECTORY/dashboard-egress.yaml"
helm upgrade --install openshell-dashboard "$DASHBOARD_CHART" \
  --namespace openshell-system -f "$DASHBOARD_INPUT_DIRECTORY/dashboard-values.yaml" \
  --post-renderer deploy/rhoai/scripts/dashboard-oidc-post-render \
  --wait --timeout 7m
```

The post-renderer resolves `$KEYCLOAK_HOST` to the router Service ClusterIP
inside the dashboard Pod. OIDC discovery and token exchange keep the public
issuer name while egress stays on the allowed internal path, including on
clusters whose apps DNS returns the external load balancer. Use it on every
dashboard upgrade.

## 6. Sign in and verify

```bash
oc -n openshell-system get deployments,pods,routes
printf 'Dashboard: https://%s\nKeycloak: https://%s\n' "$DASHBOARD_HOST" "$KEYCLOAK_HOST"
cat "$DASHBOARD_INPUT_DIRECTORY/user-password"
```

Sign in as `dashboard-admin` and confirm that the dashboard lists the existing
OCC workspace. Inspect OCC-managed resources through the dashboard, but change
their configuration through OCC. After the realm import reports `Done`, delete
its CR to clean up the Job; this does not delete the realm.

```bash
oc -n openshell-auth delete keycloakrealmimport openshell-dashboard
```

## Limits

- The realm import creates new realms only. Reapplying it does not update
  existing users or clients.
- Keycloak uses the development PostgreSQL instance and inherits its
  single-instance, backup-free profile.
- Adding dashboard OIDC does not convert the gateway's development
  authentication mode into production access control.
- For recovery, upgrade the gateway release with `gateway-values-before.yaml`
  alone. Do not delete the shared gateway, its claim, the PostgreSQL instance,
  or operator CRDs.
- Retain rendered values and the chart checksum for upgrades. Share
  configuration files only; exclude passwords, cookie secrets, client secrets,
  and kubeconfigs.
