#!/bin/bash
# Build an OCE Konflux image with podman the way the PipelineRuns do, then run
# its smoke test. Run from the repository root:
#
#     deploy/konflux/build-local.sh oce-controller
#     deploy/konflux/build-local.sh oce-runtime
set -euo pipefail

component="${1:?usage: deploy/konflux/build-local.sh oce-runtime|oce-controller}"
case "$component" in
    oce-runtime | oce-controller) ;;
    *)
        echo "unknown component: $component" >&2
        exit 1
        ;;
esac

commit=$(git rev-parse HEAD)
image="localhost/${component}:${commit:0:7}"
podman build \
    --file "deploy/docker/Dockerfile.konflux.${component}" \
    --build-arg-file "deploy/konflux/${component}/build-args.conf" \
    --build-arg "OCE_COMMIT=${commit}" \
    --tag "$image" \
    .
TEST_IMAGE="$image" OCE_COMMIT="$commit" bash "deploy/konflux/${component}/smoke-test.sh"
