# Partie 2 - Automatisation du pipeline

## Objectif

Le pipeline automatise les quatre etapes suivantes dans un ordre strict :

```text
source-filestorage
        |
        v
collecte vers le Lake
        |
        v
Bronze -> Silver -> Gold
```

Le point d'entree unique est `scripts/run_pipeline.py`. Les transformations
restent executees en SQL dans ClickHouse. Python ne charge pas les donnees
metier en memoire : il copie les fichiers, envoie les requetes et controle leur
execution.

## Prerequis

- Docker Desktop et le conteneur `clickhouse-bigdata` doivent etre actifs ;
- le dossier local `lake` doit etre monte directement sur
  `/var/lib/clickhouse/user_files` ;
- l'environnement `.venv` doit contenir les dependances de `requirements.txt`.

Le montage `/var/lib/clickhouse/user_files/lake` ajoute un niveau de repertoire
et n'est pas compatible avec les chemins relatifs utilises par les fichiers SQL.
La commande Docker correcte est documentee dans le `README.md`.

## Fonctionnement

### Collecte atomique

Chaque partition `lake/<dataset>/<AAAA-MM-JJ>/` porte un marqueur
`_INCOMPLETE` pendant sa construction. Ce marqueur est remplace par `_SUCCESS`
uniquement apres la copie complete de tous ses fichiers. Cette methode reste
fiable lorsque Docker Desktop observe simultanement le dossier monte.

Si la copie echoue :

- la partition incomplete est supprimee quand Windows le permet ;
- sinon son marqueur `_INCOMPLETE` reste present et provoque sa reconstruction
  a l'execution suivante ;
- le pipeline s'arrete avec un code de retour non nul ;
- l'execution suivante peut reprendre normalement.

Pour `patients.csv`, les colonnes `nir`, `nom` et `prenom` sont retirees avant
la publication de la partition dans le Lake.

### Transformations

- Bronze charge uniquement les nouveaux chemins `source_file`.
- Silver insere uniquement les nouvelles cles valides et applique les controles
  de qualite.
- Gold recalcule les agregats avec `TRUNCATE` puis `INSERT`. Ces tables sont
  petites et leur reconstruction evite de melanger deux versions d'un meme KPI.

En cas d'echec, aucune etape suivante n'est lancee.

### Protection contre les doubles executions

Le fichier `logs/pipeline.lock` utilise un verrou gere par le systeme
d'exploitation. Une seconde execution quitte avec le code `2` si un pipeline
est deja actif. Le verrou est libere automatiquement, y compris quand le
processus est interrompu.

Le Planificateur de taches applique aussi la regle `IgnoreNew` pour ne pas
demarrer une seconde instance.

## Lancement

### Execution complete

Depuis la racine du projet :

```console
python scripts/run_pipeline.py
```

Ordre execute :

```text
collecte -> bronze -> silver -> gold
```

Codes de retour :

| Code | Signification |
|---:|---|
| `0` | Toutes les etapes ont reussi |
| `1` | Une etape ou la connexion ClickHouse a echoue |
| `2` | Un autre pipeline est deja en cours |

### Reprise a partir d'une etape

```console
python scripts/run_pipeline.py --start-at bronze
python scripts/run_pipeline.py --start-at silver
python scripts/run_pipeline.py --start-at gold
```

Une reprise cree un nouveau `run_id`. Les etapes deja terminees ne sont pas
relancees lorsque `--start-at` les exclut.

### Parametres ClickHouse

Les valeurs locales par defaut sont :

```text
host     localhost
port     8123
user     admin
password clickhouse
```

Elles peuvent etre surchargees sans modifier le code :

```powershell
$env:CLICKHOUSE_HOST = "localhost"
$env:CLICKHOUSE_PORT = "8123"
$env:CLICKHOUSE_USER = "admin"
$env:CLICKHOUSE_PASSWORD = "clickhouse"
python scripts/run_pipeline.py
```

Le mot de passe n'est jamais ecrit dans les journaux ni dans les tables
d'audit.

Le pipeline tente la connexion six fois, avec dix secondes entre les essais.
Ces valeurs sont configurables avec `--connection-attempts` et
`--connection-delay`.

## Planification Windows

La commande suivante enregistre une execution quotidienne a `02:00` :

```powershell
powershell -ExecutionPolicy Bypass -File scripts/register_pipeline_task.ps1
```

Pour choisir une autre heure :

```powershell
powershell -ExecutionPolicy Bypass -File scripts/register_pipeline_task.ps1 `
  -DailyAt "01:30"
```

Le script configure :

- le rattrapage d'un lancement manque avec `StartWhenAvailable` ;
- trois nouvelles tentatives espacees de cinq minutes ;
- une limite d'execution de quatre heures ;
- l'autorisation de fonctionner sur batterie ;
- le refus des executions concurrentes.

La tache utilise le Python de `.venv` et le repertoire du projet comme dossier
de travail. Elle s'execute dans la session interactive de l'utilisateur, ce qui
est coherent avec Docker Desktop. Docker Desktop doit etre configure pour se
lancer avec Windows.

Verifier la tache :

```powershell
Get-ScheduledTask -TaskName "CHU Big Data Pipeline"
Get-ScheduledTaskInfo -TaskName "CHU Big Data Pipeline"
```

Declencher un test manuel :

```powershell
Start-ScheduledTask -TaskName "CHU Big Data Pipeline"
```

Supprimer uniquement la planification :

```powershell
Unregister-ScheduledTask -TaskName "CHU Big Data Pipeline"
```

Cette derniere commande ne supprime ni le Lake ni les bases ClickHouse.

## Journalisation

Les messages sont envoyes simultanement dans la console et dans :

```text
logs/pipeline.log
```

Les horodatages sont en UTC. Chaque ligne contient le niveau, le `run_id` et
l'etape. Le fichier est limite a 5 Mio et cinq anciennes versions sont
conservees.

Exemple :

```text
2026-09-03T00:00:04Z level=INFO run_id=... stage=silver Stage completed ...
```

Afficher les dernieres lignes :

```powershell
Get-Content logs\pipeline.log -Tail 100
```

## Tracabilite ClickHouse

L'orchestrateur cree deux tables :

- `audit.pipeline_runs` : etat global, debut, fin, etape de reprise, erreur et
  compteurs de collecte ;
- `audit.pipeline_stages` : debut, fin, statut et details de chaque etape.

Les tables utilisent `ReplacingMergeTree`. Le mot-cle `FINAL` retourne la
derniere version de chaque execution ou etape.

Dernieres executions :

```sql
SELECT
    run_id,
    started_at,
    finished_at,
    status,
    start_stage,
    failed_stage,
    copied_files,
    skipped_partitions,
    error_message
