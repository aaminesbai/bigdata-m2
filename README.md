# Entrepot de donnees de sante du CHU

Ce projet met en place un entrepot de donnees de sante complet : il collecte les
fichiers quotidiens du CHU dans un Lake local, les structure dans Bronze, les
nettoie et les fiabilise dans Silver, puis calcule dans Gold les indicateurs de
pilotage hospitalier et de recherche clinique.

```text
source-filestorage/ -> lake/ -> ClickHouse Bronze -> ClickHouse Silver -> ClickHouse Gold
```

## Arborescence

```text
sql/
|-- bronze.sql
|-- silver.sql
`-- gold.sql

scripts/
|-- copy_to_lake.py
|-- insert_to_bronze.py
|-- insert_to_silver.py
|-- insert_to_gold.py
|-- run_pipeline.py
`-- register_pipeline_task.ps1

docs/
|-- partie-1-interface-analyse.md
`-- partie-2-automatisation.md

tests/
`-- test_automation.py
```

Lors de la copie, les colonnes `nom`, `prenom` et `nir` sont supprimees des
fichiers `patients.csv`. Le `patient_id` est remplace par le meme HMAC-SHA-256
dans les fichiers patients et sejours afin de conserver les jointures sans
propager l'IPP source. La cle est directement dans le code uniquement pour cet
exercice ; en production, elle doit provenir d'un gestionnaire de secrets ou
d'une variable d'environnement. Les fichiers sources ne sont pas modifies.

## Prerequis

- Python 3.10 ou plus recent
- Docker

Toutes les commandes suivantes doivent etre executees depuis la racine du
projet.

## Setup

### 1. Creer l'environnement Python

```powershell
python -m venv .venv
```

### 2. Activer l'environnement Python

Sous Windows PowerShell :

```powershell
.\.venv\Scripts\Activate.ps1
```

Sous Linux ou macOS :

```bash
source .venv/bin/activate
```

Installer ensuite les dependances :

