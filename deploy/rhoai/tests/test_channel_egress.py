"""Offline channel-egress input preparation checks; live Envoy proof is separate."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPOSITORY = Path(__file__).resolve().parents[1]


class ChannelEgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "values.yaml").write_text(
            "channelEgress:\n  enabled: false\n  excludedCidrs: []\n"
            "controlPlane:\n  installationChecksum: \"\"\n"
        )
        (self.root / "installation.yaml").write_text(
            "occ:\n  cluster: test-cluster\ndrivers:\n"
            "  compute:\n    configuration:\n      runtime: {}\n"
        )
        self.env = {
            **os.environ,
            "OCC_INPUT_DIRECTORY": str(self.root),
            "OCE_CHECKOUT": str(REPOSITORY.parents[1]),
        }

    def use_fake_oc(self):
        commands = self.root / "commands"
        commands.mkdir(exist_ok=True)
        oc = commands / "oc"
        oc.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            "if 'network.config.openshift.io' in args:\n"
            "    print(json.dumps({'spec': {'clusterNetwork': [{'cidr': '10.128.0.0/14'}],"
            " 'serviceNetwork': ['172.30.0.0/16']}}))\n"
            "elif 'nodes' in args:\n"
            "    print(json.dumps({'items': [{'status': {'addresses': ["
            "{'type': 'InternalIP', 'address': '10.0.1.5'},"
            " {'type': 'ExternalIP', 'address': '203.0.113.7'},"
            " {'type': 'Hostname', 'address': 'node-a'}]}}]}))\n"
            "elif 'deployment' in args:\n"
            "    print('1')\n"
        )
        oc.chmod(0o700)
        self.env["PATH"] = str(commands) + os.pathsep + self.env["PATH"]

    def run_script(self):
        return subprocess.run(
            [str(REPOSITORY / "scripts" / "prepare-channel-egress")],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def test_writes_matching_helm_and_installation_inputs(self):
        self.use_fake_oc()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        values = subprocess.run(
            ["yq", "-o", "json", str(self.root / "values.yaml")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        document = json.loads(values)
        self.assertTrue(document["channelEgress"]["enabled"])
        self.assertEqual(
            document["channelEgress"]["excludedCidrs"],
            ["10.128.0.0/14", "172.30.0.0/16", "10.0.1.5/32", "203.0.113.7/32"],
        )
        self.assertNotEqual(document["controlPlane"]["installationChecksum"], "")
        installation = subprocess.run(
            ["yq", "-o", "json", str(self.root / "installation.yaml")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        egress = json.loads(installation)["drivers"]["compute"]["configuration"][
            "runtime"
        ]["channelEgress"]
        self.assertEqual(egress["gatewayName"], "openclaw-channel-egress")
        self.assertEqual(egress["gatewayNamespace"], "openclaw-system")
        self.assertEqual(egress["envoyNamespace"], "envoy-gateway-system")
        self.assertEqual(egress["serviceName"], "openclaw-channel-egress")
        self.assertEqual(egress["listenerPortStart"], 10001)
        self.assertEqual(egress["listenerPortEnd"], 10062)
        self.assertEqual(egress["occListenerPort"], 10000)
        self.assertEqual(egress["inspectionPort"], 19004)
        self.assertEqual(
            egress["excludedCidrs"],
            ["10.128.0.0/14", "172.30.0.0/16", "10.0.1.5/32", "203.0.113.7/32"],
        )

    def test_refuses_retired_slack_proxy_settings(self):
        (self.root / "installation.yaml").write_text(
            "drivers:\n  compute:\n    configuration:\n      runtime:\n"
            "        channels:\n          proxyUrl: http://proxy:3128\n"
        )
        self.use_fake_oc()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Retired Slack proxy settings", result.stderr)

    def test_refuses_releases_without_the_fleet(self):
        checkout = self.root / "checkout"
        (checkout / "deploy/helm/openclaw-enterprise/templates").mkdir(parents=True)
        self.env["OCE_CHECKOUT"] = str(checkout)
        self.use_fake_oc()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not include the managed channel egress fleet", result.stderr)


if __name__ == "__main__":
    unittest.main()
