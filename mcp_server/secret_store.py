"""Reading the one sensitive value this server has, without committing it.

Worth being straight about what the secret is, because the homework's rule is
"if your chosen API requires a key, store it as a Databricks secret" - and
neither API here requires one. Open-Meteo is open. api.weather.gov is open too.

What api.weather.gov *does* require is a User-Agent naming the application and
a way to contact whoever runs it. It answers 403 without one. That string is
not a credential - nobody can spend it - but it does carry a personal email
address, and a personal email address in a public repository is exactly the
sort of thing that gets scraped. So it is treated as a secret: kept out of git,
kept out of app.yaml, and resolved at runtime through the same
`WorkspaceClient().secrets.get_secret()` path a real API key would take.

The pattern is therefore demonstrated on a value that genuinely benefits from
it, rather than on an invented key that no API would ever check.

Named `secret_store` rather than the obvious `secrets` because Python ships a
stdlib module by that name. This directory is on sys.path in the deployed app,
so a local `secrets.py` would shadow it for every library in the process - and
the first thing to break would be somebody else's token generation, a long way
from here and with nothing in the traceback pointing back at this file.

Resolution order:

1. `NWS_USER_AGENT` environment variable - local development, from `.env`.
2. The Databricks secret scope named in app.yaml - the deployed app.
3. `NWS_USER_AGENT_FALLBACK` from app.yaml - a generic, non-personal contact.

Reading the environment first is what keeps local runs and deployed runs on one
code path. Reading only from the scope would make the server impossible to run,
or test, outside a Databricks runtime.
"""

from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger(__name__)

# Secret scopes are workspace-wide. In a shared workspace - and a bootcamp
# workspace is very shared - a generic name like "database" may already exist
# and belong to someone else, and a scope you do not own cannot be written to.
SCOPE = os.environ.get("WEATHER_SECRET_SCOPE", "lubo-skycast")
KEY = os.environ.get("WEATHER_SECRET_KEY", "nws-user-agent")

FALLBACK_USER_AGENT = os.environ.get(
    "NWS_USER_AGENT_FALLBACK",
    "(SkyCast-AI, bootcamp project, contact via GitHub lubobali/SkyCast-AI)",
)


def read_secret(scope: str = SCOPE, key: str = KEY) -> str | None:
    """Read one Databricks secret, or None if it cannot be read.

    Returns None rather than raising on every failure path - missing SDK, no
    workspace credentials, scope absent, key absent, ACL not granted. A server
    that will not start because a secret scope has not been created yet is
    harder to debug than one that starts and says which value it fell back to.

    Args:
        scope: Secret scope name.
        key: Key within the scope.

    Returns:
        The decoded secret, or None.
    """
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        logger.debug("databricks-sdk is not installed; not reading secret %s/%s", scope, key)
        return None

    try:
        secret = WorkspaceClient().secrets.get_secret(scope=scope, key=key)
    except Exception as exc:  # noqa: BLE001 - any failure here means "fall back"
        # The most common cause in a deployed app is not a missing secret but a
        # missing ACL: the app's service principal is a different identity from
        # the human who created the scope, and it needs READ granted to it
        # explicitly. setup_secrets.py does that; this log line is what points
        # at it when someone forgets.
        logger.warning(
            "Could not read secret %s/%s (%s). If the app is deployed, check that "
            "its service principal has READ on the scope.",
            scope, key, exc,
        )
        return None

    # The API returns the value base64-encoded regardless of how it was written.
    return base64.b64decode(secret.value).decode("utf-8").strip()


def resolve_user_agent() -> tuple[str, str]:
    """The contact string api.weather.gov requires, and where it came from.

    The source is returned alongside the value because "did the secret actually
    work" is otherwise unanswerable from outside. Both the secret and the
    fallback are valid contact strings that NWS accepts, so a server quietly
    running on the fallback because an ACL was never granted looks exactly like
    one running on the secret. /healthz reports the source, never the value.

    Returns:
        (user_agent, source), where source is "environment", "secret", or
        "fallback".
    """
    from_env = (os.environ.get("NWS_USER_AGENT") or "").strip()
    if from_env:
        return from_env, "environment"

    from_secret = read_secret()
    if from_secret:
        return from_secret, "secret"

    logger.warning(
        "Using the fallback NWS User-Agent. Set NWS_USER_AGENT locally, or put a "
        "contact address in the %s/%s secret, so NWS can reach whoever runs this.",
        SCOPE, KEY,
    )
    return FALLBACK_USER_AGENT, "fallback"


def nws_user_agent() -> str:
    """The contact string api.weather.gov requires, from wherever it lives.

    Never returns empty: NWSAlertClient refuses to construct without one, and a
    generic fallback that gets the server running beats a 403 that reads like
    an outage.
    """
    return resolve_user_agent()[0]


__all__ = [
    "FALLBACK_USER_AGENT",
    "KEY",
    "SCOPE",
    "nws_user_agent",
    "read_secret",
    "resolve_user_agent",
]
