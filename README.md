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
`-- insert_to_gold.py
```

Lors de la copie, les colonnes `nom`, `prenom` et `nir` sont supprimees des
fichiers `patients.csv` presents dans le Lake. Les fichiers sources ne sont pas
modifies.

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

Le script reproduit les partitions quotidiennes :

```text
source-filestorage/monitoring/2026-08-28/monitoring.parquet
lake/monitoring/2026-08-28/monitoring.parquet
```

Une partition `lake/<dataset>/<AAAA-MM-JJ>/` deja presente est ignoree. Une
nouvelle date est copiee integralement.

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

## Execution quotidienne

Apres le depot d'une nouvelle date dans `source-filestorage/` :

```console
python scripts/copy_to_lake.py
python scripts/insert_to_bronze.py
python scripts/insert_to_silver.py
python scripts/insert_to_gold.py
```

La premiere commande copie uniquement les nouvelles partitions de dates. La
seconde ignore les fichiers deja charges dans Bronze grace a leur `source_file`.
La troisieme insere uniquement les nouvelles cles valides dans Silver. La
derniere recalcule les indicateurs Gold.

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
```

Utiliser des chemins personnalises :

```console
python scripts/copy_to_lake.py --source C:\chemin\source --destination C:\chemin\lake
python scripts/insert_to_bronze.py --lake C:\chemin\lake --sql sql/bronze.sql
```

Le chemin fourni a `--lake` doit correspondre au dossier monte dans
`/var/lib/clickhouse/user_files`.

## Reprise sur incident

Si une copie a ete interrompue, une partition de date peut exister sans contenir
tous ses fichiers. Supprimer uniquement cette partition incomplete dans le Lake,
puis relancer `scripts/copy_to_lake.py`.

Exemple :

```powershell
Remove-Item -Recurse -Force .\lake\monitoring\2026-08-29
python scripts/copy_to_lake.py
```

Ne jamais supprimer `source-filestorage/`, qui constitue la source fournie par
le CHU.

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
