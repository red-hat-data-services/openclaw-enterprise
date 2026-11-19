# Rotate or replace an Agent's model credential

Day-2 procedures for the model credential source of Agents deployed with
[Deploy the first OpenShift Agent](README.md). The references own the behavior;
this page adds the RHOAI commands. See
[Update a source](../../../../docs/reference/credential-sources.md#update-a-source)
and
[Validation and recovery](../../../../docs/reference/drivers/kubernetes-compute/model-provider.md#validation-and-recovery).

## Before you begin

Set `OCC_INPUT_DIRECTORY`, `KUBECONFIG`, `OCC_URL`, `OCC_SERVICE_KEY_FILE`, and
`OCC_NAMESPACE` as in [Select the tenant](README.md#select-the-tenant). Then
select the source and list the Agents bound to it, through their draft or their
active revision. Each step below that names `$AGENT_ID` runs once per Agent in
that list.

```bash
deploy/rhoai/bin/occ credential-source list
# REQUIRED: the credential source to rotate or replace.
export SOURCE_ID='<credential source ID>'

rm -f "$OCC_INPUT_DIRECTORY/source-agents.txt"
match='if has("configurationReadError") then "unreadable"
  elif .harnessAuth.sourceId == $source or
    any(.credentialSources[]?; .sourceId == $source) then "bound"
  else "no" end'
deploy/rhoai/bin/occ agent list -o json > "$OCC_INPUT_DIRECTORY/agents.json" &&
  jq -e 'type == "array"' "$OCC_INPUT_DIRECTORY/agents.json" > /dev/null &&
  jq -c '.[]' "$OCC_INPUT_DIRECTORY/agents.json" | while read -r agent; do
    id="$(jq -r .id <<< "$agent")"
    draft="$(jq -r --arg source "$SOURCE_ID" "$match" <<< "$agent")"
    active=no
    revision="$(jq -r '.activeRevisionId // empty' <<< "$agent")"
    if [ -n "$revision" ]; then
      if out="$(deploy/rhoai/bin/occ agent revision "$id" "$revision" \
        -o json < /dev/null)"; then
        active="$(jq -r --arg source "$SOURCE_ID" "$match" <<< "$out")"
      else
        active=failed
      fi
    fi
    if [ "$draft" != no ] || [ "$active" != no ]; then
      printf '%s %s draft=%s active=%s\n' "$id" \
        "$(jq -r .desiredRuntimeState <<< "$agent")" "$draft" "$active"
    fi
  done > "$OCC_INPUT_DIRECTORY/source-agents.txt"
cat "$OCC_INPUT_DIRECTORY/source-agents.txt"
```

Each line gives the Agent ID, its desired runtime state, and whether its draft
and its active revision bind the source: `bound`, `no`, `unreadable`, or
`failed`. The list covers only Agents you can read.

- `active=unreadable` means OCC cannot show that revision's saved settings, for
  example a revision saved before Harness settings were recorded. Treat it as
  `bound`.
- `draft=unreadable` means OCC cannot read the Agent's saved settings; check the
  Agent with `occ agent get` before you continue.
- `failed` means the read itself failed. Run the block again, or ask an
  administrator who can read the Agent.

A deployment saves a new revision from the Agent's current draft and
Configuration; it does not repeat the active revision. Before you deploy an
Agent, compare `occ agent get "$AGENT_ID"` with
`occ agent revision "$AGENT_ID" <activeRevisionId>`. If the draft holds changes
that are not ready to ship, keep the old key or the old source until they are
reviewed. A `draft=no active=bound` Agent always has such a difference: its
draft has already moved off this source.

A deployment also starts a stopped Agent's workload, so deploy only Agents whose
desired state is `running`. A stopped Agent picks up the change at its next
intentional deployment. Every deploy command below takes
`--retain-harness-overrides <REVISION_ID>` for an Agent whose endpoint comes from
a revision Harness override; without it, the deployment uses the Installation
defaults.

## Rotate the key

OCC writes the gateway's copy of the key only when a source is created or
explicitly updated. Changing the Secret does not change the copy, and neither
does redeploying an Agent: deployment attaches the provider OpenShell already
holds without re-reading the Secret. Change the Secret, push it, then deploy.

```bash
export SECRET_ID="$(deploy/rhoai/bin/occ credential-source get "$SOURCE_ID" \
  -o json | jq -r '.secrets.api_key.id')"
test -n "$SECRET_ID" && test "$SECRET_ID" != null
umask 077
read -r -s -p 'OpenAI API key: ' OPENAI_API_KEY
printf '\n'
printf '%s' "$OPENAI_API_KEY" |
  jq -Rs '{value: .}' > "$OCC_INPUT_DIRECTORY/model-secret.json"
unset OPENAI_API_KEY
deploy/rhoai/bin/occ secret update "$SECRET_ID" \
  --file "$OCC_INPUT_DIRECTORY/model-secret.json" &&
  deploy/rhoai/bin/occ credential-source update "$SOURCE_ID"
rm -f "$OCC_INPUT_DIRECTORY/model-secret.json"
deploy/rhoai/bin/occ credential-source get "$SOURCE_ID"
deploy/rhoai/bin/occ secret get "$SECRET_ID"
```

Continue only when both updates succeed and the source shows `GATEWAY STATUS`
`ready`. `secret get` names the resources you can read that reference the
Secret, up to 50; run `credential-source update` for each other source it lists,
and include their Agents below. If it reports unreadable or more references, an
administrator who can read every source must list them before you revoke the old
key:

```bash
deploy/rhoai/bin/occ credential-source list -o json \
  > "$OCC_INPUT_DIRECTORY/sources.json" &&
  jq -e 'type == "array"' "$OCC_INPUT_DIRECTORY/sources.json" > /dev/null &&
  jq -r --arg secret "$SECRET_ID" \
    '.[] | select(any(.secrets[]?; .id == $secret)) | .id' \
    "$OCC_INPUT_DIRECTORY/sources.json"
```

The value must not end with a newline: OpenShell rejects it, and
the revision then fails its model probe with `credential_unavailable`.

OpenShell gives the new value only to processes started after the update, and
an already-failed revision does not re-run its model probe. Deploy each running
Agent, then repeat the status command until that deployment reports
`succeeded`:

```bash
# REQUIRED: one running Agent ID from source-agents.txt.
export AGENT_ID='<Agent ID>'
export DEPLOYMENT_ID="$(deploy/rhoai/bin/occ agent deploy "$AGENT_ID" \
  -o json | jq -r .id)"
test -n "$DEPLOYMENT_ID" && test "$DEPLOYMENT_ID" != null &&
  deploy/rhoai/bin/occ agent deployment-status "$AGENT_ID" "$DEPLOYMENT_ID"
```

Deploying a dedicated Agent stops its running predecessor before the
replacement activates, so rotation interrupts service. If the replacement fails
its model probe, the Agent stays down until you fix the cause and deploy again.
Revoke the previous key at the provider only after each of these deployments
reports `succeeded`.

## Replace a source saved with `config.base_url`

A source saved with the removed `config.base_url` cannot be attached or
refreshed, and deploying an Agent that binds it fails with a `409` naming the
fix. There is no automatic migration. This section covers Agents outside a
running upgrade, such as Agents stopped before it. For an Agent a runtime
upgrade refused, follow
[Correct a refused Agent deployment](../../../../docs/guides/deploy/production-upgrade-recovery.md#correct-a-refused-agent-deployment)
instead.

The replacement's `config.authorized_base_url` only authorizes a destination:
the endpoint itself comes from the Installation `modelProvider` or a revision
override. Set the Installation provider first, as in
[Register the model credential](README.md#register-the-model-credential), and
authorize exactly the `baseUrl` your Agents' revisions resolve to.

A source authorizes one endpoint, and its key goes only there. If the Agents on
the old source resolve to different endpoints, run this section once per
endpoint and rebind only that endpoint's Agents. The block reuses the old
source's Secret only when `MODEL_BASE_URL` exactly matches the old source's
`config.base_url`, the URL that key was already sent to. For any other
endpoint, create a Secret holding a key issued for it, as in
[Register the model credential](README.md#register-the-model-credential), and
fill in its reference where the block asks.

```bash
rm -f "$OCC_INPUT_DIRECTORY/replacement-source-id"
unset NEW_SOURCE_ID SECRET_REF
export OLD_SOURCE_ID="$SOURCE_ID"
# REQUIRED: the endpoint the Agents' revisions resolve to.
export MODEL_BASE_URL='https://models.example.com/v1'
# REQUIRED: a name no other source in the Namespace uses.
export REPLACEMENT_NAME='openai-2'
deploy/rhoai/bin/occ credential-source get "$OLD_SOURCE_ID" -o json \
  > "$OCC_INPUT_DIRECTORY/old-source.json"
if [ "$(jq -r '.config.base_url' "$OCC_INPUT_DIRECTORY/old-source.json")" = \
  "$MODEL_BASE_URL" ]; then
  export SECRET_REF="$(jq -c '.secrets.api_key' \
    "$OCC_INPUT_DIRECTORY/old-source.json")"
else
  # REQUIRED for another endpoint: its Secret's reference, as JSON.
  export SECRET_REF='<Secret reference JSON>'
fi
test -n "$SECRET_REF" && test "$SECRET_REF" != null &&
  jq -n --argjson ref "$SECRET_REF" --arg name "$REPLACEMENT_NAME" \
    --arg url "$MODEL_BASE_URL" \
    '{name: $name, type: "openai", secrets: {api_key: $ref},
      config: {authorized_base_url: $url}}' \
    > "$OCC_INPUT_DIRECTORY/replacement-source.json" &&
  deploy/rhoai/bin/occ credential-source create \
    --file "$OCC_INPUT_DIRECTORY/replacement-source.json" -o json |
  jq -r .id > "$OCC_INPUT_DIRECTORY/replacement-source-id"
export NEW_SOURCE_ID="$(cat "$OCC_INPUT_DIRECTORY/replacement-source-id")"
test -n "$NEW_SOURCE_ID" && test "$NEW_SOURCE_ID" != null &&
  deploy/rhoai/bin/occ credential-source get "$NEW_SOURCE_ID"
```

Find a Role that grants exactly `operate` on credential sources, or create one:

```bash
if deploy/rhoai/bin/occ iam role list -o json \
  > "$OCC_INPUT_DIRECTORY/iam-roles.json" &&
  jq -e 'type == "array"' "$OCC_INPUT_DIRECTORY/iam-roles.json" > /dev/null; then
  export ROLE_ID="$(jq -r 'map(select(.permissions == [{"action": "operate",
    "resourceKind": "credential_source"}]))[0].id // empty' \
    "$OCC_INPUT_DIRECTORY/iam-roles.json")"
  if [ -z "$ROLE_ID" ]; then
    jq -n '{name: "Use an OpenShell credential source",
      permissions: [{action: "operate", resourceKind: "credential_source"}]}' \
      > "$OCC_INPUT_DIRECTORY/credential-source-role.json"
    export ROLE_ID="$(deploy/rhoai/bin/occ iam role create \
      --file "$OCC_INPUT_DIRECTORY/credential-source-role.json" -o json | jq -r .id)"
  fi
else
  echo 'Could not list IAM roles; not creating a role' >&2
fi
test -n "$ROLE_ID" && test "$ROLE_ID" != null
```

Then, for each Agent in `source-agents.txt` whose draft is `bound`, grant its
service principal `operate` on the replacement, and rebind it. An Agent bound
only through its active revision needs just the deploy below, which also ships
its pending draft changes. Deployment checks the grant for
both you and the Agent's principal, and an access binding names one exact
source, so the old binding does not carry over. Build the update from a fresh
read of the Agent: the `credentialSources` list replaces the draft's bindings,
so the update keeps the Agent's other sources and swaps only the old one.

```bash
# REQUIRED: one Agent ID from source-agents.txt.
export AGENT_ID='<Agent ID>'
deploy/rhoai/bin/occ agent get "$AGENT_ID" -o json \
  > "$OCC_INPUT_DIRECTORY/agent-current.json"
jq --arg role "$ROLE_ID" --arg source "$NEW_SOURCE_ID" \
  '{subjectKind: "identity", subjectId: .servicePrincipalId, roleId: $role,
    resourceKind: "credential_source", resourceId: $source}' \
  "$OCC_INPUT_DIRECTORY/agent-current.json" \
  > "$OCC_INPUT_DIRECTORY/replacement-binding.json"
deploy/rhoai/bin/occ iam access-binding create \
  --file "$OCC_INPUT_DIRECTORY/replacement-binding.json"

jq --arg old "$OLD_SOURCE_ID" --arg source "$NEW_SOURCE_ID" '
  {configurationId: .configurationId} +
  (if .harnessAuth.method == "credential_source" and .harnessAuth.sourceId == $old
    then {harnessAuth: {method: "credential_source", sourceId: $source}}
    else {} end) +
  (if any(.credentialSources[]?; .sourceId == $old)
    then {credentialSources: ((.credentialSources | map(.sourceId)) - [$old] +
      [$source] | unique | map({sourceId: .}))}
    else {} end)' \
  "$OCC_INPUT_DIRECTORY/agent-current.json" \
  > "$OCC_INPUT_DIRECTORY/agent-update.json"
deploy/rhoai/bin/occ agent update "$AGENT_ID" \
  --file "$OCC_INPUT_DIRECTORY/agent-update.json"
```

Fields the update omits, such as the display name, keep their current values.
Deploy each Agent whose desired state is `running`, and each Agent you stopped
before an upgrade, with the deploy command in [Rotate the key](#rotate-the-key),
and wait until that deployment reports `succeeded`.

Once those deployments succeed, delete the old source:

```bash
deploy/rhoai/bin/occ credential-source delete "$OLD_SOURCE_ID"
```

OCC refuses the delete with `409` while anything still references the source;
[Delete a source](../../../../docs/reference/credential-sources.md#delete-a-source)
lists each case and what to do.
