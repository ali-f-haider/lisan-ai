"""Headers for Supabase's REST API that work with the old keys and the new ones.

Supabase is replacing the old project keys (the long "eyJ..." anon and
service_role keys) with "sb_publishable_..." and "sb_secret_..." keys, and
retiring the old ones by the end of 2026. The new keys are not JWTs: Supabase
says they belong in the `apikey` header only, and a new key sent as
`Authorization: Bearer` can be refused as an invalid JWT.

Every request in this app used to send the project key twice, in `apikey` and
in `Authorization: Bearer`. `sb_auth(key)` gives the `Authorization` part:
exactly what it always was for an old key, and nothing for a new one. So the
keys in Railway can be changed one at a time, whenever it suits, with no code
change and no downtime.

It is only for the project's own keys. A signed-in user's access token still
goes in `Authorization: Bearer <token>`; that is a real JWT.
"""

NEW_KEY_PREFIX = "sb_"          # sb_publishable_... and sb_secret_...; the old keys start with "eyJ"


def is_new_style_key(key) -> bool:
    return str(key or "").strip().startswith(NEW_KEY_PREFIX)


def sb_auth(key) -> dict:
    """The Authorization header for a project key: {"Authorization": "Bearer <key>"} for an old key,
    {} for a new one (use it as `{"apikey": key, **sb_auth(key)}`)."""
    if is_new_style_key(key):
        return {}
    return {"Authorization": f"Bearer {key}"}
