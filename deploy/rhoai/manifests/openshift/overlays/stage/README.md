# Stage profile

The overlay that the GitOps repository
[`rh-oce-team/oce-ocp-setup`](https://github.com/rh-oce-team/oce-ocp-setup)
reconciles for `operators`, `database`, `admission`, `openshell`, and `routing`.
It is a set of overlays on `../../base`, not a full deployment procedure.
`control-plane`, `control-ui`, and `control-ui-access` stay in the deployment
guide and keep using `../test` through `scripts/prepare-kustomize`.

The committed values are placeholders for a public repository. The
`oce-ocp-setup/environment: stage` label and the 10Gi PostgreSQL volume are the
only deviations from the base today; replace the volume with the real
allocation before the stage redeploy.

| Stage        | Argo Application  | Wave | Namespace              | Deviation                |
| ------------ | ----------------- | ---- | ---------------------- | ------------------------ |
| `operators`  | `oce-operators`   | 0    | `openclaw-postgres`    | environment label        |
| `database`   | `oce-database`    | 1    | `openclaw-postgres`    | environment label, 10Gi  |
| `admission`  | `oce-admission`   | 1    | `openclaw-system`      | environment label        |
| `openshell`  | `oce-openshell`   | 2    | `openshell-system`     | environment label        |
| `routing`    | `oce-routing`     | 3    | `envoy-gateway-system` | environment label        |

## Why `labels` and not `commonLabels`

The `labels` transformer with `includeSelectors: false` and
`includeTemplates: false` adds the environment label to object metadata only.
`commonLabels` would also inject it into every NetworkPolicy `podSelector` and
`namespaceSelector`. The Pods those selectors target are created outside these
overlays (the `oce` Helm release, OCE-managed workspace Pods, and the OpenShell
gateway release), so the selectors would match nothing and the policies would
deny the traffic they exist to allow.

## Values that are not committed

Cluster-specific values stay in the private input directory described in the
deployment guide: hostnames, API and PostgreSQL egress addresses, router CIDRs,
image digests, and every Secret.
