# Keycloak SSO

Optional, pluggable at zero cost: the stack ships on local email/password
auth and switches to Keycloak by uncommenting one `.env` block and
restarting `api` -- no image rebuild, no config-file edit, and it reverts
the same way.

This assumes Keycloak is **already running** somewhere you control; this
stack does not deploy it.

## 1. Create the client

In your Keycloak admin console, in the realm you want LibreChat users to
come from:

1. **Clients -> Create client**
   - Client ID: `librechat`
   - Client authentication: **On** (confidential client)
   - Authentication flow: **Standard flow** only -- leave Direct access
     grants and everything else off
2. **Settings** tab:
   - Valid redirect URIs: `https://<your-chat-host>/oauth/openid/callback`
   - Valid post logout redirect URIs: `https://<your-chat-host>/`
   - Web origins: `https://<your-chat-host>`
3. **Credentials** tab: copy the **Client secret** -- this is `OPENID_CLIENT_SECRET`.

## 2. Create the access-gate role

1. **Realm roles -> Create role**: `librechat-user`
2. Assign it to every user who should be able to log into LibreChat at all.
   No role, no access -- `OPENID_REQUIRED_ROLE` below enforces this.

Two more roles are worth creating now even though nothing in this launch
enforces them yet (per-tool authorization happens in the MCP layer later,
not here): `librechat-product` and `librechat-dev`, for scoping which team's
agents a user can see once that's built.

## 3. Fill in `.env`

Uncomment the whole `OPENID_*` block in `.env`, set `ALLOW_EMAIL_LOGIN=false`,
and fill in:

```
ALLOW_SOCIAL_LOGIN=true
ALLOW_SOCIAL_REGISTRATION=true
OPENID_ISSUER=https://<keycloak-host>/realms/<realm>
OPENID_CLIENT_ID=librechat
OPENID_CLIENT_SECRET=<from step 1>
OPENID_CALLBACK_URL=/oauth/openid/callback
OPENID_SCOPE="openid profile email"
OPENID_SESSION_SECRET=<generate with scripts/generate-secrets.sh>
OPENID_REQUIRED_ROLE=librechat-user
OPENID_REQUIRED_ROLE_PARAMETER_PATH=realm_access.roles
OPENID_REQUIRED_ROLE_TOKEN_KIND=access
OPENID_USE_END_SESSION_ENDPOINT=true
```

`OPENID_REQUIRED_ROLE_PARAMETER_PATH=realm_access.roles` is specifically
where Keycloak puts realm roles in its access token -- this is not a generic
OIDC default, it's Keycloak's own token shape.

## 4. Restart and verify

```bash
docker compose up -d api
```

- Visiting the chat host should redirect to Keycloak's login page instead of
  showing LibreChat's own login form.
- Log in as a user **without** `librechat-user` -- confirm access is refused.
- Log in as a user **with** `librechat-user` -- confirm it succeeds and the
  LibreChat account gets created/linked on first login.

To check the role claim actually lands in the token (useful if step 4 fails
in a confusing way): decode the access token at
[jwt.io](https://jwt.io) after a login attempt and confirm
`realm_access.roles` contains `librechat-user`. If it's missing, the role
wasn't assigned to that user, or a client scope is stripping it.

## Switching back to local auth

Set `ALLOW_EMAIL_LOGIN=true`, comment the `OPENID_*` block back out (or just
leave the values -- `ALLOW_SOCIAL_LOGIN=false` alone is enough to disable
the flow), restart `api`. Existing local accounts are unaffected; accounts
created via Keycloak login remain in Mongo but can't log in again until
Keycloak is re-enabled.
