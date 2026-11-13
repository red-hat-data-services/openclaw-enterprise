---
created: 2026-11-12
---

# Stage Git co-authorship claims before a runtime update

For an existing login, keep its saved `settings.json` and client secret. Do not
recreate the realm or client. Set `TEAM_CREDIT_USERS_FILE` to a private JSON file
outside the checkout. Each key must match a username in the saved login:

```json
{
  "alice": {
    "name": "Alice Example",
    "email": "alice@example.test",
    "emailVerified": true
  },
  "bob": {
    "name": "Bob Example",
    "email": "bob@example.test",
    "emailVerified": false
  }
}
```

Use addresses confirmed by their owners. Set `emailVerified: true` only after
independently verifying ownership; the helper does not verify addresses. These
are client-specific, administrator-managed credit attributes in Keycloak. They
do not change account email, login aliases, subjects, passwords, or permissions.
An omitted user keeps any existing credit details. To withdraw verification,
include that user with `emailVerified: false` and run the helper again.

With the same explicit Keycloak environment used for that saved login, run:

```bash
export TEAM_CREDIT_USERS_FILE="$TEAM_LOGIN_DIRECTORY/credit-users.json"
deploy/rhoai/scripts/configure-team-login-credit
deploy/rhoai/scripts/prepare-team-login
oc apply --dry-run=server -f "$TEAM_LOGIN_DIRECTORY/proxy-manifests-private.json"
oc apply -f "$TEAM_LOGIN_DIRECTORY/proxy-manifests-private.json"
```

The first command changes the selected realm's user profile definitions, selected
users' credit attributes, and selected client's three token mappers. It saves a
private snapshot before the first change. On failure, inspect the snapshot and
private HTTP error before retrying. Successful repeats update the same attributes
and mappers. This does not create users or change their passwords.

The proxy overwrites `X-Coauthor-Name`, `X-Coauthor-Email`, and
`X-Coauthor-Email-Verified` from the dedicated signed claims. Users without credit
claims can still sign in. Apply the proxy manifests only; this staging step does
not require a Configuration deployment or runtime image update. Existing browser
sessions must sign out and sign in again to obtain new claims.

The renderer does not add `gateway.auth.trustedProxy.gitCoauthor`: the currently
pinned runtime does not recognize that setting. After updating to an OpenClaw
runtime that supports Git co-authorship, configure it separately:

```json
{
  "nameHeader": "x-coauthor-name",
  "emailHeader": "x-coauthor-email",
  "emailVerifiedHeader": "x-coauthor-email-verified"
}
```

Use this object as `gateway.auth.trustedProxy.gitCoauthor` in the Agent's
Configuration, deploy it, and have each person opt in under Profile. Imported
organization credit details do not grant consent or populate linked login emails.
Use an address associated with the person's GitHub account if GitHub attribution
is required. Verify a consented trailer in a disposable repository after the
runtime update. See the [claim flow](../../docs/flows/team-login-credit.md) for
ownership and proof limits.