FROM audit.pipeline_runs FINAL
ORDER BY started_at DESC
LIMIT 20;
```

Detail d'une execution :

```sql
SELECT
    stage,
    started_at,
    finished_at,
    status,
    details
FROM audit.pipeline_stages FINAL
WHERE run_id = '<run_id>'
ORDER BY started_at;
```

Une execution qui reste en statut `running` apres l'arret du processus indique
une interruption brutale : arret de Windows, terminaison forcee ou panne du
moteur avant l'enregistrement final.

## Reprise sur incident

### ClickHouse indisponible

1. Verifier Docker Desktop.
2. Verifier le conteneur avec `docker ps -a`.
3. Lire `docker logs clickhouse-bigdata`.
4. Demarrer le conteneur avec `docker start clickhouse-bigdata`.
5. Relancer le pipeline complet.

Une panne de connexion est toujours presente dans `logs/pipeline.log`, meme si
ClickHouse et ses tables d'audit sont indisponibles.

### Echec de collecte

Relancer le pipeline complet :

```console
python scripts/run_pipeline.py
```

Les marqueurs garantissent qu'aucune partition partielle n'est prise pour une
partition terminee.

### Echec Bronze

```console
python scripts/run_pipeline.py --start-at bronze
```

Les chemins deja presents dans `source_file` sont ignores, ce qui rend la
reprise sans doublon pour un depot quotidien immuable.

### Echec Silver

```console
python scripts/run_pipeline.py --start-at silver
```

Les clauses de non-existence des tables Silver empechent de reinserer les cles
deja traitees.

### Echec Gold

```console
python scripts/run_pipeline.py --start-at gold
```

Gold est reconstruit integralement a partir de Silver. Une nouvelle execution
remplace donc un calcul interrompu.

### Correction d'un fichier historique

Le flux normal considere qu'un chemin date est immuable. Une correction sous le
meme chemin demande une intervention controlee : suspendre la planification,
sauvegarder la base, retirer la partition concernee du Lake et de Bronze, puis
reconstruire les couches dependantes. Cette operation ne doit pas etre
automatisee car elle modifie un historique deja publie.

## Maintenance

Controle hebdomadaire :

1. Verifier le dernier statut dans `audit.pipeline_runs FINAL`.
2. Rechercher les etapes `failed` ou anormalement longues.
3. Controler l'espace du volume Docker et du dossier `lake`.
4. Verifier la rotation des journaux.
5. Executer les tests apres toute modification du pipeline.

Commande de test :

```console
python -m unittest discover -s tests -v
```

## Justification des choix

### Python comme orchestrateur, SQL comme moteur de transformation

Python est adapte a la copie de fichiers, au controle d'execution et a la
gestion des erreurs. Les volumes metier ne sont jamais transformes avec pandas :
Bronze, Silver et Gold restent dans ClickHouse, ce qui respecte le principe de
passage a l'echelle du sujet.

### Pipeline sequentiel

Silver depend de Bronze et Gold depend de Silver. L'execution sequentielle rend
la dependance explicite et empeche de publier des indicateurs calcules sur une
couche incomplete.

### Incremental en entree, reconstruction des agregats

Le Lake et Bronze sont incrementaux pour eviter de retraiter les fichiers
volumineux. Silver protege ses cles contre la reinsertion. Les tables Gold sont
petites : leur reconstruction complete est plus simple a verifier et evite les
erreurs de mise a jour d'agregats.

### Deux niveaux de traces

Le journal local couvre les pannes de ClickHouse. Les tables `audit` permettent
des recherches SQL, le suivi des durees et la comparaison des executions. Un
seul support ne couvrirait pas ces deux usages.

### Planificateur Windows natif

Le projet tourne sur un laptop Windows avec Docker Desktop. Le Planificateur de
taches ne demande aucun service d'orchestration supplementaire et fournit deja
le rattrapage, les nouvelles tentatives et la politique d'instances multiples.
Pour un deploiement serveur ou multi-machine, Airflow ou Dagster deviendrait
pertinent, mais ajouter cette infrastructure ici serait disproportionne.

### Limites connues

- ClickHouse ne fournit pas de transaction unique couvrant toutes les tables
  Gold. Une erreur se reprend en relancant Gold.
- La tache Windows interactive suppose une session utilisateur ouverte et
  Docker Desktop actif.
- La politique de chemin immuable ne recharge pas automatiquement une source
  historique corrigee sous le meme nom.
- Les journaux locaux doivent etre collectes par un outil centralise si le
  pipeline passe en production sur plusieurs machines.
