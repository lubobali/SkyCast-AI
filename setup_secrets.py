"""One-time secret setup for SkyCast-AI.

Creates the Databricks secret scope the deployed MCP server reads its
api.weather.gov contact string from, writes the value, and - the step that is
easy to skip and expensive to skip - grants the app's service principal READ on
the scope.

    python setup_secrets.py                    # scope + value
    python setup_secrets.py --grant skycast-ai # ... and the ACL for that app

Run from anywhere the Databricks SDK is authenticated.

On what is being stored. Neither weather API used here needs a key. What
api.weather.gov does need is a User-Agent naming the application and a way to
reach its operator; it answers 403 without one. That string is not a credential,
but it carries a personal email address, and a personal email address in a
public repository is what gets scraped. So it is kept out of git and read at
runtime through the same path an API key would take.
"""

from __future__ import annotations

import argparse
import sys

# Workspace-wide namespace. On a shared workspace - and a bootcamp workspace is
# very shared - a generic name like "database" may already exist under someone
# else's ownership, and writing to a scope you do not own fails with
# PermissionDenied, which reads like an authentication problem and is not one.
SCOPE = "lubo-skycast"
KEY = "nws-user-agent"


def looks_like_a_contact_string(value: str) -> bool:
    """A weak check for the shape NWS asks for: an app name and a way to reach you.

    Deliberately weak. The point is to catch an empty prompt or a pasted
    placeholder, not to police the format - NWS accepts anything descriptive,
    and a stricter rule would reject valid strings for no gain.
    """
    return bool(value) and ("@" in value or "http" in value.lower())


def create_scope(client) -> bool:
    print(f"Creating secret scope '{SCOPE}' (skipped if it already exists)...")
    try:
        client.secrets.create_scope(scope=SCOPE)
        print("  created")
    except Exception as exc:  # noqa: BLE001
        # RESOURCE_ALREADY_EXISTS is the expected path on every re-run.
        if "RESOURCE_ALREADY_EXISTS" in str(exc):
            print("  already exists")
        else:
            print(f"  could not create scope: {exc}")
            return False
    return True


def grant_app_read(client, app_name: str) -> bool:
    """Give the app's service principal READ on the scope.

    This is its own step because forgetting it produces a failure that does not
    look like a permissions failure. The app deploys, starts, reports healthy,
    and then cannot read its own secret - because the app runs as a service
    principal, which is a different identity from the human who created the
    scope. The symptom is a silently degraded server, not an error at deploy
    time.
    """
    from databricks.sdk.service.workspace import AclPermission

    print(f"\nGranting READ on '{SCOPE}' to the service principal for app '{app_name}'...")
    try:
        app = client.apps.get(name=app_name)
    except Exception as exc:  # noqa: BLE001
        print(f"  could not find app '{app_name}': {exc}")
        print("  deploy the app first, then re-run with --grant.")
        return False

    principal = getattr(app, "service_principal_client_id", None) or getattr(
        app, "service_principal_id", None
    )
    if not principal:
        print("  the app reports no service principal yet; wait for it to finish deploying.")
        return False

    try:
        client.secrets.put_acl(
            scope=SCOPE, principal=str(principal), permission=AclPermission.READ
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  could not grant READ: {exc}")
        return False

    print(f"  granted to {principal}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grant",
        metavar="APP_NAME",
        help="Also grant that Databricks App's service principal READ on the scope.",
    )
    parser.add_argument(
        "--value",
        help="The contact string. Prompted for if omitted.",
    )
    arguments = parser.parse_args()

    from databricks.sdk import WorkspaceClient

    client = WorkspaceClient()

    if not create_scope(client):
        return 1

    value = (arguments.value or "").strip()
    if not value:
        value = input(
            "\napi.weather.gov contact string\n"
            "  format: (AppName, your@email.com)\n"
            "> "
        ).strip()

    # Not read with getpass, unlike a password. This is a contact address whose
    # whole purpose is to be legible to NWS, and masking it would only make a
    # typo harder to notice - a typo that surfaces later as a 403.
    if not looks_like_a_contact_string(value):
        print(
            "\nThat does not contain an email address or a URL. NWS needs a way to "
            "reach whoever runs this. Nothing stored."
        )
        return 1

    client.secrets.put_secret(scope=SCOPE, key=KEY, string_value=value)
    print(f"\nStored {SCOPE}/{KEY}.")

    if arguments.grant and not grant_app_read(client, arguments.grant):
        return 1

    if not arguments.grant:
        print(
            "\nOnce the app is deployed, re-run with --grant <app-name> so its "
            "service principal can read this."
        )

    print("app.yaml already points at this scope, so the app needs no further config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
