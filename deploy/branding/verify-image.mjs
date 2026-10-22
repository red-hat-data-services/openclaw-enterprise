import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { constants } from "node:fs";
import { join } from "node:path";

// Run inside a normally started API container, without a theme override or mount.
// This exercises the image's actual default and HTTP serving path together.
const directory = "/etc/openclaw/console-theme";
assert.equal(process.env.OCC_CONSOLE_THEME_DIR, directory);
const manifest = JSON.parse(await readFile(join(directory, "theme.json"), "utf8"));
assert.equal(manifest.displayName, "RedClaw");
// The image's unprivileged API must not be able to replace operator-owned assets.
assert.notEqual(process.getuid(), 0);
await assert.rejects(
  access(directory, constants.W_OK),
  (error) => error.code === "EACCES" || error.code === "EROFS",
);
const origin = `http://127.0.0.1:${process.env.OCC_PORT ?? "3000"}`;
const shell = await fetch(`${origin}/console/login`);
assert.equal(shell.status, 200);
const html = await shell.text();
assert.ok(html.includes(`<title>${manifest.displayName}</title>`));
assert.equal(shell.headers.get("cache-control"), "no-store");

const css = await fetch(`${origin}/console/theme/colors.css`);
assert.equal(css.status, 200);
assert.ok((await css.text()).includes(`--color-accent: ${manifest.colors.accent}`));

for (const filename of [manifest.logo, manifest.favicon, manifest.appleTouchIcon]) {
  assert.ok(html.includes(`/console/theme/${filename}`));
  const asset = await fetch(`${origin}/console/theme/${filename}`);
  assert.equal(asset.status, 200);
  assert.equal(asset.headers.get("content-type"), "image/png");
  assert.deepEqual(
    Buffer.from(await asset.arrayBuffer()),
    await readFile(join(directory, filename)),
  );
}

// Bundling public artwork must not expose its configuration or alter API errors.
const manifestResponse = await fetch(`${origin}/console/theme/theme.json`);
assert.equal(manifestResponse.status, 404);
const missing = await fetch(`${origin}/api/does-not-exist`);
assert.equal(missing.status, 404);
assert.equal((await missing.json()).error.code, "NOT_FOUND");
console.log("Controller image default theme: HTTP and asset checks passed.");
