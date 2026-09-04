"""Verify the two Metabase demo profiles and their collection isolation."""

from __future__ import annotations

from setup_metabase import ADMIN_EMAIL, ADMIN_PASSWORD, MetabaseApi


PROFILES = (
    (
        "pilotage@chu.local",
        "Pilotage2026!",
        "Pilotage hospitalier",
        "Recherche clinique",
    ),
    (
        "recherche@chu.local",
        "Recherche2026!",
        "Recherche clinique",
        "Pilotage hospitalier",
    ),
)


def dashboard_ids() -> dict[str, int]:
    api = MetabaseApi()
    session = api.request(
        "POST",
        "/api/session",
        {"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    api.session_id = session["id"]
    result = {}
    for collection in api.request("GET", "/api/collection"):
        if collection["name"] not in {"Pilotage hospitalier", "Recherche clinique"}:
            continue
        items = api.request(
            "GET", f"/api/collection/{collection['id']}/items?models=dashboard"
        )
        dashboard = next(
            item for item in items["data"] if item["name"] == collection["name"]
        )
        result[dashboard["name"]] = dashboard["id"]
    if len(result) != 2:
        raise RuntimeError("The two expected dashboards were not found")
    return result


def main() -> None:
    ids = dashboard_ids()
    for email, password, expected_name, denied_name in PROFILES:
        api = MetabaseApi()
        session = api.request(
            "POST", "/api/session", {"username": email, "password": password}
        )
        api.session_id = session["id"]

        dashboard = api.request("GET", f"/api/dashboard/{ids[expected_name]}")
        if dashboard["name"] != expected_name:
            raise RuntimeError(f"Unexpected dashboard for {email}: {dashboard['name']}")

        try:
            api.request("GET", f"/api/dashboard/{ids[denied_name]}")
        except RuntimeError as error:
            if "(403)" not in str(error):
                raise
        else:
            raise RuntimeError(f"Cross-dashboard access was not denied for {email}")

        print(f"{email}: {expected_name} visible, accès croisé refusé")


if __name__ == "__main__":
    main()
