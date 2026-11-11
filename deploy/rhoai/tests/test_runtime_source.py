"""Run with python3 -m unittest discover -s tests; no cluster or registry access."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE = {
    "commit": "a" * 40,
    "sourceArchiveSha256": "b" * 64,
    "buildTimestamp": "2026-01-02T03:04:05Z",
}


class RuntimeSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.checkout = self.directory / "oce"
        self.checkout.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("remote", "add", "origin", str(self.checkout))
        self.runtime = self.checkout / "deploy/runtime"
        self.runtime.mkdir(parents=True)
        self.manifest = self.runtime / "openclaw-source.json"
        # A distinct legacy pin ensures a present manifest takes precedence.
        (self.runtime / "Dockerfile").write_text(
            f'ARG OPENCLAW_COMMIT={"c" * 40}\n'
            f'ENV OPENCLAW_BUILD_TIMESTAMP={SOURCE["buildTimestamp"]}\n'
            f'    echo \'{"d" * 64}  /tmp/openclaw-source.tar.gz\' '
            '| sha256sum --check --strict\n'
        )
        self.manifest.write_text(json.dumps(SOURCE))
        self.inputs = self.directory / "inputs"
        self.inputs.mkdir()
        commands = self.directory / "commands"
        commands.mkdir()
        self.log = self.directory / "oc.jsonl"
        oc = commands / "oc"
        oc.write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\n"
            "with open(os.environ['OC_TEST_LOG'], 'a') as f:\n"
            "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1] == 'process': print('{}')\n"
            "elif 'apply' in sys.argv: sys.stdin.read()\n"
        )
        oc.chmod(0o700)
        self.env = dict(
            os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
            OCE_CHECKOUT=str(self.checkout), OCC_INPUT_DIRECTORY=str(self.inputs),
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

    def resolve(self, revision):
        return subprocess.run(
            [
                str(REPOSITORY / "scripts/resolve-runtime-source"),
                str(self.checkout),
                revision,
            ],
            text=True, capture_output=True,
        )

    def test_manifest_comes_from_frozen_revision(self):
        revision = self.commit()
        self.manifest.write_text("{}")
        result = self.resolve(revision)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "oceCommit": revision, "openclawCommit": SOURCE["commit"],
            "sourceArchiveSha256": SOURCE["sourceArchiveSha256"],
            "openclawBuildTimestamp": SOURCE["buildTimestamp"],
        })

    def test_legacy_revision_without_manifest(self):
        self.manifest.unlink()
        result = self.resolve(self.commit())
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["openclawCommit"], "c" * 40)
        self.assertEqual(record["sourceArchiveSha256"], "d" * 64)
        self.assertEqual(record["openclawBuildTimestamp"], SOURCE["buildTimestamp"])

    def test_build_passes_metadata_and_preserves_component_selection(self):
        revision = self.commit()
        for component, expected in [("both", ["oce-controller", "oce-runtime"]),
                                    ("controller", ["oce-controller"]),
                                    ("runtime", ["oce-runtime"])]:
            with self.subTest(component=component):
                self.log.unlink(missing_ok=True)
                result = subprocess.run(
                    [str(REPOSITORY / "scripts/build-images"), component],
                    env=dict(self.env, OCC_REV=revision),
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = [json.loads(line) for line in self.log.read_text().splitlines()]
                process = next(call for call in calls if call[0] == "process")
                for parameter in [
                    f'REV={revision}', f'RUNTIME_TAG={revision}-{SOURCE["commit"]}',
                    f'OPENCLAW_COMMIT={SOURCE["commit"]}',
                    f'OPENCLAW_SOURCE_SHA256={SOURCE["sourceArchiveSha256"]}',
                    f'OPENCLAW_BUILD_TIMESTAMP={SOURCE["buildTimestamp"]}',
                ]:
                    self.assertIn(parameter, process)
                self.assertEqual([c[3] for c in calls if "start-build" in c], expected)
                self.assertEqual(
                    json.loads((self.inputs / "runtime-source.json").read_text()),
                    json.loads(self.resolve(revision).stdout),
                )

    def test_invalid_manifest_never_falls_back_or_calls_cluster(self):
        for source in ["{", "{}", json.dumps({**SOURCE, "commit": "main"}),
                       json.dumps({**SOURCE, "sourceArchiveSha256": ""}),
                       json.dumps({
                           **SOURCE,
                           "buildTimestamp": "2026-02-30T00:00:00Z",
                       })]:
            with self.subTest(source=source):
                self.manifest.write_text(source)
                revision = self.commit()
                result = subprocess.run(
                    [str(REPOSITORY / "scripts/build-images")],
                    env=dict(self.env, OCC_REV=revision),
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Cannot resolve runtime source", result.stderr)
                self.assertFalse(self.log.exists())
                self.assertFalse((self.inputs / "runtime-source.json").exists())

    def test_published_runtime_checks_manifest_and_legacy_pins(self):
        # Exercise the resolver's digest checks using local registry response bytes.
        def document(value):
            text = json.dumps(value)
            digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
            return text, digest

        for legacy in [False, True]:
            if legacy:
                self.manifest.unlink()
            revision = self.commit()
            self.git("update-ref", "refs/remotes/origin/main", revision)
            pin = "c" * 40 if legacy else SOURCE["commit"]
            for image_commit in [pin, "e" * 40]:
                with self.subTest(legacy=legacy, matches=image_commit == pin):
                    config, config_digest = document({
                        "os": "linux", "architecture": "amd64",
                        "config": {
                            "Labels": {"org.openclaw.image.revision": image_commit},
                        },
                    })
                    manifest, manifest_digest = document(
                        {"config": {"digest": config_digest}}
                    )
                    index, _ = document({"manifests": [{
                        "platform": {"os": "linux", "architecture": "amd64"},
                        "digest": manifest_digest,
                    }]})
                    responses = {
                        "latest": index,
                        manifest_digest: manifest,
                        config_digest: config,
                    }
                    fixture = self.directory / "registry.json"
                    fixture.write_text(json.dumps(responses))
                    curl = self.directory / "commands/curl"
                    curl.write_text(
                        "#!/usr/bin/env python3\nimport json, os, pathlib, sys\n"
                        "responses = json.loads(pathlib.Path("
                        "os.environ['REGISTRY_TEST_FILE']).read_text())\n"
                        "pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text(\n"
                        "    responses[sys.argv[-1].rsplit('/', 1)[1]])\n"
                    )
                    curl.chmod(0o700)
                    receipt = self.inputs / "published-images.json"
                    receipt.unlink(missing_ok=True)
                    result = subprocess.run(
                        [str(REPOSITORY / "scripts/resolve-images"), "runtime"],
                        env=dict(
                            self.env,
                            OCC_REV=revision,
                            REGISTRY_TEST_FILE=str(fixture),
                        ),
                        text=True, capture_output=True,
                    )
                    if image_commit == pin:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(
                            json.loads(receipt.read_text())["runtime"]["openclawCommit"],
                            pin,
                        )
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("Select matching tags", result.stderr)
                        self.assertFalse(receipt.exists())


if __name__ == "__main__":
    unittest.main()
