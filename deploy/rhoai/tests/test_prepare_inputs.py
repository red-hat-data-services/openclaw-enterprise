"""Offline proof of the merged Installation shape; no cluster or registry access."""

import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

RHOAI = Path(__file__).resolve().parents[1]
REPOSITORY = RHOAI.parents[1]
CONTROLLER_IMAGE = "registry.example.test/oce/controller@sha256:" + "a" * 64
RUNTIME_IMAGE = "registry.example.test/oce/runtime@sha256:" + "b" * 64
OPENSHELL_SERVICE_IP = "10.128.0.10"

# Dispatches prepare-inputs' six discovery calls; anything else fails loudly.
OC_STUB = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["OC_TEST_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
arguments = sys.argv[1:]
if "infrastructure.config.openshift.io" in arguments:
    print("cluster-test")
elif "openclaw-postgres" in arguments:
    print(json.dumps({"items": [{"status": {"podIP": "10.128.0.5"}}]}))
elif "service" in arguments and "kubernetes" in arguments:
    print(json.dumps({"spec": {"clusterIP": "172.30.0.1"}}))
elif "endpointslices" in arguments:
    print(json.dumps({"items": [{
        "endpoints": [{"addresses": ["10.0.0.1", "10.0.0.2"]}],
        "ports": [{"protocol": "TCP", "port": 6443}],
    }]}))
elif "openshift-ingress" in arguments:
    print(json.dumps({"items": [{"status": {"podIP": "10.128.0.9"}}]}))
else:
    sys.exit("unexpected oc call: " + " ".join(arguments))
"""

# Synthetic stand-in for the pinned openshell-workspace 0.1.6 chart render.
# README section 4 renders this chart with `helm template openshell-workspace`
# and strips namespaces; the kinds and names below are the chart contract, so
# regenerate this fixture when the pinned chart changes.
WORKSPACE_RENDERED = """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: openshell-workspace-sandbox-ssh
  namespace: openclaw-workspace-template
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: openshell-sandbox
  namespace: openclaw-workspace-template
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: openshell-workspace-sandbox
  namespace: openclaw-workspace-template
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: openshell-workspace-sandbox
  namespace: openclaw-workspace-template
"""


class PrepareInputsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.inputs = self.directory / "inputs"
        self.inputs.mkdir()
        commands = self.directory / "commands"
        commands.mkdir()
        self.log = self.directory / "oc.jsonl"
        oc = commands / "oc"
        oc.write_text(OC_STUB)
        oc.chmod(0o700)
        self.env = dict(
            os.environ,
            PATH=str(commands) + os.pathsep + os.environ["PATH"],
            OCE_CHECKOUT=str(REPOSITORY),
            OCC_INPUT_DIRECTORY=str(self.inputs),
            OC_TEST_LOG=str(self.log),
            OCC_CONSOLE_HOST="console.apps.example.test",
            OCC_ADMIN_EMAIL="admin@example.test",
            CONTROLLER_IMAGE=CONTROLLER_IMAGE,
            RUNTIME_IMAGE=RUNTIME_IMAGE,
        )

    def run_command(self, *arguments, environment=None):
        return subprocess.run(
            arguments,
            env=environment or self.env,
            text=True,
            capture_output=True,
        )

    def prepare(self):
        result = self.run_command(str(RHOAI / "scripts/prepare-inputs"), "bootstrap")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tag_only_image_refuses_before_discovery(self):
        # A mutable tag must never reach the Helm values or the Installation:
        # the refusal has to fire before any oc discovery runs, so the stub's
        # call log stays empty.
        mutable = "registry.example.test/oce/controller:latest"
        result = self.run_command(
            str(RHOAI / "scripts/prepare-inputs"),
            "bootstrap",
            environment=dict(self.env, CONTROLLER_IMAGE=mutable),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONTROLLER_IMAGE", result.stderr)
        self.assertFalse(
            self.log.exists() and self.log.read_text(),
            "oc discovery ran before the image refusal",
        )

    def merge_fragment(self):
        """Apply the README section 4 merge steps to the bootstrap Installation."""
        rendered = self.inputs / "openshell-workspace-rendered.yaml"
        rendered.write_text(WORKSPACE_RENDERED)
        resources = self.inputs / "openshell-workspace-resources.json"
        result = self.run_command(
            "yq", "eval-all", "-o=json", "-I=0",
            "[.] | del(.[].metadata.namespace)", str(rendered),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        resources.write_text(result.stdout)
        environment = dict(
            self.env,
            OPEN_SHELL_FRAGMENT=str(
                RHOAI / "manifests/openshift/openshell-installation-fragment.yaml"
            ),
            OPEN_SHELL_WORKSPACE_RESOURCES=str(resources),
            OPENSHELL_SERVICE_IP=OPENSHELL_SERVICE_IP,
        )
        for expression in [
            ". *= load(strenv(OPEN_SHELL_FRAGMENT))",
            ".drivers.compute.configuration.network.providerHarness.address = "
            "strenv(OPENSHELL_SERVICE_IP) | "
            ".drivers.sandbox.configuration.gateway.operatorWorkspaceResources = "
            "load(strenv(OPEN_SHELL_WORKSPACE_RESOURCES))",
        ]:
            result = self.run_command(
                "yq", "-i", expression, str(self.inputs / "installation.yaml"),
                environment=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        return resources

    def installation(self):
        result = self.run_command(
            "yq", "-o=json", str(self.inputs / "installation.yaml")
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_merged_installation_selects_default_gateway_profile(self):
        self.prepare()
        self.merge_fragment()
        installation = self.installation()
        self.assertEqual(
            installation["backend"][0]["drivers"], {"sandbox": "openshell-sandbox"}
        )
        # The retired credential-gateway driver must not return. The backend
        # admits only the sandbox driver above, and the merged Installation
        # defines no driver block beyond the production set; the release
        # loader refuses a reintroduced selection, so drift also fails at
        # load time. Assert the exact key set instead of naming the retired
        # term, which must stay out of deploy/rhoai entirely.
        self.assertEqual(
            set(installation["drivers"]),
            {
                "plugin",
                "configuration",
                "iam",
                "compute",
                "secret",
                "sandbox",
                "token_service",
            },
        )
        token_service = installation["drivers"]["token_service"]
        self.assertEqual(token_service["id"], "occ")
        self.assertEqual(token_service["configuration"], {})
        self.assertEqual(
            installation["drivers"]["compute"]["configuration"]["network"]
            ["providerHarness"]["address"],
            OPENSHELL_SERVICE_IP,
        )
        self.assertEqual(
            installation["sandboxNetworkPolicyCeiling"][0]["id"],
            "codex-model-egress",
        )

    def test_merged_installation_admits_source_control_egress_binaries(self):
        self.prepare()
        self.merge_fragment()
        policies = self.installation()["drivers"]["sandbox"]["configuration"][
            "policy"
        ]["networkPolicies"]
        source_control = [
            policy for policy in policies if policy["name"] == "source-control"
        ]
        self.assertEqual(len(source_control), 1)
        # gh and the git HTTP helpers carry the HTTPS Git and GitHub traffic
        # the sandbox admits; gh also resolves API operations against
        # api.github.com. The helpers live under /usr/lib/git-core on
        # Debian-based images and /usr/libexec/git-core on RPM-based runtimes,
        # and both locations are admitted, as the controller's own policy does.
        # Dropping any entry here silently removes egress for Agents.
        self.assertEqual(
            source_control[0]["binaries"],
            [
                {"path": "/usr/bin/git"},
                {"path": "/usr/local/bin/gh"},
                {"path": "/usr/lib/git-core/git-remote-http"},
                {"path": "/usr/lib/git-core/git-remote-https"},
                {"path": "/usr/libexec/git-core/git-remote-http"},
                {"path": "/usr/libexec/git-core/git-remote-https"},
            ],
        )
        self.assertEqual(
            [endpoint["host"] for endpoint in source_control[0]["endpoints"]],
            ["github.com", "api.github.com"],
        )

    def test_merged_installation_delegates_codex_model_egress(self):
        self.prepare()
        self.merge_fragment()
        ceiling = [
            entry for entry in self.installation()["sandboxNetworkPolicyCeiling"]
            if entry["id"] == "codex-model-egress"
        ]
        self.assertEqual(len(ceiling), 1)
        # The delegated ceiling must name the Codex binary the runtime image
        # ships. A version bump that skips this path leaves the per-Agent
        # model-egress addition unmatched and api.openai.com unreachable.
        dockerfile = (REPOSITORY / "deploy/runtime/Dockerfile").read_text()
        version = re.search(
            r"^ENV OPENAI_CODEX_VERSION=(\S+)$", dockerfile, re.MULTILINE
        )
        self.assertIsNotNone(
            version, "deploy/runtime/Dockerfile pins no OPENAI_CODEX_VERSION"
        )
        binary = (
            "/app/node_modules/openclaw/node_modules/.pnpm/"
            f"@openai+codex@{version.group(1)}-linux-x64/node_modules/@openai/codex/"
            "vendor/x86_64-unknown-linux-musl/bin/codex"
        )
        self.assertEqual(
            ceiling[0],
            {
                "id": "codex-model-egress",
                "binary": binary,
                "host": "api.openai.com",
                "ports": [{"from": 443, "to": 443}],
            },
        )
        # The agents guide tells operators to save this exact binary on the Agent.
        guide = (RHOAI / "manifests/agents/README.md").read_text()
        exported = re.search(
            r"^export CODEX_BINARY='([^']+)'$", guide, re.MULTILINE
        )
        self.assertIsNotNone(exported, "agents guide exports no CODEX_BINARY")
        self.assertEqual(exported.group(1), binary)
        # The controller's approved Codex binaries are a separate pin: deployment
        # admission intersects them with the ceiling and refuses anything else,
        # so the ceiling path must be an exact array member, not a substring.
        artifacts = (
            REPOSITORY
            / "apps/controller/src/drivers/sandbox/openshell-codex-artifacts.ts"
        ).read_text()
        block = re.search(
            r"OPEN_SHELL_CODEX_BINARIES = \[(.*?)\] as const", artifacts, re.DOTALL
        )
        self.assertIsNotNone(
            block, "controller pins no OPEN_SHELL_CODEX_BINARIES array"
        )
        approved = re.findall(r'"([^"]+)"', block.group(1))
        # A controller refactor that stops writing quoted paths inline (hoisted
        # constants, template literals) empties this extraction; fail with the
        # file to update rather than a bare membership error.
        self.assertTrue(
            approved,
            "extract no paths from OPEN_SHELL_CODEX_BINARIES; update this "
            "extraction for the current openshell-codex-artifacts.ts",
        )
        self.assertIn(binary, approved)

    def test_merged_installation_carries_no_credential_proxy(self):
        self.prepare()
        self.merge_fragment()
        # The plaintext HTTP token service replaces the credential proxy; a
        # reintroduced driver selection is refused by the release loader.
        for name in ["values", "installation"]:
            self.assertNotIn(
                "credential_proxy", (self.inputs / f"{name}.yaml").read_text()
            )

    def test_workspace_resources_keep_chart_identity_without_namespaces(self):
        self.prepare()
        resources = json.loads(self.merge_fragment().read_text())
        self.assertEqual(
            [
                (resource["apiVersion"], resource["kind"], resource["metadata"]["name"])
                for resource in resources
            ],
            [
                ("networking.k8s.io/v1", "NetworkPolicy",
                 "openshell-workspace-sandbox-ssh"),
                ("v1", "ServiceAccount", "openshell-sandbox"),
                ("rbac.authorization.k8s.io/v1", "Role",
                 "openshell-workspace-sandbox"),
                ("rbac.authorization.k8s.io/v1", "RoleBinding",
                 "openshell-workspace-sandbox"),
            ],
        )
        for resource in resources:
            self.assertNotIn("namespace", resource["metadata"])
        self.assertEqual(
            self.installation()["drivers"]["sandbox"]["configuration"]["gateway"]
            ["operatorWorkspaceResources"],
            resources,
        )

    def test_chart_renders_openshift_dns_peer_selector(self):
        helm = shutil.which("helm")
        if helm is None:
            # A silent skip would drop the null k8s-app guard in CI, where
            # nothing re-checks it; fail there so the lane stays honest.
            if os.environ.get("CI"):
                self.fail("helm is not on PATH; the chart render guard cannot run")
            self.skipTest("Install helm on PATH to render the chart's DNS peers.")
        self.prepare()
        result = self.run_command(
            helm, "template", "oce",
            str(REPOSITORY / "deploy/helm/openclaw-enterprise"),
            "--namespace", "openclaw-system",
            "-f", str(self.inputs / "values.yaml"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = self.directory / "rendered.yaml"
        rendered.write_text(result.stdout)
        result = self.run_command(
            "yq", "eval-all", "-o=json", "-I=0",
            '[select(.kind == "NetworkPolicy")]', str(rendered),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        selectors = [
            peer["podSelector"]["matchLabels"]
            for policy in json.loads(result.stdout)
            for rule in policy.get("spec", {}).get("egress") or []
            for peer in rule.get("to") or []
            if (peer.get("namespaceSelector") or {}).get("matchLabels", {}).get(
                "kubernetes.io/metadata.name"
            ) == "openshift-dns"
        ]
        self.assertTrue(selectors, "chart rendered no OpenShift DNS egress peers")
        # prepare-inputs deletes the chart's inherited k8s-app: kube-dns via the
        # null podLabels idiom; without it matchLabels ANDs in kube-dns and the
        # peers select no OpenShift DNS Pod.
        for selector in selectors:
            self.assertEqual(
                selector.get("dns.operator.openshift.io/daemonset-dns"), "default"
            )
            self.assertNotIn("k8s-app", selector)

    def test_image_pins_overlay_matches_gateway_values(self):
        # The dashboard and mTLS procedures pass openshell-image-pins.yaml over
        # a saved gateway values snapshot; the overlay must carry exactly the
        # three reviewed image references and nothing else, or it would pin an
        # image from the wrong repository or an unreviewed component. yq parses
        # the YAML: the documented workstation tools include no Python YAML
        # module.
        def images(path):
            rendered = subprocess.run(
                ["yq", "-o=json", ".", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            return json.loads(rendered.stdout)

        values = images(RHOAI / "manifests/openshift/openshell-gateway-values.yaml")
        pins = images(RHOAI / "manifests/openshift/openshell-image-pins.yaml")
        self.assertEqual(sorted(pins), ["gateway", "sandboxRuntime", "supervisor"])
        for component in ("gateway", "sandboxRuntime", "supervisor"):
            self.assertEqual(values[component]["image"], pins[component]["image"])


if __name__ == "__main__":
    unittest.main()
