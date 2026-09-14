# Keycloak SSO

Optional, pluggable at zero cost: the stack ships on local email/password
auth and switches to Keycloak by uncommenting one `.env` block and
restarting `open-webui` -- no image rebuild, no config-file edit, and it
reverts the same way.

This assumes Keycloak is **already running** somewhere you control; this
stack does not deploy it.

## 1. Create the client

In your Keycloak admin console, in the realm you want Open WebUI users to
come from:

1. **Clients -> Create client**
   - Client ID: `open-webui`
   - Client authentication: **On** (confidential client)
   - Authentication flow: **Standard flow** only -- leave Direct access
     grants and everything else off
2. **Settings** tab:
   - Valid redirect URIs: `https://<your-chat-host>/oauth/oidc/login/callback`
   - Valid post logout redirect URIs: `https://<your-chat-host>/`
   - Web origins: `https://<your-chat-host>`
3. **Credentials** tab: copy the **Client secret** -- this is `OAUTH_CLIENT_SECRET`.

## 2. Create the access-gate role

1. **Realm roles -> Create role**: `open-webui-user`
2. Assign it to every user who should be able to log into Open WebUI at
   all. `OAUTH_ALLOWED_ROLES` below enforces this.
3. (Optional) Create a second role, `open-webui-admin`, for anyone who
   should land as an Open WebUI admin on first login instead of a plain
   user -- `OAUTH_ADMIN_ROLES` maps to it.

## 3. Fill in `.env`

Uncomment the whole OAuth block in `.env` and fill in:

```
ENABLE_OAUTH_SIGNUP=true
OAUTH_MERGE_ACCOUNTS_BY_EMAIL=true
OAUTH_PROVIDER_NAME=Keycloak
OAUTH_CLIENT_ID=open-webui
OAUTH_CLIENT_SECRET=<from step 1>
OPENID_PROVIDER_URL=https://<keycloak-host>/realms/<realm>/.well-known/openid-configuration
OAUTH_SCOPES="openid email profile"
OAUTH_ROLES_CLAIM=roles
OAUTH_ALLOWED_ROLES=open-webui-user
OAUTH_ADMIN_ROLES=open-webui-admin
ENABLE_OAUTH_ROLE_MANAGEMENT=true
```

`OPENID_PROVIDER_URL` is the full **discovery document** URL (ending in
`/.well-known/openid-configuration`), not just the realm root -- Open
WebUI fetches issuer/authorization/token/JWKS endpoints from it directly,
unlike some clients that derive those paths from a bare realm URL.

`OAUTH_ROLES_CLAIM=roles` assumes a client scope/mapper that puts Keycloak
realm roles under a top-level `roles` claim in the token -- Keycloak's own
default token shape nests them under `realm_access.roles` instead, so you
likely need a Keycloak **Client scope -> Mapper** (User Realm Role, token
claim name `roles`, "Multivalued" on) to actually get them there; without
one, `OAUTH_ROLES_CLAIM` finds nothing and every login is refused.

## 4. Restart and verify

```bash
docker compose up -d open-webui
```

- Visiting the chat host should redirect to Keycloak's login page instead
  of showing Open WebUI's own login form.
- Log in as a user **without** `open-webui-user` -- confirm access is
  refused.
- Log in as a user **with** `open-webui-user` -- confirm it succeeds and
  the Open WebUI account gets created/linked on first login.

To check the role claim actually lands in the token (useful if step 4
fails in a confusing way): decode the access token at
[jwt.io](https://jwt.io) after a login attempt and confirm your mapped
`roles` claim contains `open-webui-user`. If it's missing, the mapper from
step 3 isn't wired up, or the role wasn't assigned to that user.

## Switching back to local auth

Set `ENABLE_OAUTH_SIGNUP=false` (or comment the whole OAuth block back
out), restart `open-webui`. Existing local accounts are unaffected;
accounts created via Keycloak login remain in Postgres but can't log in
again until Keycloak is re-enabled.
