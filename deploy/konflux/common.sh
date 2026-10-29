#!/bin/bash
# Shared smoke-test helpers for the OCE Konflux images.
set -euo pipefail

PASS=0
FAIL=0

assert_ok() {
    local desc="$1"
    shift
    if "$@" > /dev/null 2>&1; then
        echo "PASS: $desc"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $desc"
        FAIL=$((FAIL + 1))
    fi
}

assert_contains() {
    local desc="$1" output="$2" pattern="$3"
    if echo "$output" | grep -qi "$pattern"; then
        echo "PASS: $desc"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $desc -- expected '$pattern' in output"
        FAIL=$((FAIL + 1))
    fi
}

run_in() {
    local image="$1"
    shift
    podman run --rm --entrypoint "" "$image" "$@"
}

assert_hummingbird_base() {
    local image="$1"
    local runtime_image builder_image runtime_stream builder_stream
    assert_ok "base OS is Hummingbird" run_in "$image" \
        bash -c '. /etc/os-release; test "$ID" = hummingbird'
    assert_ok "runtime image excludes package managers" run_in "$image" \
        bash -c '! command -v dnf && ! command -v microdnf'
    assert_ok "runtime image excludes package-manager state" run_in "$image" \
        bash -c 'test ! -e /var/cache/libdnf5 &&
                 test ! -e /var/log/dnf5.log && test ! -e /run/dnf'

    runtime_image=$(podman image inspect --format \
        '{{index .Labels "io.openshell.hummingbird.runtime-image"}}' "$image")
    builder_image=$(podman image inspect --format \
        '{{index .Labels "io.openshell.hummingbird.builder-image"}}' "$image")
    runtime_stream=${runtime_image%@sha256:*}
    builder_stream=${builder_image%@sha256:*}
    assert_ok "runtime image label is digest-pinned" \
        bash -c '[[ "$1" =~ @sha256:[0-9a-f]{64}$ ]]' _ "$runtime_image"
    assert_ok "builder image label is digest-pinned" \
        bash -c '[[ "$1" =~ @sha256:[0-9a-f]{64}$ ]]' _ "$builder_image"
    assert_ok "runtime and builder labels use matching streams" \
        test "$builder_stream" = "${runtime_stream}-builder"
}

assert_node_major() {
    local image="$1" expected="$2"
    assert_ok "Node.js major version is ${expected}" run_in "$image" \
        node -e 'process.exit(process.versions.node.split(".")[0] === process.argv[1] ? 0 : 1)' "$expected"
}

assert_node_min() {
    local image="$1" expected="$2"
    assert_ok "Node.js is ${expected}+ in the ${expected%%.*} series" run_in "$image" \
        node -e 'const [major, minor, patch] = process.versions.node.split(".").map(Number); const [wantMajor, wantMinor, wantPatch] = process.argv[1].split(".").map(Number); process.exit(major === wantMajor && (minor > wantMinor || (minor === wantMinor && patch >= wantPatch)) ? 0 : 1)' "$expected"
}

print_results() {
    local status=$?
    echo ""
    echo "=== Results ==="
    echo "Passed: $PASS"
    if [[ "$FAIL" -gt 0 ]]; then
        echo "Failed: $FAIL"
        exit 1
    elif [[ "$status" -ne 0 ]]; then
        echo "ERROR: test script aborted with status ${status}"
        exit "$status"
    elif [[ "$PASS" -eq 0 ]]; then
        echo "ERROR: test script aborted before running any assertion"
        exit 1
    else
        echo "All tests passed."
    fi
}
trap print_results EXIT
