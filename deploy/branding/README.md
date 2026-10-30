# Default console theme in this fork

Build the controller from this checkout to include the RedClaw console theme:

```sh
docker build --build-arg NODE_BASE_IMAGE="$NODE_BASE_IMAGE" \
  --target runtime --tag "$CONTROLLER_IMAGE" .
```

Select an approved immutable Node 24 base image as described in the
[image build guide](../../docs/release/containers.md). The same default is included
in `--target development`, which the development Compose stack and CLI build.
Deploy the resulting controller image using the existing
[deployment guides](../../docs/guides/deploy.md). Rebuild and roll existing API
containers to pick up bundle changes; previously built or upstream images do
not acquire this theme automatically.

## Files and selection

`redclaw/` contains `theme.json`, `logo.png`, `favicon.png`, and `touch.png`.
The logo is transparent outside its circular badge. The manifest uses the
[Red Hat brand palette](https://www.redhat.com/en/about/brand/standards/color).
All four files are public assets and must remain free of credentials.

The root [Dockerfile](../../Dockerfile) copies the directory into both final
controller stages at `/etc/openclaw/console-theme` and sets
`OCC_CONSOLE_THEME_DIR` to that absolute path. The existing API startup loader
validates it once before listening. Files are root-owned and readable by the
image's nonroot user; the theme requires no writable container storage.
Workers and bootstrap commands share the image but do not load the theme.

For a controller started directly on the host, set the directory explicitly:

```sh
OCC_CONSOLE_THEME_DIR="$(pwd)/deploy/branding/redclaw" \
  node apps/controller/src/server.mjs
```

Keep the rest of the selected development configuration in place. The
[theme reference](../../docs/reference/console/theming.md) owns formats, limits,
and validation. The [customization guide](../../docs/guides/deploy/console-theme.md)
explains replacement bundles with Compose and Helm. An explicit replacement
directory or a ConfigMap mounted over the image directory takes precedence.
Restart the API after replacement; invalid replacements stop startup.
To restore this fork's default, remove the override and recreate the API from
the fork image. An empty variable is invalid; removing a container override
restores the image's environment default.

## Carrying upstream updates

Keep edits confined to this directory and the two Dockerfile blocks marked
`Fork branding`. Do not patch the console, theme loader, Compose configuration,
Helm templates, or upstream documentation for branding. Document fork behavior
on this page; lines added to upstream pages conflict with upstream edits during
the downstream sync. Merge upstream normally and preserve both blocks
if the final stages move. Changes to the upstream theme schema or image recipe
need review; a newly introduced controller image target must include the bundle
and its environment default too.

After an update, build both controller targets and start each through the normal
PostgreSQL-backed development stack, with no theme override or mount. Run:

```sh
docker exec -i "$CONTROLLER_CONTAINER" node --input-type=module \
  < deploy/branding/verify-image.mjs
```

The verifier checks the image default, public HTML and CSS, exact image bytes,
private manifest refusal, and unchanged API JSON errors. Also check a deliberate
operator replacement and refusal of an invalid bundle. Before merging image
recipe changes, complete the repository's
[native image verification](../../.agents/skills/local-dev/references/image-version-verification.md).
Local single-architecture checks do not replace that workflow. Publishing images
and updating deployed image references are separate release steps.
