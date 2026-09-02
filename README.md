# Entrepot de donnees de sante du CHU

Ce projet collecte les fichiers quotidiens du CHU dans un Lake local, puis les
charge dans des tables Bronze ClickHouse.

```text
source-filestorage/ -> lake/ -> ClickHouse Bronze -> ClickHouse Silver
```

Lors de la copie, les colonnes `nom`, `prenom` et `nir` sont supprimees des
fichiers `patients.csv` presents dans le Lake. Les fichiers sources ne sont pas
modifies.

## Prerequis

- Python 3.10 ou plus recent
- Docker Desktop demarre
- PowerShell

Toutes les commandes suivantes doivent etre executees depuis la racine du
projet.

## Premier setup

### 1. Creer l'environnement Python

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Il est aussi possible d'activer l'environnement :

```powershell
.\.venv\Scripts\Activate.ps1
```

### 2. Alimenter le Lake

```powershell
.\.venv\Scripts\python.exe .\copy_to_lake.py
```

Le script reproduit les partitions quotidiennes :

```text
source-filestorage/monitoring/2026-08-28/monitoring.parquet
lake/monitoring/2026-08-28/monitoring.parquet
```

Une partition `lake/<dataset>/<AAAA-MM-JJ>/` deja presente est ignoree. Une
nouvelle date est copiee integralement.

### 3. Demarrer ClickHouse

Le dossier local `lake/` doit etre monte directement dans le repertoire
`user_files` de ClickHouse :

```powershell
$lakePath = (Resolve-Path ".\lake").Path

docker run -d `
  --name clickhouse-bigdata `
  -p 8123:8123 `
  -p 9000:9000 `
  -e CLICKHOUSE_USER=admin `
  -e CLICKHOUSE_PASSWORD=clickhouse `
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 `
  --mount "type=volume,source=clickhouse-bigdata-data,target=/var/lib/clickhouse" `
  --mount "type=bind,source=$lakePath,target=/var/lib/clickhouse/user_files" `
  clickhouse/clickhouse-server:latest
```

Verifier que ClickHouse repond :

```powershell
docker exec clickhouse-bigdata clickhouse-client --user admin --password clickhouse --query "SELECT version()"
```

Si le conteneur existe deja mais est arrete :

```powershell
docker start clickhouse-bigdata
```

### 4. Creer et alimenter Bronze

```powershell
.\.venv\Scripts\python.exe .\insert_to_bronze.py
```

Le script execute `bronze.sql`, cree les tables si necessaire, charge uniquement
les fichiers dont le chemin n'est pas encore enregistre, puis affiche le nombre
de lignes par table.

### 5. Creer et alimenter Silver

```powershell
.\.venv\Scripts\python.exe .\insert_to_silver.py
```

Le script execute `silver.sql`, deduplique les patients et les sejours, ecarte
les sejours dont la sortie precede l'admission, puis conserve uniquement les
releves monitoring dans les plages physiologiques attendues.

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

```powershell
.\.venv\Scripts\python.exe .\copy_to_lake.py
.\.venv\Scripts\python.exe .\insert_to_bronze.py
.\.venv\Scripts\python.exe .\insert_to_silver.py
```

La premiere commande copie uniquement les nouvelles partitions de dates. La
seconde ignore les fichiers deja charges dans Bronze grace a leur `source_file`.
La troisieme insere uniquement les nouvelles cles valides dans Silver.

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

```powershell
.\.venv\Scripts\python.exe .\copy_to_lake.py --help
.\.venv\Scripts\python.exe .\insert_to_bronze.py --help
.\.venv\Scripts\python.exe .\insert_to_silver.py --help
```

Utiliser des chemins personnalises :

```powershell
.\.venv\Scripts\python.exe .\copy_to_lake.py --source C:\chemin\source --destination C:\chemin\lake
.\.venv\Scripts\python.exe .\insert_to_bronze.py --lake C:\chemin\lake --sql .\bronze.sql
```

Le chemin fourni a `--lake` doit correspondre au dossier monte dans
`/var/lib/clickhouse/user_files`.

## Reprise sur incident

Si une copie a ete interrompue, une partition de date peut exister sans contenir
tous ses fichiers. Supprimer uniquement cette partition incomplete dans le Lake,
puis relancer `copy_to_lake.py`.

Exemple :

```powershell
Remove-Item -Recurse -Force .\lake\monitoring\2026-08-29
.\.venv\Scripts\python.exe .\copy_to_lake.py
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

Relancer ensuite les etapes 3 et 4 du premier setup.

## Donnees sensibles

Le dossier `lake/` est genere localement et ignore par Git. Les colonnes
directement identifiantes `nom`, `prenom` et `nir` n'y sont pas copiees. Le
champ `patient_id` reste cependant un identifiant interne et doit etre protege
par des droits d'acces adaptes.
