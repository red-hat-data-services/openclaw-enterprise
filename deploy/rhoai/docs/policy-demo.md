# Demo a denied request, approve its policy, and retry

Deploy an OCE controller built from main after
[OCE PR #2599, per-Agent sandbox network policies](https://github.com/openclaw/openclaw-enterprise/pull/2599)
merged (October 19, 2026), either built in-cluster or
[published](../README.md#3-resolve-the-oce-images).
Use dedicated Codex with OpenShell,
a working model credential, and an OCC administrator login. The Agent's Control UI
and OCC Console are separate browser tabs.

## Prepare the Installation once

The Installation delegation ceiling is the operator's limit on what Agent policy
editors may approve. An empty ceiling rejects every addition, even for an
administrator. It grants no network access by itself.

The example ceiling permits four exact executable/hostname pairs, on port 443.
It does not allow other Python versions, other executables or arbitrary Google
subdomains. Check the resolved interpreter inside your image; this trial used
`/usr/bin/python3.11`.

The demo was verified with `/usr/bin/python3.11`. The [published runtime image](../README.md#3-resolve-the-oce-images) has `/usr/bin/python3.14` and no `python3.11`; `curl` and `git` are at the same paths. To use it, copy the ceiling file into your protected setup directory, replace `/usr/bin/python3.11` with `/usr/bin/python3.14` in the copy, set `DEMO_CEILING_FILE` to the copy in the block below, and use `python3.14` in the prompts. That variant has not been verified end to end.

```bash
# REQUIRED: protected inputs for the deployed OCC installation.
export OCC_SETUP_DIRECTORY='/private/path/to/cluster-setup'

cp "$OCC_SETUP_DIRECTORY/installation.yaml" \
  "$OCC_SETUP_DIRECTORY/installation.yaml.before-policy-demo"

# Run from the fork root.
export DEMO_CEILING_FILE="$PWD/deploy/rhoai/manifests/agents/policy-demo-ceiling.json"

yq -i '.sandboxNetworkPolicyCeiling = load(strenv(DEMO_CEILING_FILE))' \
  "$OCC_SETUP_DIRECTORY/installation.yaml"
```

Review the four entries before replacing any existing ceiling; retain other
approved entries when your Installation already delegates access. Apply the
edited file using OCE's
[Installation-change procedure](https://github.com/openclaw/openclaw-enterprise/blob/main/docs/guides/deploy/production-installation-changes.md).
Run it from the installed controller's source checkout. Keep the controller and
runtime image references unchanged. The checksum update restarts OCC's API and
worker; it does not redeploy the Agents.

In OCC, open the Agent, select its current running version, and choose **Policy
denials**. Under **Agent network policy**, enable policy editing if needed. Confirm
that **Installation delegation ceiling** now lists the demo destinations.
Enabling editing requires Installation and Agent administration; editing additions
also requires Agent `manage_policy` and SandboxPolicy `update` permissions.

## Example 1: Python fetches Example Domain

Start with no Python → `example.com` addition. Ask the Agent in its Control UI:

```text
Use only /usr/bin/python3.11 and Python's urllib.request to fetch
https://example.com/. Print the HTTP status and whether the response contains
"Example Domain". Use a 15-second timeout. Do not use curl, a browser, another
interpreter, proxies or a fallback. If network policy blocks the request,
report the denial and stop.
```

In the OCC **Policy denials** tab:

1. Find the denial for `/usr/bin/python3.11` and `example.com:443`.
2. Under **Policy suggestions**, choose **Review suggestion** for that exact
   executable, hostname and port. Unrelated model-client suggestions are not part
   of this demo. If the provider has not produced a suggestion, add that exact
   rule manually under **Proposed network rules**.
3. Select **Review changes → Save and apply to current revision**.
4. Wait for application state **applied** and its provider version. **Save** alone
   does not change the running sandbox.
5. Ask the Agent:

```text
Retry the exact Python request now. Use the same interpreter and URL, with no
fallback. Print the HTTP status and whether "Example Domain" is present.
```

Expect HTTP `200` and a matching page title/content. Live policy application needs
no image build or Agent redeployment.

## More destination examples

Repeat the same deny → review → save-and-apply → retry sequence for each.

**Google robots file:** use the final hostname explicitly to avoid introducing
an extra redirect destination.

```text
Use only /usr/bin/python3.11 and urllib.request to GET
https://www.google.com/robots.txt with a 15-second timeout. Print the HTTP status
and first three lines. Do not switch tools or follow a fallback. If policy blocks
the request, report the denial and stop.
```

Approve Python → `www.google.com:443`, then repeat the request. Starting at
`https://google.com/` can redirect to `www.google.com`; the apex grant does not
authorize that second hostname. The example ceiling includes both so each exact
suggestion can be approved when demonstrating redirects intentionally.

**GitHub API with curl:** the Installation's existing git rule does not grant
curl access to the GitHub API.

```text
Use only /usr/bin/curl to GET https://api.github.com/zen with a 15-second timeout.
Print the response and HTTP status. Do not use git, Python, a browser, proxies
or a fallback. If policy blocks it, report the denial and stop.
```

Approve curl → `api.github.com:443`, then repeat. Expect an HTTP response from the
GitHub API; its unauthenticated rate limits still apply.

## Show that approval belongs to an executable

The following scenarios need additional ceiling entries. They are prepared demo
variants; the Python → `example.com` flow above is the one verified end to end.
Running a binary, such as `gh --version`, does not require a network grant. These
examples control which destinations that executable may contact.

### Extend the ceiling for these examples

Append these entries to the protected Installation's `sandboxNetworkPolicyCeiling`
list, preserving its existing entries. Then follow the Installation-change
procedure above. The checked-in four-entry demo ceiling does not include these
optional grants, and editing this document changes no running policy.

```yaml
- id: demo-curl-github
  binary: /usr/bin/curl
  host: github.com
  ports:
    - from: 443
      to: 443
- id: demo-node-example
  binary: /usr/local/bin/node
  host: example.com
  ports:
    - from: 443
      to: 443
- id: demo-gh-api
  binary: /usr/local/bin/gh
  host: api.github.com
  ports:
    - from: 443
      to: 443
```

These resolved paths were observed in the trial image. Verify them again when
changing runtime images. Adding the ceiling entries permits approval; it does
not create Agent additions.

### Git succeeds; curl to the same host is denied

The trial Installation already allows `/usr/bin/git` → `github.com:443` in its
read-only defaults. Start without a curl → `github.com` addition and ask:

```text
Run /usr/bin/git ls-remote https://github.com/octocat/Hello-World.git HEAD.
Then run /usr/bin/curl --head --max-time 15 https://github.com/.
Report each result separately. Do not follow redirects, switch tools, use a
proxy or retry with a fallback. If curl is blocked, report the denial and stop.
```

Expect git to succeed and curl to be denied. Review the curl → `github.com:443`
suggestion, save and apply it, and wait for **applied**. Ask the Agent to retry
only the exact curl command. This demonstrates that allowing a host for git does
not allow every binary to reach it. Neither command needs a GitHub token for this
public repository and page.

### Python succeeds; Node to the same host is denied

Keep the Python → `example.com:443` addition from Example 1 applied, but start
without a Node → `example.com` addition. Ask:

```text
First use /usr/bin/python3.11 and urllib.request to fetch https://example.com/
with a 15-second timeout and print its HTTP status. Then use only
/usr/local/bin/node and its built-in fetch to request the exact same URL with
a 15-second timeout and print the HTTP status. Report the results separately.
Do not switch tools, use a proxy or use a fallback if Node is blocked.
```

Expect Python to succeed and Node to be denied. Approve only Node →
`example.com:443`, wait for **applied**, and retry the Node request. The Python
approval does not transfer to Node. Reset each addition separately when finished.

### GitHub CLI credentials affect the denial demo

Use the [GitHub CLI credential recipe](github-cli.md) to attach a PAT through OCC
and OpenShell. Do not paste the PAT or its injected placeholder into chat.
A missing credential can stop `gh` before any request and produces no network
policy denial.

The credential profile authorizes its endpoint for the approved `gh` executable.
After attaching it, removing a gh → `api.github.com` Agent addition may therefore
leave the authenticated request working. Use the Python, Node or curl examples
above to demonstrate a separate network denial and approval. The optional gh
ceiling entry is unnecessary for the credential check itself.

## Reset for another presentation

Remove only the addition used by your demo, then **Review changes → Save and
apply to current revision** and wait for **applied**. Retry the original prompt to
show denial again. Preserve other approved additions and read-only defaults.
Old provider suggestions may remain visible; their presence does not mean access
is granted. Use the current policy and confirmed application state as evidence.

A ceiling error means the complete executable/host/port request does not fit the
operator's ceiling. Check the resolved binary, exact hostname, redirected hosts
and requested port range. A saved policy with a failed or pending application
has not established enforcement; inspect the application result before retrying.
