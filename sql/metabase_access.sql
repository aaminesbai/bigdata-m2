-- Comptes techniques en lecture seule utilises par les deux connexions Metabase.
CREATE USER IF NOT EXISTS metabase_pilotage
IDENTIFIED WITH sha256_password BY 'PilotageDb2026!';

CREATE USER IF NOT EXISTS metabase_recherche
IDENTIFIED WITH sha256_password BY 'RechercheDb2026!';

GRANT SELECT ON gold.dms_par_service TO metabase_pilotage;
GRANT SELECT ON gold.activite_urgences_par_jour TO metabase_pilotage;
GRANT SELECT ON gold.taux_readmission_30_jours TO metabase_pilotage;
GRANT SELECT ON gold.alertes_constantes_par_jour TO metabase_pilotage;

GRANT SELECT ON gold.prevalence_par_pathologie TO metabase_recherche;
GRANT SELECT ON gold.distribution_cohorte_age_sexe TO metabase_recherche;
