"""Run with python3 -m unittest discover -s tests; no cluster or registry access."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_REPOSITORY = REPOSITORY.parents[1]
SCRIPT = REPOSITORY / "scripts/prepare-upgrade-profile"
CONTROLLER_IMAGE = (
    "quay.io/aipcc/base-images/agentic/oce-controller@sha256:" + "a" * 64
)
RUNTIME_IMAGE = "quay.io/aipcc/base-images/agentic/oce-runtime@sha256:" + "b" * 64
KUBECONFIG = """apiVersion: v1
kind: Config
current-context: reviewed
contexts:
- name: reviewed
  context:
    cluster: cluster
    user: user
clusters:
- name: cluster
  cluster:
    server: https://api.example.test:6443
users:
- name: user
  user:
    token: synthetic-not-a-credential
"""


class UpgradeProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.inputs = self.directory / "inputs"
        self.inputs.mkdir()
        self.checkout = self.directory / "oce"
        self.checkout.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.checkout / "scripts").mkdir()
        (self.checkout / "scripts/upgrade-production").write_text(
            "#!/usr/bin/env python3\n"
        )
        self.revision = self.commit()
        self.occ = self.directory / "bin/occ"
        self.occ.parent.mkdir()
        self.occ.write_text("#!/usr/bin/env bash\n")
        self.occ.chmod(0o700)
        commands = self.directory / "commands"
        commands.mkdir()
        self.log = self.directory / "oc.jsonl"
        oc = commands / "oc"
        oc.write_text(
            "#!/usr/bin/env bash\n"
            'printf "%s\\n" "$*" >> "$OC_TEST_LOG"\n'
            "exit 1\n"
        )
        oc.chmod(0o700)
        self.write_inputs()
        self.env = dict(
            os.environ,
            PATH=str(commands) + os.pathsep + os.environ["PATH"],
            OCE_CHECKOUT=str(self.checkout),
            OCC_INPUT_DIRECTORY=str(self.inputs),
            OCC_BIN=str(self.occ),
            OC_TEST_LOG=str(self.log),
        )

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.checkout), *args], text=True
        ).strip()

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.test",
                 "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                 "commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def write_inputs(self):
        (self.inputs / "kubeconfig").write_text(KUBECONFIG)
        (self.inputs / "values.yaml").write_text(
            "repositoryCredentials:\n  enabled: false\n"
        )
        (self.inputs / "installation.yaml").write_text(
            "apiVersion: openclaw.dev/v1\nkind: Installation\n"
        )
        (self.inputs / "images.env").write_text(
            f"export CONTROLLER_IMAGE='{CONTROLLER_IMAGE}'\n"
            f"export RUNTIME_IMAGE='{RUNTIME_IMAGE}'\n"
        )
        (self.inputs / "published-images.json").write_text(
            json.dumps({
                "oceCommit": self.revision,
                "controller": {"image": CONTROLLER_IMAGE},
                "runtime": {"image": RUNTIME_IMAGE},
            })
        )
        (self.inputs / "upgrade-profile.json").unlink(missing_ok=True)

    def prepare(self, cwd=None, **environment):
        return subprocess.run(
            [str(SCRIPT)],
            env=dict(self.env, **environment),
            cwd=cwd,
            text=True,
            capture_output=True,
        )

    def profile(self):
        return json.loads((self.inputs / "upgrade-profile.json").read_text())

    def translator_fields(self, name):
        text = (SOURCE_REPOSITORY / "scripts/upgrade-production").read_text()
        match = re.search(rf"^{name} = \(([^)]*)\)", text, re.MULTILINE)
        self.assertIsNotNone(match, f"missing {name} in scripts/upgrade-production")
        return set(re.findall(r'"([A-Za-z]+)"', match.group(1)))

    def test_profile_fields_and_permissions(self):
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.profile(), {
            "kubeconfig": "kubeconfig",
            "context": "reviewed",
            "namespace": "openclaw-system",
            "release": "oce",
            "installationDirectory": str(self.inputs),
            "sourceRevision": self.revision,
            "occ": str(self.occ),
            "images": {
                "controller": CONTROLLER_IMAGE,
                "runtime": RUNTIME_IMAGE,
            },
        })
        self.assertNotIn("imagePublication", self.profile())
        self.assertNotIn("broker", self.profile()["images"])
        self.assertIn("No cluster access was needed.", result.stdout)
        # Only local files and one git query may be read; no cluster command runs.
        self.assertFalse(self.log.exists())
        mode = stat.S_IMODE(
            (self.inputs / "upgrade-profile.json").stat().st_mode
        )
        self.assertEqual(mode, 0o600)

    def test_profile_keys_match_translator_contract(self):
        # Upstream may rename or add profile fields at the next sync; the
        # rendered profile must keep covering the translator's contract.
        required = self.translator_fields("REQUIRED")
        optional = self.translator_fields("OPTIONAL")
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        keys = set(self.profile())
        self.assertLessEqual(required, keys)
        self.assertLessEqual(keys, required | optional)

    def test_rerun_replaces_stale_profile(self):
        stale = self.inputs / "upgrade-profile.json"
        stale.write_text(json.dumps({"kubeconfig": "stale"}))
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.profile()["context"], "reviewed")
        self.assertEqual(list(self.inputs.glob("upgrade-profile.json.*")), [])

    def test_directory_at_profile_destination_refuses(self):
        # mv would move the temp file inside a same-named directory and exit
        # zero, so refuse before claiming a profile the translator rejects.
        destination = self.inputs / "upgrade-profile.json"
        destination.mkdir()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"Refusing to replace {destination}", result.stderr)
        self.assertIn("not a regular file", result.stderr)
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(self.inputs.glob("upgrade-profile.json.*")), [])

    def test_refuses_inputs_inside_checkouts(self):
        # The guard covers the whole tooling checkout, not just deploy/rhoai,
        # because run evidence defaults to a private directory beside the
        # profile and must not land inside the repository.
        for label, parent in (
            ("deploy-rhoai", REPOSITORY),
            ("tooling-root", REPOSITORY.parents[1]),
            ("release-checkout", self.checkout),
        ):
            with self.subTest(inputs=label):
                nested = self.directory / f"inside-{label}"
                nested.symlink_to(parent, target_is_directory=True)
                result = self.prepare(OCC_INPUT_DIRECTORY=str(nested))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "Keep cluster inputs outside both repositories.", result.stderr
                )
                self.assertFalse((parent / "upgrade-profile.json").exists())
                self.assertEqual(
                    list(parent.glob("upgrade-profile.json.*")), []
                )

    def test_git_work_tree_override_cannot_hide_tooling_root(self):
        # An inherited GIT_WORK_TREE must not redirect the containment lookup
        # away from the real tooling checkout.
        nested = self.directory / "inside-tooling-root-override"
        nested.symlink_to(REPOSITORY.parents[1], target_is_directory=True)
        result = self.prepare(
            OCC_INPUT_DIRECTORY=str(nested), GIT_WORK_TREE=str(self.checkout)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Keep cluster inputs outside both repositories.", result.stderr
        )

    def test_missing_translator_names_prerequisite(self):
        (self.checkout / "scripts/upgrade-production").unlink()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("upgrade CLI", result.stderr)
        self.assertIn("#6504", result.stderr)
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_revision_mismatch_refuses(self):
        (self.checkout / "scripts/upgrade-production").write_text("changed\n")
        head = self.commit()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(self.revision, result.stderr)
        self.assertIn(head, result.stderr)
        self.assertIn("Check out the published revision", result.stderr)
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_repository_credentials_refuse(self):
        # yq keeps the spelling of a YAML boolean, so every spelling Helm
        # honors as enabled must reach the broker refusal.
        for enabled in ("true", "True", "TRUE"):
            with self.subTest(enabled=enabled):
                self.write_inputs()
                (self.inputs / "values.yaml").write_text(
                    f"repositoryCredentials:\n  enabled: {enabled}\n"
                )
                result = self.prepare()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("broker", result.stderr)
                self.assertFalse(
                    (self.inputs / "upgrade-profile.json").exists()
                )

    def test_string_true_repository_credentials_refuse(self):
        # Helm accepts the string "true"; the guard must not let it through
        # just because it is not a YAML boolean.
        (self.inputs / "values.yaml").write_text(
            'repositoryCredentials:\n  enabled: "true"\n'
        )
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broker", result.stderr)
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_string_false_repository_credentials_refuse(self):
        # Helm truthiness enables the broker for the string "false" too, so a
        # quoted value must fail as a type error instead of passing the guard.
        (self.inputs / "values.yaml").write_text(
            'repositoryCredentials:\n  enabled: "false"\n'
        )
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be a YAML boolean", result.stderr)
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_unparseable_values_refuses_with_parse_error(self):
        # A malformed values.yaml is not the same as an enabled block: report
        # the file the operator must regenerate instead of accepting it.
        (self.inputs / "values.yaml").write_text(
            "repositoryCredentials: [true\n"
        )
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot parse", result.stderr)
        self.assertIn("values.yaml", result.stderr)
        self.assertIn("prepare-inputs", result.stderr)
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_values_without_repository_credentials_accepted(self):
        # Older prepare-inputs outputs omit the block entirely. Only an
        # explicit enabled: true needs the broker image this profile cannot
        # supply; an absent or partial block must not block the render.
        cases = {
            "no keys": "{}\n",
            "unrelated keys": "imagePullSecrets: []\n",
            "block without enabled": "repositoryCredentials:\n  variables: []\n",
        }
        for case, text in cases.items():
            with self.subTest(case=case):
                self.write_inputs()
                (self.inputs / "values.yaml").write_text(text)
                result = self.prepare()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((self.inputs / "upgrade-profile.json").exists())

    def test_occ_path_must_be_a_regular_file(self):
        # A directory with the execute bit passes -x but is not the CLI; the
        # profile must not record a directory as its occ binary.
        directory = self.directory / "bin/occ-directory"
        directory.mkdir()
        directory.chmod(0o700)
        result = self.prepare(OCC_BIN=str(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"Missing executable {directory}", result.stderr)
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_missing_default_occ_binary_names_build_step(self):
        # A fresh checkout has no built CLI at the default path, so the
        # script must name the build step instead of failing obscurely.
        default = REPOSITORY / "bin/occ"
        if os.access(default, os.X_OK):
            self.skipTest(f"untracked build artifact {default} exists")
        environment = dict(self.env)
        del environment["OCC_BIN"]
        result = subprocess.run(
            [str(SCRIPT)], env=environment, text=True, capture_output=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"Missing executable {default}", result.stderr)
        self.assertIn("deploy/rhoai/scripts/build-occ", result.stderr)
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_relative_occ_binary_is_absolutized(self):
        # build-occ may be invoked from the checkout root with a relative
        # path; the profile must carry an absolute occ path so the upgrade
        # CLI can run it from any working directory.
        result = self.prepare(cwd=self.directory, OCC_BIN="bin/occ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.profile()["occ"], str(self.occ.resolve()))
        self.assertTrue(os.path.isabs(self.profile()["occ"]))

    def test_malformed_oce_commit_refuses(self):
        # Only a full lowercase object ID identifies the reviewed checkout;
        # a short SHA, uppercase hex, or a tag must not reach the comparison.
        for commit in ("a" * 12, "A" * 40, "refs/tags/v1.2.3"):
            with self.subTest(commit=commit):
                self.write_inputs()
                (self.inputs / "published-images.json").write_text(
                    json.dumps({"oceCommit": commit})
                )
                result = self.prepare()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    f"published-images.json has no full OCE commit: {commit}",
                    result.stderr,
                )
                self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_unreadable_checkout_revision_refuses(self):
        # An exported source tree has the translator but no git metadata, so
        # the reviewed revision cannot be proved and the render must stop.
        shutil.rmtree(self.checkout / ".git")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            f"Cannot read the revision of {self.checkout.resolve()}",
            result.stderr,
        )
        self.assertIn(
            "set OCE_CHECKOUT to the reviewed release checkout", result.stderr
        )
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_runtime_image_requires_digest(self):
        # Both images are consumed by digest; a runtime tag alone is mutable
        # and must be refused even when the controller digest is valid.
        (self.inputs / "images.env").write_text(
            f"export CONTROLLER_IMAGE='{CONTROLLER_IMAGE}'\n"
            "export RUNTIME_IMAGE='quay.io/aipcc/base-images/agentic/"
            "oce-runtime:latest'\n"
        )
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "images.env has no immutable RUNTIME_IMAGE digest", result.stderr
        )
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_partial_images_env_ignores_ambient_image_exports(self):
        # images.env is the only source of both digests. A partial file must
        # not inherit an image exported in the operator's shell, or a stale
        # but valid digest would silently enter the profile.
        (self.inputs / "images.env").write_text(
            f"export CONTROLLER_IMAGE='{CONTROLLER_IMAGE}'\n"
        )
        result = self.prepare(RUNTIME_IMAGE=RUNTIME_IMAGE)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "images.env has no immutable RUNTIME_IMAGE digest", result.stderr
        )
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_images_env_digest_must_match_published_images(self):
        # resolve-images writes images.env and published-images.json together;
        # a hand-edited digest must not enter the profile while the receipt
        # still names the published image.
        stale = "quay.io/aipcc/base-images/agentic/oce-controller@sha256:" + "c" * 64
        (self.inputs / "images.env").write_text(
            f"export CONTROLLER_IMAGE='{stale}'\n"
            f"export RUNTIME_IMAGE='{RUNTIME_IMAGE}'\n"
        )
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(stale, result.stderr)
        self.assertIn(CONTROLLER_IMAGE, result.stderr)
        self.assertIn("resolve-images", result.stderr)
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_partial_published_images_receipt_refuses(self):
        # A component-selected resolve-images run also writes a partial
        # images.env, so a receipt missing a component only comes from hand
        # assembly. Refuse it before a foreign digest reaches the profile.
        (self.inputs / "published-images.json").write_text(
            json.dumps({
                "oceCommit": self.revision,
                "runtime": {"image": RUNTIME_IMAGE},
            })
        )
        controller_other = (
            "quay.io/aipcc/base-images/agentic/oce-controller@sha256:" + "c" * 64
        )
        (self.inputs / "images.env").write_text(
            f"export CONTROLLER_IMAGE='{controller_other}'\n"
            f"export RUNTIME_IMAGE='{RUNTIME_IMAGE}'\n"
        )
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "published-images.json has no controller.image", result.stderr
        )
        self.assertIn("resolve-images", result.stderr)
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def write_installation_images(self, gateway, agent):
        (self.inputs / "installation.yaml").write_text(
            "apiVersion: openclaw.dev/v1\n"
            "kind: Installation\n"
            "drivers:\n"
            "  compute:\n"
            "    configuration:\n"
            "      images:\n"
            f"        gateway: {gateway}\n"
            f"        agent: {agent}\n"
        )

    def test_controller_only_upgrade_omits_unchanged_runtime(self):
        # upgrade-production-images refuses a runtime digest both installed
        # slots already run; omitting it lets a controller-only upgrade proceed
        # and keep the installed Agent revisions.
        self.write_installation_images(RUNTIME_IMAGE, RUNTIME_IMAGE)
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.profile()["images"], {"controller": CONTROLLER_IMAGE})

    def test_changed_runtime_slot_keeps_runtime_image(self):
        # One slot still differs, so the upgrade must deploy the runtime image.
        other = (
            "quay.io/aipcc/base-images/agentic/oce-runtime@sha256:" + "c" * 64
        )
        self.write_installation_images(RUNTIME_IMAGE, other)
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.profile()["images"], {
            "controller": CONTROLLER_IMAGE,
            "runtime": RUNTIME_IMAGE,
        })

    def test_unreadable_installation_slots_keep_runtime_image(self):
        # Fail-safe: without both slots proven equal, keep the runtime image
        # rather than silently skipping a required deployment.
        cases = {
            "absent images": "apiVersion: openclaw.dev/v1\nkind: Installation\n",
            "unparseable": "drivers: [\n",
        }
        for case, text in cases.items():
            with self.subTest(case=case):
                self.write_inputs()
                (self.inputs / "installation.yaml").write_text(text)
                result = self.prepare()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.profile()["images"], {
                    "controller": CONTROLLER_IMAGE,
                    "runtime": RUNTIME_IMAGE,
                })

    def test_unusable_images_env_refuses_as_input_error(self):
        # The script's own fallback promises this message when images.env
        # cannot be sourced. A file that fails to load must not fall through
        # to the digest check, which reports a misleading missing digest.
        cases = {
            "broken": lambda: (self.inputs / "images.env").write_text(
                f"export CONTROLLER_IMAGE='{CONTROLLER_IMAGE}'\n"
                "not-a-command\n"
            ),
            "unreadable": lambda: (self.inputs / "images.env").chmod(0o000),
        }
        if os.geteuid() == 0:
            # root reads mode-000 files, so the unreadable case cannot fail.
            del cases["unreadable"]
        for case, mutate in cases.items():
            with self.subTest(case=case):
                (self.inputs / "images.env").chmod(0o600)
                self.write_inputs()
                mutate()
                result = self.prepare()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Cannot read images.env", result.stderr)
                self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_missing_required_environment_refuses(self):
        # Every other refusal names its prerequisite; these entry-point
        # guards must do the same instead of rendering with empty paths.
        for name, message in {
            "OCC_INPUT_DIRECTORY": "Set an existing private input directory",
            "OCE_CHECKOUT": "Set the OCE source checkout",
        }.items():
            with self.subTest(variable=name):
                self.write_inputs()
                result = self.prepare(**{name: ""})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_missing_input_directory_names_variable(self):
        # A path that does not exist must fail with the variable that needs
        # it, not with bash's own cd error.
        missing = self.directory / "absent-inputs"
        result = self.prepare(OCC_INPUT_DIRECTORY=str(missing))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OCC_INPUT_DIRECTORY", result.stderr)
        self.assertIn(str(missing), result.stderr)
        self.assertIn("prepare-inputs", result.stderr)
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_missing_checkout_names_variable(self):
        # Same for OCE_CHECKOUT; the message must point at the release
        # checkout rather than surfacing a bare cd failure.
        missing = self.directory / "absent-checkout"
        result = self.prepare(OCE_CHECKOUT=str(missing))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OCE_CHECKOUT", result.stderr)
        self.assertIn(str(missing), result.stderr)
        self.assertIn("reviewed release checkout", result.stderr)
        self.assertFalse((self.inputs / "upgrade-profile.json").exists())

    def test_invalid_inputs_write_nothing(self):
        def remove(name):
            return lambda: (self.inputs / name).unlink()

        def rewrite(name, text):
            return lambda: (self.inputs / name).write_text(text)

        cases = {
            "missing kubeconfig": remove("kubeconfig"),
            "missing values.yaml": remove("values.yaml"),
            "missing installation.yaml": remove("installation.yaml"),
            "missing images.env": remove("images.env"),
            "missing published-images.json": remove("published-images.json"),
            "empty current-context": rewrite(
                "kubeconfig",
                KUBECONFIG.replace(
                    "current-context: reviewed", 'current-context: ""'
                ),
            ),
            "unknown context": rewrite(
                "kubeconfig",
                KUBECONFIG.replace(
                    "current-context: reviewed", "current-context: absent"
                ),
            ),
            "two contexts": rewrite(
                "kubeconfig",
                KUBECONFIG.replace(
                    "clusters:",
                    "- name: other\n  context:\n    cluster: cluster\n"
                    "    user: user\nclusters:",
                    1,
                ),
            ),
            "non-digest image": rewrite(
                "images.env",
                "export CONTROLLER_IMAGE='quay.io/example/controller:latest'\n"
                "export RUNTIME_IMAGE='quay.io/example/runtime:latest'\n",
            ),
        }
        for case, mutate in cases.items():
            with self.subTest(case=case):
                self.write_inputs()
                mutate()
                result = self.prepare()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.inputs / "upgrade-profile.json").exists())
                self.assertEqual(
                    list(self.inputs.glob("upgrade-profile.json.*")), []
                )

    def test_existing_inputs_untouched(self):
        names = (
            "kubeconfig", "values.yaml", "installation.yaml", "images.env",
            "published-images.json",
        )
        before = {name: (self.inputs / name).read_bytes() for name in names}
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {name: (self.inputs / name).read_bytes() for name in names}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
