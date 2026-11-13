"""Offline proxy-rendering and scope-input checks; live Keycloak proof is separate."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest

REPOSITORY = Path(__file__).resolve().parents[1]


class TeamLoginTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        issuer = "https://keycloak.example.test/realms/team-claw"
        prefix = "oidc:" + hashlib.sha256(issuer.encode()).hexdigest() + ":"
        self.identity_prefix = prefix
        self.identities = {name: prefix + name for name in ["owner", "member"]}
        settings = {
            "issuer": issuer,
            "host": "claw.example.test",
            "url": "https://claw.example.test",
            "namespace": "agent-test",
            "gatewayService": "gateway-test",
            "agentId": "agent-test",
            "identityPrefix": prefix,
            "identities": self.identities,
            "administrators": ["owner"],
        }
        (self.root / "settings.json").write_text(json.dumps(settings))
        (self.root / "client-secret").write_text(secrets.token_urlsafe(32))
        self.configuration = self.root / "current.json"
        self.configuration.write_text(
            json.dumps(
                {
                    "values": {
                        "gateway": {
                            "auth": {
                                "trustedProxy": {
                                    "allowUsers": list(self.identities.values()),
                                    "deviceAutoApprove": {
                                        "scopes": ["operator.admin", "operator.read"]
                                    },
                                },
                                "identityScopes": {},
                            },
                            "controlUi": {"allowedOrigins": []},
                        }
                    }
                }
            )
        )
        self.env = {
            **os.environ,
            "TEAM_LOGIN_DIRECTORY": str(self.root),
            "CONFIGURATION_FILE": str(self.configuration),
            "KEYCLOAK_URL": "https://keycloak.example.test",
            "KEYCLOAK_REALM": "team-claw",
            "KUBECONFIG": str(self.root / "kubeconfig"),
        }

    def run_script(self, name, *args):
        return subprocess.run(
            [sys.executable, str(REPOSITORY / "scripts" / name), *args],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def use_fake_oc(self):
        commands = self.root / "commands"
        commands.mkdir(exist_ok=True)
        oc = commands / "oc"
        oc.write_text(
            '#!/usr/bin/env python3\nprint(\'{"spec":{"clusterIP":"10.0.0.1"}}\')\n'
        )
        oc.chmod(0o700)
        self.env["PATH"] = str(commands) + os.pathsep + self.env["PATH"]

    def test_renderer_replaces_scope_header_with_signed_claim(self):
        self.use_fake_oc()
        result = self.run_script("prepare-team-login")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.root / "proxy-manifests-private.json").read_text())
        secret = next(item for item in manifest["items"] if item["kind"] == "Secret")
        alpha = json.loads(secret["stringData"]["alpha.json"])
        headers = [
            h
            for h in alpha["injectRequestHeaders"]
            if h["name"].lower() == "x-openclaw-scopes"
        ]
        self.assertEqual(
            headers,
            [
                {
                    "name": "X-Openclaw-Scopes",
                    "preserveRequestValue": False,
                    "values": [{"claimSource": {"claim": "oce_gateway_scopes"}}],
                }
            ],
        )
        self.assertIn("oce_gateway_scopes", alpha["providers"][0]["additionalClaims"])
        # Staging claims must preserve login semantics and remain acceptable to
        # the pinned runtime, which does not yet recognize gitCoauthor.
        provider = alpha["providers"][0]
        self.assertEqual(provider["oidcConfig"]["emailClaim"], "preferred_username")
        self.assertEqual(provider["oidcConfig"]["userIDClaim"], "sub")
        credit = {
            "X-Coauthor-Name": "oce_git_coauthor_name",
            "X-Coauthor-Email": "oce_git_coauthor_email",
            "X-Coauthor-Email-Verified": "oce_git_coauthor_email_verified",
        }
        for header, claim in credit.items():
            self.assertIn(claim, provider["additionalClaims"])
            self.assertIn(
                {
                    "name": header,
                    "preserveRequestValue": False,
                    "values": [{"claimSource": {"claim": claim}}],
                },
                alpha["injectRequestHeaders"],
            )
        # oauth2-proxy stores userIDClaim (sub) as the session user claim; a
        # claimSource for "sub" resolves only through additionalClaims.
        identity = [
            h
            for h in alpha["injectRequestHeaders"]
            if h["name"].lower() == "x-occ-identity"
        ]
        self.assertEqual(
            identity,
            [
                {
                    "name": "X-Occ-Identity",
                    "preserveRequestValue": False,
                    "values": [
                        {
                            "claimSource": {
                                "claim": "user",
                                "prefix": self.identity_prefix,
                            }
                        }
                    ],
                }
            ],
        )
        # One login per Agent: the name carries an Agent-derived suffix.
        expected_name = "oce-team-claw-oidc-" + hashlib.sha256(
            b"agent-test"
        ).hexdigest()[:8]
        self.assertEqual(secret["metadata"]["name"], expected_name)
        configuration = json.loads(
            (self.root / "configuration-keycloak.json").read_text()
        )
        gateway = configuration["values"]["gateway"]
        self.assertNotIn("roles", gateway)
        self.assertNotIn("gitCoauthor", gateway["auth"]["trustedProxy"])
        self.assertNotIn(
            "operator.admin",
            gateway["auth"]["trustedProxy"]["deviceAutoApprove"]["scopes"],
        )
        grants = gateway["auth"]["identityScopes"]
        self.assertEqual(
            grants[self.identities["member"]], ["operator.read", "operator.write"]
        )
        self.assertIn("operator.admin", grants[self.identities["owner"]])
        deployment = next(
            item for item in manifest["items"] if item["kind"] == "Deployment"
        )
        checksum = deployment["spec"]["template"]["metadata"]["annotations"]
        settings = json.loads((self.root / "settings.json").read_text())
        settings["clientId"] = "another-agent-client"
        (self.root / "settings.json").write_text(json.dumps(settings))
        result = self.run_script("prepare-team-login")
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = json.loads((self.root / "proxy-manifests-private.json").read_text())
        deployment = next(
            item for item in updated["items"] if item["kind"] == "Deployment"
        )
        self.assertNotEqual(
            checksum, deployment["spec"]["template"]["metadata"]["annotations"]
        )

    def test_login_origin_added_when_none_are_configured(self):
        self.use_fake_oc()
        configuration = json.loads(self.configuration.read_text())
        del configuration["values"]["gateway"]["controlUi"]["allowedOrigins"]
        self.configuration.write_text(json.dumps(configuration))
        result = self.run_script("prepare-team-login")
        self.assertEqual(result.returncode, 0, result.stderr)
        written = json.loads(
            (self.root / "configuration-keycloak.json").read_text()
        )
        self.assertEqual(
            written["values"]["gateway"]["controlUi"]["allowedOrigins"],
            ["https://claw.example.test"],
        )

    def test_invalid_credit_entries_refused_before_credentials_are_read(self):
        path = self.root / "credit-users.json"
        self.env["TEAM_CREDIT_USERS_FILE"] = str(path)
        for entry in [
            {"name": "Member", "email": "member@example.test", "emailVerified": "true"},
            {
                "name": "Member\r\nInjected",
                "email": "member@example.test",
                "emailVerified": True,
            },
            {
                "name": "Member",
                "email": "member@example.test>\n",
                "emailVerified": True,
            },
        ]:
            with self.subTest(entry=entry):
                path.write_text(json.dumps({"member": entry}))
                result = self.run_script("configure-team-login-credit")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("FileNotFoundError", result.stderr)
                self.assertRegex(result.stderr, "boolean emailVerified|single-line")

    def test_missing_grant_refused_before_keycloak_access(self):
        result = self.run_script(
            "configure-team-login-scopes", "--configuration", str(self.configuration)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Every human needs an explicit grant", result.stderr)

    def test_header_injection_refused_before_keycloak_access(self):
        configuration = json.loads(self.configuration.read_text())
        configuration["values"]["gateway"]["auth"]["identityScopes"] = {
            identity: ["operator.read\r\nX-Injected: yes"]
            for identity in self.identities.values()
        }
        self.configuration.write_text(json.dumps(configuration))
        result = self.run_script(
            "configure-team-login-scopes", "--configuration", str(self.configuration)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Supply explicit Gateway operator scopes", result.stderr)

    def test_wrong_issuer_refused_before_credentials_are_read(self):
        configuration = json.loads(self.configuration.read_text())
        configuration["values"]["gateway"]["auth"]["identityScopes"] = {
            identity: ["operator.read"] for identity in self.identities.values()
        }
        self.configuration.write_text(json.dumps(configuration))
        self.env["KEYCLOAK_URL"] = "https://another.example.test"
        result = self.run_script(
            "configure-team-login-scopes", "--configuration", str(self.configuration)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("environment does not match", result.stderr)


if __name__ == "__main__":
    unittest.main()
