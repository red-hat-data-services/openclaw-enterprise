# Vendored chart provenance

- Source: <https://github.com/opendatahub-io/openshell-dashboard>, path
  `deploy/helm/openshell-dashboard`, commit
  `f74a27417c50a56708ba1b487d5e42b954b341f8` (2026-11-05).
- Local change: `values.yaml` only. The dashboard image registry and repository
  point at the Red Hat Konflux build
  `quay.io/opendatahub/odh-openshell-dashboard` instead of
  `ghcr.io/ai-openshell/console`. Every other file matches upstream byte for
  byte.

To re-vendor, check out the new upstream revision, copy the chart directory,
reapply the image values, and diff the result against this copy. Update the
dashboard image digest in `dashboard-values.yaml` in the same change.
