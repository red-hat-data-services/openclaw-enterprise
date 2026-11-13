#!/usr/bin/env python3
"""Real Keycloak integration for deployment helpers; requires disposable HTTPS Keycloak.

KEYCLOAK_TEST_URL, KEYCLOAK_TEST_ADMIN_PASSWORD and SSL_CERT_FILE are explicit.
Only oc Secret/service lookup is substituted; all realm/user/mapper/token operations
use the real Keycloak API. This does not prove an OpenShift rollout or Git trailers.
"""

import base64
import hashlib
import html
import http.cookiejar
import re
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

scripts = Path(__file__).resolve().parents[1] / "scripts"
base = os.environ["KEYCLOAK_TEST_URL"].rstrip("/")
password = os.environ["KEYCLOAK_TEST_ADMIN_PASSWORD"]
realm = "credit-proof-" + secrets.token_hex(6)
with urllib.request.urlopen(
    urllib.request.Request(
        base + "/realms/master/protocol/openid-connect/token",
        data=urllib.parse.urlencode(
            {
                "client_id": "admin-cli",
                "username": "admin",
                "password": password,
                "grant_type": "password",
            }
        ).encode(),
    )
) as response:
    token = json.load(response)["access_token"]


def api(path, data=None, method=None):
    req = urllib.request.Request(
        base + "/admin/realms" + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    with urllib.request.urlopen(req) as response:
        return (
            json.load(response)
            if response.status != 204 and response.headers.get("Content-Length") != "0"
            else None
        )


try:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        commands = root / "commands"
        commands.mkdir()
        # The deployment normally obtains this bootstrap credential from a Secret.
        # Substitute only that lookup; no Keycloak behavior is mocked.
        (commands / "oc").write_text(
            "#!/usr/bin/env python3\nimport json, os, sys, base64\n"
            'print(json.dumps({"data": {"username": base64.b64encode(b"admin").decode(), '
            '"password": base64.b64encode(os.environ["KEYCLOAK_TEST_ADMIN_PASSWORD"].encode()).decode()}} '
            'if "secret" in sys.argv else {"spec": {"clusterIP": "127.0.0.1"}}))\n'
        )
        (commands / "oc").chmod(0o700)
        (root / "users.json").write_text(json.dumps(["alice", "bob", "carol"]))
        (root / "admins.json").write_text('["alice"]')
        (root / "current.json").write_text(
            json.dumps({"values": {"gateway": {"auth": {"trustedProxy": {}}}}})
        )
        env = {
            **os.environ,
            "PATH": str(commands) + os.pathsep + os.environ["PATH"],
            "TEAM_LOGIN_DIRECTORY": directory,
            "TEAM_USERS_FILE": str(root / "users.json"),
            "TEAM_ADMIN_USERS_FILE": str(root / "admins.json"),
            "CONFIGURATION_FILE": str(root / "current.json"),
            "TEAM_CREDIT_USERS_FILE": str(root / "credit.json"),
            "KUBECONFIG": str(root / "kubeconfig"),
            "KEYCLOAK_URL": base,
            "KEYCLOAK_REALM": realm,
            "TEAM_CLAW_HOST": "claw.example.test",
            "GATEWAY_NAMESPACE": "agent-proof",
            "GATEWAY_SERVICE": "gateway-proof",
            "AGENT_ID": "agent-proof",
        }

        def run(script):
            result = subprocess.run(
                [sys.executable, str(scripts / script)],
                env=env,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise RuntimeError(script + " failed: " + result.stderr)

        run("create-team-login-realm")
        run("prepare-team-login")
        run("configure-team-login-scopes")
        users = {u["username"]: u for u in api("/" + realm + "/users?max=-1")}
        # Complete the first-login password change for synthetic accounts before
        # checking that credit configuration preserves working browser credentials.
        login_passwords = {name: secrets.token_urlsafe(24) for name in users}
        for name, user in users.items():
            api(
                "/" + realm + "/users/" + user["id"] + "/reset-password",
                {
                    "type": "password",
                    "value": login_passwords[name],
                    "temporary": False,
                },
                "PUT",
            )
            user["requiredActions"] = []
            api("/" + realm + "/users/" + user["id"], user, "PUT")
        credentials = {
            name: api("/" + realm + "/users/" + u["id"] + "/credentials")
            for name, u in users.items()
        }
        client = api("/" + realm + "/clients?clientId=team-claw-browser")[0]
        client_path = "/" + realm + "/clients/" + client["id"]

        def claims(name):
            return api(
                client_path
                + "/evaluate-scopes/generate-example-id-token?"
                + urllib.parse.urlencode(
                    {"userId": users[name]["id"], "scope": "openid"}
                )
            )

        def sign_in(name):
            callback = "https://claw.example.test/oauth2/callback"

            class CaptureCallback(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    if newurl.startswith(callback):
                        return None
                    return super().redirect_request(req, fp, code, msg, headers, newurl)

            browser = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                CaptureCallback(),
            )
            verifier = secrets.token_urlsafe(48)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            endpoint = base + "/realms/" + realm + "/protocol/openid-connect"
            query = urllib.parse.urlencode(
                {
                    "client_id": "team-claw-browser",
                    "redirect_uri": callback,
                    "response_type": "code",
                    "scope": "openid",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
            with browser.open(endpoint + "/auth?" + query) as response:
                page = response.read().decode()
            action = html.unescape(
                re.search(r'<form[^>]+action="([^"]+)"', page).group(1)
            )
            try:
                browser.open(
                    urllib.request.Request(
                        action,
                        data=urllib.parse.urlencode(
                            {
                                "username": name,
                                "password": login_passwords[name],
                                "credentialId": "",
                            }
                        ).encode(),
                    )
                )
            except urllib.error.HTTPError as response:
                if response.code != 302:
                    raise
                code = urllib.parse.parse_qs(
                    urllib.parse.urlparse(response.headers["Location"]).query
                )["code"][0]
            else:
                raise AssertionError(
                    "Expected the authenticated authorization-code callback"
                )
            with browser.open(
                urllib.request.Request(
                    endpoint + "/token",
                    data=urllib.parse.urlencode(
                        {
                            "grant_type": "authorization_code",
                            "client_id": "team-claw-browser",
                            "client_secret": (root / "client-secret").read_text(),
                            "redirect_uri": callback,
                            "code": code,
                            "code_verifier": verifier,
                        }
                    ).encode(),
                )
            ) as response:
                tokens = json.load(response)
            # Userinfo accepts the issued access token; no unsigned fixture token
            # or direct-grant client replaces the supported code+PKCE login.
            with browser.open(
                urllib.request.Request(
                    endpoint + "/userinfo",
                    headers={"Authorization": "Bearer " + tokens["access_token"]},
                )
            ) as response:
                return json.load(response)

        before = {name: sign_in(name) for name in users}
        entries = {
            "alice": {
                "name": "Alice Example",
                "email": "alice@example.test",
                "emailVerified": True,
            },
            "bob": {
                "name": "Bob Example",
                "email": "bob@example.test",
                "emailVerified": False,
            },
        }
        (root / "credit.json").write_text(json.dumps(entries))
        # A competing claim mapper is an operator error. Refuse it before any
        # user/profile writes, rather than silently replacing another owner.
        profile_before = api("/" + realm + "/users/profile")
        api(
            client_path + "/protocol-mappers/models",
            {
                "name": "competing-credit-mapper",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-usermodel-property-mapper",
                "config": {
                    "user.attribute": "username",
                    "claim.name": "oce_git_coauthor_email",
                    "jsonType.label": "String",
                    "id.token.claim": "true",
                },
            },
            "POST",
        )
        rejected = subprocess.run(
            [sys.executable, str(scripts / "configure-team-login-credit")],
            env=env,
            capture_output=True,
            text=True,
        )
        assert (
            rejected.returncode != 0 and "Conflicting credit mapper" in rejected.stderr
        )
        assert api("/" + realm + "/users/profile") == profile_before
        assert not list(root.glob("credit-claims-before-*-private.json"))
        conflict = next(
            m
            for m in api(client_path + "/protocol-mappers/models")
            if m["name"] == "competing-credit-mapper"
        )
        api(client_path + "/protocol-mappers/models/" + conflict["id"], method="DELETE")
        run("configure-team-login-credit")
        snapshots = list(root.glob("credit-claims-before-*-private.json"))
        assert len(snapshots) == 1
        backup = snapshots[0].read_bytes()
        for name, entry in entries.items():
            actual = sign_in(name)
            assert actual["oce_git_coauthor_name"] == entry["name"]
            assert actual["oce_git_coauthor_email"] == entry["email"]
            assert (
                actual["oce_git_coauthor_email_verified"]
                == str(entry["emailVerified"]).lower()
            )
        assert not any(k.startswith("oce_git_coauthor_") for k in sign_in("carol"))
        # Reconfiguration updates existing users and mappers without replacing
        # identities, credentials, group membership, grants or the recovery snapshot.
        entries["alice"]["email"] = "alice-updated@example.test"
        entries["alice"]["emailVerified"] = False
        (root / "credit.json").write_text(json.dumps(entries))
        run("configure-team-login-credit")
        assert claims("alice")["oce_git_coauthor_email"] == "alice-updated@example.test"
        assert claims("alice")["oce_git_coauthor_email_verified"] == "false"
        assert snapshots[0].read_bytes() == backup
        mappers = api(client_path + "/protocol-mappers/models")
        assert (
            len([m for m in mappers if m["name"].startswith("oce_git_coauthor_")]) == 3
        )
        for name, user in users.items():
            current = api("/" + realm + "/users/" + user["id"])
            for field in [
                "id",
                "username",
                "email",
                "emailVerified",
                "requiredActions",
                "enabled",
            ]:
                assert current.get(field) == user.get(field), field
            assert (
                api("/" + realm + "/users/" + user["id"] + "/credentials")
                == credentials[name]
            )
            actual = sign_in(name)
            for claim in ["sub", "preferred_username", "groups", "oce_gateway_scopes"]:
                assert actual.get(claim) == before[name].get(claim), claim
        configuration = json.loads((root / "configuration-keycloak.json").read_text())
        assert (
            "gitCoauthor"
            not in configuration["values"]["gateway"]["auth"]["trustedProxy"]
        )
        print(
            "PASS: real Keycloak code+PKCE/userinfo, verified/unverified/absent credit, conflict rejection, repeat updates, preserved identity/scopes/credentials, recovery snapshot, and runtime configuration"
        )
finally:
    # Delete only the uniquely named realm created by this test.
    if any(r["realm"] == realm for r in api("")):
        api("/" + realm, method="DELETE")