```console
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Alimenter le Lake

```console
python scripts/copy_to_lake.py
```

Apres une modification de la cle HMAC, reconstruire tout le Lake :

```console
python scripts/copy_to_lake.py --rebuild
```

Le script reproduit les partitions quotidiennes :

```text
source-filestorage/monitoring/2026-08-28/monitoring.parquet
lake/monitoring/2026-08-28/monitoring.parquet
```

Une partition munie du marqueur `_SUCCESS` est ignoree. Une partition portant
`_INCOMPLETE` est reconstruite avant la reprise du pipeline.

### 4. Demarrer ClickHouse

Le dossier local `lake/` doit etre monte directement dans le repertoire
`user_files` de ClickHouse :

```console
docker run -d --name clickhouse-bigdata -p 8123:8123 -p 9000:9000 -e CLICKHOUSE_USER=admin -e CLICKHOUSE_PASSWORD=clickhouse -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 --mount "type=volume,source=clickhouse-bigdata-data,target=/var/lib/clickhouse" --mount "type=bind,source=${PWD}/lake,target=/var/lib/clickhouse/user_files" clickhouse/clickhouse-server:latest
```

Verifier que ClickHouse repond :

```powershell
docker exec clickhouse-bigdata clickhouse-client --user admin --password clickhouse --query "SELECT version()"
```

### 5. Creer et alimenter Bronze

```console
python scripts/insert_to_bronze.py
```

Le script execute `sql/bronze.sql`, cree les tables si necessaire, charge uniquement
les fichiers dont le chemin n'est pas encore enregistre, puis affiche le nombre
de lignes par table.

### 6. Creer et alimenter Silver

```console
python scripts/insert_to_silver.py
```

Le script execute `sql/silver.sql`, deduplique les patients et les sejours, ecarte
les sejours dont la sortie precede l'admission, puis conserve uniquement les
releves monitoring dans les plages physiologiques attendues.

### 7. Creer et alimenter Gold

```console
python scripts/insert_to_gold.py
```

Le SQL recree les indicateurs Gold a partir des tables Silver nettoyees.

L'interface SQL ClickHouse est accessible sur :

```text
http://localhost:8123/play
```

Identifiants locaux par defaut :

```text
Utilisateur : admin
Mot de passe : clickhouse
```

### 8. Demarrer Metabase

```console
docker network create bigdata-network
docker network connect bigdata-network clickhouse-bigdata
docker compose -f docker-compose.metabase.yml up -d
```

Si le reseau existe deja, l'erreur de la premiere commande peut etre ignoree.
Le volume nomme `metabase-data` conserve les comptes, droits, questions et
dashboards lorsque le conteneur est supprime puis recree.

L'interface Metabase est accessible sur :

```text
http://localhost:3000
```

Les identifiants locaux sont disponibles dans `METABASE-CREDENTIALS.txt`.

### 9. Configurer les dashboards et le cloisonnement

Creer les deux comptes ClickHouse en lecture seule, puis provisionner Metabase :

```powershell
Get-Content -Raw sql/metabase_access.sql | docker exec -i clickhouse-bigdata clickhouse-client --user admin --password clickhouse --multiquery
python scripts/setup_metabase.py
python scripts/verify_metabase.py
```

Le provisionnement cree :

- le dashboard `Pilotage hospitalier` avec la DMS, les urgences, les
  readmissions et les alertes de constantes ;
- le dashboard `Recherche clinique` avec la prevalence et la description des
  cohortes par age et sexe ;
- les groupes, collections et comptes de demonstration separes ;
- deux connexions ClickHouse dont les comptes techniques ne peuvent lire que
  les tables Gold correspondant a leur usage.

Les cohortes et cellules de moins de 5 patients sont exclues par `gold.sql`.
Le groupe Pilotage ne peut pas ouvrir la collection Recherche, et inversement.

### 10. Recreer le conteneur Metabase

```console
docker compose -f docker-compose.metabase.yml down
docker compose -f docker-compose.metabase.yml up -d
```

Ne pas ajouter `-v` a `docker compose down`, car cette option supprimerait le
volume persistant et toute la configuration Metabase.

## Execution quotidienne

Apres le depot d'une nouvelle date dans `source-filestorage/` :

```console
python scripts/run_pipeline.py
```

L'orchestrateur execute dans l'ordre la collecte, Bronze, Silver et Gold. Il
protege les executions concurrentes, journalise chaque etape et conserve une
trace dans les tables `audit.pipeline_runs` et `audit.pipeline_stages`.

Pour installer une execution quotidienne a 02:00 dans le Planificateur de
taches Windows :

```powershell
powershell -ExecutionPolicy Bypass -File scripts/register_pipeline_task.ps1
```

La documentation complete de la Partie 2, avec les choix d'architecture et les
procedures de maintenance, est disponible dans
[`docs/partie-2-automatisation.md`](docs/partie-2-automatisation.md).

Le dossier de la Partie 1, avec le besoin, les sources, l'architecture, les
traitements, les KPI, les visualisations et les recommandations, est disponible
dans [`docs/partie-1-interface-analyse.md`](docs/partie-1-interface-analyse.md).

## Verification

Afficher le nombre de lignes des tables Bronze :

```powershell
docker exec clickhouse-bigdata clickhouse-client --user admin --password clickhouse --query "SELECT table, total_rows FROM system.tables WHERE database = 'bronze' ORDER BY table"
```

Afficher le nombre de lignes des tables Silver :

```powershell
docker exec clickhouse-bigdata clickhouse-client --user admin --password clickhouse --query "SELECT table, total_rows FROM system.tables WHERE database = 'silver' ORDER BY table"
```

Verifier les fichiers visibles par ClickHouse :

```powershell
docker exec clickhouse-bigdata sh -c "find /var/lib/clickhouse/user_files -maxdepth 3 -type f -print"
```

## Options des scripts

Afficher l'aide :

```console
python scripts/copy_to_lake.py --help
python scripts/insert_to_bronze.py --help
python scripts/insert_to_silver.py --help
python scripts/insert_to_gold.py --help
python scripts/run_pipeline.py --help
```

Utiliser des chemins personnalises :

```console
python scripts/copy_to_lake.py --source C:\chemin\source --destination C:\chemin\lake
python scripts/insert_to_bronze.py --lake C:\chemin\lake --sql sql/bronze.sql
```

Le chemin fourni a `--lake` doit correspondre au dossier monte dans
`/var/lib/clickhouse/user_files`.

## Reprise sur incident

La collecte marque une partition `_SUCCESS` uniquement lorsque tous ses fichiers
ont ete copies. Apres une erreur de collecte, relancer le pipeline complet :

```console
python scripts/run_pipeline.py
```

Pour reprendre directement une transformation deja alimentee en entree :

```console
python scripts/run_pipeline.py --start-at bronze
python scripts/run_pipeline.py --start-at silver
python scripts/run_pipeline.py --start-at gold
```

Consulter `logs/pipeline.log` et les tables `audit` avant de choisir l'etape de
reprise. Ne jamais supprimer `source-filestorage/`, qui constitue la source
fournie par le CHU.

## Depannage

### `CANNOT_EXTRACT_TABLE_STRUCTURE`

ClickHouse ne trouve aucun fichier correspondant au chemin donne a `file()`.
Avec le montage documente ici, les chemins SQL sont relatifs a `user_files` :

```sql
FROM file('monitoring/*/monitoring.parquet', 'Parquet')
```

Il ne faut pas utiliser `lake/monitoring/...`, car `lake/` est deja le dossier
monte sur `/var/lib/clickhouse/user_files`.

### Connexion refusee sur le port 8123

Verifier le conteneur et ses journaux :

```powershell
docker ps -a --filter "name=clickhouse-bigdata"
docker logs clickhouse-bigdata
```

### Reinitialisation complete de ClickHouse

Les commandes suivantes suppriment le conteneur et toutes les donnees
ClickHouse locales :

```powershell
docker rm -f clickhouse-bigdata
docker volume rm clickhouse-bigdata-data
```

Relancer ensuite les etapes 4 a 7 du setup.
