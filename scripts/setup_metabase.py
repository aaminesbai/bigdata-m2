"""Provision the local Metabase instance used for the EDS demonstration."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


BASE_URL = "http://localhost:3000"
ADMIN_EMAIL = "admin@chu.local"
ADMIN_PASSWORD = "ChuAdmin2026!"


class MetabaseApi:
    def __init__(self) -> None:
        self.session_id: str | None = None

    def request(self, method: str, path: str, payload=None):
        headers = {"Content-Type": "application/json"}
        if self.session_id:
            headers["X-Metabase-Session"] = self.session_id
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            BASE_URL + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Metabase {method} {path} failed ({error.code}): {details}"
            ) from error

    def wait_until_ready(self) -> None:
        for _ in range(60):
            try:
                if self.request("GET", "/api/health").get("status") == "ok":
                    return
            except (OSError, RuntimeError):
                pass
            time.sleep(2)
        raise RuntimeError("Metabase did not become ready")

    def setup_or_login(self) -> None:
        properties = self.request("GET", "/api/session/properties")
        token = properties.get("setup-token")
        if token:
            self.request(
                "POST",
                "/api/setup",
                {
                    "token": token,
                    "user": {
                        "email": ADMIN_EMAIL,
                        "first_name": "Admin",
                        "last_name": "CHU",
                        "password": ADMIN_PASSWORD,
                        "site_name": "EDS du CHU",
                    },
                    "prefs": {
                        "site_name": "EDS du CHU",
                        "site_locale": "fr",
                        "allow_tracking": False,
                    },
                },
            )
        session = self.request(
            "POST",
            "/api/session",
            {"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        self.session_id = session["id"]


def find_named(items, name: str):
    return next((item for item in items if item.get("name") == name), None)


def ensure_group(api: MetabaseApi, name: str) -> dict:
    existing = find_named(api.request("GET", "/api/permissions/group"), name)
    return existing or api.request("POST", "/api/permissions/group", {"name": name})


def ensure_collection(api: MetabaseApi, name: str, description: str) -> dict:
    collections = api.request("GET", "/api/collection")
    existing = find_named(collections, name)
    return existing or api.request(
        "POST",
        "/api/collection",
        {"name": name, "description": description, "parent_id": None},
    )


def ensure_user(
    api: MetabaseApi, email: str, first_name: str, group_id: int, password: str
) -> dict:
    users = api.request("GET", "/api/user?status=all")
    existing = next((user for user in users["data"] if user["email"] == email), None)
    user = existing or api.request(
        "POST",
        "/api/user",
        {
            "email": email,
            "first_name": first_name,
            "last_name": "CHU",
            "password": password,
        },
    )
    memberships = api.request("GET", "/api/permissions/membership")
    user_memberships = memberships.get(str(user["id"]), memberships.get(user["id"], []))
    if not any(item["group_id"] == group_id for item in user_memberships):
        api.request(
            "POST",
            "/api/permissions/membership",
            {"user_id": user["id"], "group_id": group_id},
        )
    return user


def ensure_database(
    api: MetabaseApi, name: str, username: str, password: str
) -> dict:
    databases = api.request("GET", "/api/database")["data"]
    existing = find_named(databases, name)
    if existing:
        return existing
    return api.request(
        "POST",
        "/api/database",
        {
            "engine": "clickhouse",
            "name": name,
            "details": {
                "host": "clickhouse-bigdata",
                "port": 8123,
                "user": username,
                "password": password,
                "ssl": False,
                "enable-multiple-db": True,
                "db-filters-type": "inclusion",
                "db-filters-patterns": "gold",
            },
            "is_full_sync": True,
            "is_on_demand": False,
        },
    )


def ensure_card(
    api: MetabaseApi,
    collection_id: int,
    database_id: int,
    name: str,
    description: str,
    sql: str,
    display: str,
    dimensions: list[str] | None = None,
    metrics: list[str] | None = None,
) -> dict:
    cards = api.request("GET", f"/api/collection/{collection_id}/items?models=card")
    existing = next(
        (item for item in cards["data"] if item.get("name") == name), None
    )
    settings = {}
    if dimensions:
        settings["graph.dimensions"] = dimensions
    if metrics:
        settings["graph.metrics"] = metrics
    if display in {"bar", "line"}:
        settings["graph.show_values"] = True

    payload = {
        "name": name,
        "description": description,
        "collection_id": collection_id,
        "display": display,
        "visualization_settings": settings,
        "dataset_query": {
            "database": database_id,
            "type": "native",
            "native": {"query": sql, "template-tags": {}},
        },
    }
    if existing:
        return api.request("PUT", f"/api/card/{existing['id']}", payload)
    return api.request("POST", "/api/card", payload)


def ensure_dashboard(
    api: MetabaseApi, collection_id: int, name: str, description: str
) -> dict:
    items = api.request(
        "GET", f"/api/collection/{collection_id}/items?models=dashboard"
    )
    existing = next(
        (item for item in items["data"] if item.get("name") == name), None
    )
    return existing or api.request(
        "POST",
        "/api/dashboard",
        {
            "name": name,
            "description": description,
            "collection_id": collection_id,
            "parameters": [],
        },
    )


def add_dashboard_cards(api: MetabaseApi, dashboard_id: int, cards: list[dict]) -> None:
    dashboard = api.request("GET", f"/api/dashboard/{dashboard_id}")
    existing_by_card = {
        dashcard.get("card", {}).get("id"): dashcard
        for dashcard in dashboard.get("dashcards", [])
    }
    updates = []
    for index, card in enumerate(cards):
        dashcard = existing_by_card.get(card["id"], {})
        updates.append(
            {
                "id": dashcard.get("id", -(index + 1)),
                "card_id": card["id"],
                "row": (index // 2) * 8,
                "col": (index % 2) * 12,
                "size_x": 12,
                "size_y": 8,
                "parameter_mappings": [],
                "visualization_settings": dashcard.get("visualization_settings", {}),
                "series": [],
            }
        )
    api.request("PUT", f"/api/dashboard/{dashboard_id}", {"dashcards": updates})


def configure_collection_permissions(
    api: MetabaseApi,
    pilotage_group: dict,
    recherche_group: dict,
    pilotage_collection: dict,
    recherche_collection: dict,
) -> None:
    graph = api.request("GET", "/api/collection/graph")
    groups = graph["groups"]
    all_users_id = str(
        next(
            group["id"]
            for group in api.request("GET", "/api/permissions/group")
            if group["name"] == "All Users"
        )
    )
    for group_id in (all_users_id, str(pilotage_group["id"]), str(recherche_group["id"])):
        groups.setdefault(group_id, {})["root"] = "none"
        groups[group_id][pilotage_collection["id"]] = "none"
        groups[group_id][recherche_collection["id"]] = "none"
    groups[str(pilotage_group["id"])][pilotage_collection["id"]] = "read"
    groups[str(recherche_group["id"])][recherche_collection["id"]] = "read"
    api.request("PUT", "/api/collection/graph", graph)


def configure_data_permissions(
    api: MetabaseApi,
    pilotage_group: dict,
    recherche_group: dict,
    pilotage_database: dict,
    recherche_database: dict,
) -> None:
    graph = api.request("GET", "/api/permissions/graph")
    groups = graph["groups"]
    all_users_id = str(
        next(
            group["id"]
            for group in api.request("GET", "/api/permissions/group")
            if group["name"] == "All Users"
        )
    )
    database_ids = {
        str(database["id"])
        for database in api.request("GET", "/api/database")["data"]
    }
    blocked = {
        "view-data": "legacy-no-self-service",
        "create-queries": "no",
        "download": {"schemas": "none"},
    }
    granted = {
        "view-data": "unrestricted",
        "create-queries": "query-builder-and-native",
        "download": {"schemas": "full"},
    }
    for group_id in (
        all_users_id,
        str(pilotage_group["id"]),
        str(recherche_group["id"]),
    ):
        groups.setdefault(group_id, {})
        for database_id in database_ids:
            groups[group_id][database_id] = blocked.copy()
    groups[str(pilotage_group["id"])][str(pilotage_database["id"])] = granted.copy()
    groups[str(recherche_group["id"])][str(recherche_database["id"])] = granted.copy()
    api.request("PUT", "/api/permissions/graph", graph)


def main() -> None:
    api = MetabaseApi()
    api.wait_until_ready()
    api.setup_or_login()

    pilotage_group = ensure_group(api, "Pilotage hospitalier")
    recherche_group = ensure_group(api, "Recherche clinique")
    pilotage_collection = ensure_collection(
        api,
        "Pilotage hospitalier",
        "Indicateurs opérationnels destinés à la direction hospitalière.",
    )
    recherche_collection = ensure_collection(
        api,
        "Recherche clinique",
        "Cohortes agrégées pour la recherche; les effectifs inférieurs à 5 sont exclus.",
    )

    ensure_user(
        api,
        "pilotage@chu.local",
        "Utilisateur Pilotage",
        pilotage_group["id"],
        "Pilotage2026!",
    )
    ensure_user(
        api,
        "recherche@chu.local",
        "Utilisateur Recherche",
        recherche_group["id"],
        "Recherche2026!",
    )

    pilotage_db = ensure_database(
        api, "EDS CHU - Pilotage", "metabase_pilotage", "PilotageDb2026!"
    )
    recherche_db = ensure_database(
        api, "EDS CHU - Recherche", "metabase_recherche", "RechercheDb2026!"
    )

    pilotage_cards = [
        ensure_card(
            api,
            pilotage_collection["id"],
            pilotage_db["id"],
            "DMS par service",
            "Durée moyenne des séjours terminés, en jours.",
            "SELECT service_name AS service, average_stay_days AS dms_jours FROM gold.dms_par_service ORDER BY dms_jours DESC",
            "bar",
            ["service"],
            ["dms_jours"],
        ),
        ensure_card(
            api,
            pilotage_collection["id"],
            pilotage_db["id"],
            "Passages aux urgences par jour",
            "Nombre quotidien de séjours admis en mode urgence.",
            "SELECT activity_date AS date, emergency_visits AS passages FROM gold.activite_urgences_par_jour ORDER BY date",
            "line",
            ["date"],
            ["passages"],
        ),
        ensure_card(
            api,
            pilotage_collection["id"],
            pilotage_db["id"],
            "Séjours en cours aux urgences",
            "Nombre de séjours sans date de sortie, par date d'admission.",
            "SELECT activity_date AS date, current_stays AS sejours_en_cours FROM gold.activite_urgences_par_jour ORDER BY date",
            "line",
            ["date"],
            ["sejours_en_cours"],
        ),
        ensure_card(
            api,
            pilotage_collection["id"],
            pilotage_db["id"],
            "Durée moyenne aux urgences",
            "Durée moyenne en heures des séjours terminés au service des urgences.",
            "SELECT activity_date AS date, average_stay_hours AS duree_moyenne_heures FROM gold.activite_urgences_par_jour ORDER BY date",
            "line",
            ["date"],
            ["duree_moyenne_heures"],
        ),
        ensure_card(
            api,
            pilotage_collection["id"],
            pilotage_db["id"],
            "Taux de réadmission observé à 30 jours",
            "Part des séjours précédés d'une sortie du même patient dans les 30 jours.",
            "SELECT provisional_readmission_rate_percent AS taux_percent FROM gold.taux_readmission_30_jours",
            "scalar",
        ),
        ensure_card(
            api,
            pilotage_collection["id"],
            pilotage_db["id"],
            "Nombre total de séjours",
            "Dénominateur du taux de réadmission.",
            "SELECT eligible_discharges AS sejours FROM gold.taux_readmission_30_jours",
            "scalar",
        ),
        ensure_card(
            api,
            pilotage_collection["id"],
            pilotage_db["id"],
            "Réadmissions à 30 jours",
            "Nombre de séjours précédés d'une sortie du même patient dans les 30 jours.",
            "SELECT observed_readmissions AS readmissions FROM gold.taux_readmission_30_jours",
            "scalar",
        ),
        ensure_card(
            api,
            pilotage_collection["id"],
            pilotage_db["id"],
            "Alertes de constantes par jour",
            "FC < 50 ou > 100, SpO2 < 92 ou température > 38,5 °C.",
            "SELECT activity_date AS date, alert_readings AS alertes FROM gold.alertes_constantes_par_jour ORDER BY date",
            "line",
            ["date"],
            ["alertes"],
        ),
        ensure_card(
            api,
            pilotage_collection["id"],
            pilotage_db["id"],
            "Taux d'alerte quotidien",
            "Part des relevés déclenchant au moins une alerte.",
            "SELECT activity_date AS date, alert_rate_percent AS taux_percent FROM gold.alertes_constantes_par_jour ORDER BY date",
            "line",
            ["date"],
            ["taux_percent"],
        ),
    ]
    recherche_cards = [
        ensure_card(
            api,
            recherche_collection["id"],
            recherche_db["id"],
            "Prévalence par pathologie",
            "Patients distincts par diagnostic; seules les cohortes d'au moins 5 patients sont diffusées.",
            "SELECT diagnostic_name AS diagnostic, cohort_size AS patients, prevalence_percent AS prevalence_percent FROM gold.prevalence_par_pathologie ORDER BY patients DESC",
            "bar",
            ["diagnostic"],
            ["patients"],
        ),
        ensure_card(
            api,
            recherche_collection["id"],
            recherche_db["id"],
            "Cohortes par âge et sexe",
            "Diagnostics principaux par tranche décennale et sexe; cellules de moins de 5 patients exclues.",
            "SELECT diagnostic_name AS diagnostic, age_group AS tranche_age, sex AS sexe, patient_count AS patients FROM gold.distribution_cohorte_age_sexe ORDER BY diagnostic, age_group_order, sexe",
            "table",
        ),
    ]

    pilotage_dashboard = ensure_dashboard(
        api,
        pilotage_collection["id"],
        "Pilotage hospitalier",
        "Vue direction : activité, durée des séjours, réadmissions et surveillance.",
    )
    recherche_dashboard = ensure_dashboard(
        api,
        recherche_collection["id"],
        "Recherche clinique",
        "Vue chercheurs : prévalence et description de cohortes agrégées (seuil RGPD >= 5).",
    )
    add_dashboard_cards(api, pilotage_dashboard["id"], pilotage_cards)
    add_dashboard_cards(api, recherche_dashboard["id"], recherche_cards)
    configure_data_permissions(
        api,
        pilotage_group,
        recherche_group,
        pilotage_db,
        recherche_db,
    )
    configure_collection_permissions(
        api,
        pilotage_group,
        recherche_group,
        pilotage_collection,
        recherche_collection,
    )

    print(f"Pilotage dashboard: {BASE_URL}/dashboard/{pilotage_dashboard['id']}")
    print(f"Recherche dashboard: {BASE_URL}/dashboard/{recherche_dashboard['id']}")


if __name__ == "__main__":
    main()
