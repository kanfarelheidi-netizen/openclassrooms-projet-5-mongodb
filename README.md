# Projet 5 Migration CSV vers MongoDB

## Mission et livrables

Les sources scolaires et les consignes viennent du site de l'école OpenClassrooms, projet « Maintenez et documentez un système de stockage des données sécurisé et performant ».

Les livrables demandés sont le lien GitHub des scripts de migration, ce README, le fichier docker-compose.yml et la présentation PowerPoint. Le dépôt contient uniquement ces éléments et leurs dépendances d'exécution. Les cours, les discours personnels, les archives de revue et les rapports internes n'en font pas partie. Le dataset, les mots de passe, les sauvegardes et les chemins personnels sont exclus.

## Quel script fait la migration

`P05_Migration.py` est le script principal. Il importe `P05_Profilage_source.py` pour contrôler le CSV. `P05_Generer_secrets.py` prépare les trois mots de passe locaux sans les afficher ni remplacer ceux qui existent. `mongo-init/01-users.js` initialise les comptes et les index. Le Dockerfile construit l'image Python et requirements.txt fixe la version de PyMongo.

`source_rows` lit chaque ligne avec le module csv. `build_document` crée un dictionnaire Python avec les champs typés. PyMongo le sérialise en BSON pour MongoDB. `migrate` envoie des lots de 500 opérations ReplaceOne avec upsert. `verify` recompte puis compare chaque document avec la transformation de la ligne source.

## Contrat de migration

Source approuvée : 55 500 lignes, 15 colonnes, empreinte SHA-256 d8c23c7dddaf0e1f5daf26182eb908c219d28a63bb574377c3ba6cb09be8a342. Le précontrôle refuse une autre source. Les 534 lignes supplémentaires identiques et les 108 montants négatifs sont conservés : aucune règle métier ne permet de les supprimer.

Une ligne source donne un document dans `p05_medical.admissions`. L'identifiant technique est le SHA-256 de l'empreinte source et du numéro de ligne. Une relance de la même source remplace les documents portant le même identifiant, sans en ajouter. Cette garantie ne transforme pas le script en importateur universel pour des CSV modifiés.

## Schéma documentaire

| Colonne source | Champ MongoDB | Type |
| --- | --- | --- |
| Name | patient_name | texte |
| Age | age | entier |
| Gender | gender | texte |
| Blood Type | blood_type | texte |
| Medical Condition | medical_condition | texte |
| Date of Admission | admission_date | texte ISO AAAA-MM-JJ |
| Doctor | doctor | texte |
| Hospital | hospital | texte |
| Insurance Provider | insurance_provider | texte |
| Billing Amount | billing_amount | Decimal128 |
| Room Number | room_number | entier |
| Admission Type | admission_type | texte |
| Discharge Date | discharge_date | texte ISO AAAA-MM-JJ |
| Medication | medication | texte |
| Test Results | test_results | texte |

Trois champs techniques complètent ces quinze champs : `_id` (texte, clé primaire), `source_sha256` (texte, traçabilité) et `source_row` (entier, position source). Les dates restent des dates sans heure ni fuseau inventé. Le montant n'est pas arrondi. MongoDB autorise des schémas souples : le contrat est imposé ici par le script Python, pas par un validateur JSON Schema sur la collection.

## Installation avec Docker Desktop

MongoDB n'est pas installé manuellement dans Windows. Compose télécharge l'image officielle `mongo:8.0.30-noble`, qui contient déjà le serveur. Le conteneur exécute ce logiciel. L'image du migrateur part de `python:3.12.14-slim-trixie`, installe PyMongo et copie les deux scripts Python nécessaires.

1. Ouvrir Docker Desktop et attendre que le moteur soit démarré en mode conteneurs Linux.
2. Télécharger le dépôt avec Code puis Download ZIP, puis l'extraire dans un dossier de travail.
3. Créer un sous-dossier `data` et y placer le CSV officiel sous le nom `healthcare_dataset.csv`. Ce fichier reste local et ne doit pas être envoyé à GitHub.
4. Ouvrir le terminal intégré de Docker Desktop, au niveau de l'hôte, et se placer dans le dossier contenant docker-compose.yml. Ce terminal n'est pas l'onglet Exec d'un conteneur. Le dossier choisi dépend du poste et n'est pas reproduit ici.
5. Exécuter les commandes ci-dessous une par une. Elles ne contiennent aucune syntaxe PowerShell.

```text
docker build -t p05-migrator .
docker run --rm --mount type=bind,source=.,target=/work --workdir /work python:3.12.14-slim-trixie python P05_Generer_secrets.py
docker compose config --quiet
docker compose up -d mongo
docker compose up --build --no-deps --abort-on-container-exit --exit-code-from migrator migrator
```

Entre les deux dernières commandes, attendre dans Docker Desktop que `mongo` soit healthy. Si le montage `source=.` n'est pas accepté par la version du moteur, utiliser dans cette commande le chemin du dossier de travail choisi, sans le publier. La génération ne remplace aucun secret existant. Ne jamais régénérer des mots de passe pour réparer arbitrairement un volume déjà initialisé.

Le rapport est créé dans `out/P05_Controle_migration_reelle.json`. Il doit contenir `verified_documents: 55500` et `target_verification: passed`. Le conteneur migrator doit terminer avec le code 0. Un fichier de rapport ancien ne suffit pas : vérifier sa date de modification et les journaux de l'exécution.

## Démonstration depuis l'interface Docker Desktop

1. Dans Containers, développer le groupe `p05-medical`.
2. Démarrer `mongo` si nécessaire et attendre healthy. Il reste actif pour servir les requêtes.
3. Ouvrir `migrator`, puis Logs. Le statut Exited (0) est normal : l'import est une tâche finie.
4. Cliquer sur Start du seul migrator. Attendre la fin et vérifier les nouveaux journaux puis le rapport dans le dossier out.
5. Refaire Start une deuxième fois. Le rapport doit toujours indiquer 55 500 documents vérifiés. Ne pas supprimer le volume pour démontrer la relance.
6. Pour compter directement, ouvrir le conteneur mongo puis Exec et saisir la commande suivante. Elle lit le mot de passe monté sans l'afficher.

```sh
mongosh --quiet --username p05_reader --password "$(cat /run/secrets/mongo_reader_password)" --authenticationDatabase p05_medical p05_medical --eval 'db.admissions.countDocuments({})'
```

Résultat attendu : `55500`. Cette commande utilise le shell Linux du conteneur MongoDB. Ne pas la saisir dans le terminal de l'hôte. Pour afficher les noms des index, remplacer l'expression après `--eval` par `'db.admissions.getIndexes().map(i => i.name)'`.

## Paramètres Compose et sécurité

`mongo` et `migrator` sont deux services distincts. Le réseau `p05_internal` permet au migrateur de joindre l'hôte `mongo` sur le port 27017. Son nom ne signifie pas que Docker a activé l'option réseau `internal: true` : cette option n'est pas configurée. Le port Windows est limité à 127.0.0.1.

Le volume nommé `mongo_data` conserve la base dans `/data/db`. Le montage du CSV expose un fichier local à `/data/healthcare_dataset.csv` en lecture seule. Le montage `./out:/reports` conserve les contrôles hors du conteneur. Le système de fichiers du migrateur est en lecture seule, avec `/tmp` temporaire en mémoire et `/reports` accessible en écriture. `depends_on` attend la santé du serveur lors d'un lancement Compose avec ses dépendances ; un démarrage isolé depuis l'interface nécessite de vérifier mongo soi-même.

Les trois secrets sont des fichiers locaux montés sous `/run/secrets`. Le compte `p05_admin` administre le serveur, `p05_ingest` possède readWrite sur p05_medical et `p05_reader` possède read sur cette base. La lecture seule ne masque pas les données nominatives. L'initialisation JavaScript s'exécute uniquement sur un volume neuf. Les quatre index sont `_id_`, `ux_source_row`, `ix_admission_date` et `ix_condition_admission`.

Les contrôles du 20 septembre 2026 ont démontré la migration et la relance sur MongoDB réel, les refus d'accès, l'export et la restauration applicative dans une base temporaire du même serveur. Ce sont des preuves locales historiques, pas un test de charge ni une certification. Le dépôt minimal ne distribue pas les outils internes de sauvegarde ni les rapports de travail.

Pour arrêter : utiliser Stop dans Docker Desktop. Ne pas supprimer le volume et ne pas employer `docker compose down -v`. Le volume n'est ni une sauvegarde hors poste, ni une solution de haute disponibilité. TLS, la rotation des secrets, les sauvegardes externes et un plan de reprise complet restent à concevoir avant une exploitation distante.

## Recherche AWS

Cette recherche fait partie du travail à présenter. Aucun compte AWS n'a été créé pour le projet et aucun service n'a été déployé. L'étude distingue les options suivantes.

| Service | Utilité | Limite et décision |
| --- | --- | --- |
| S3 | Stocker fichiers sources et sauvegardes comme objets | Ce n'est pas un moteur MongoDB. Accès privé, chiffrement et durée de conservation à définir. |
| RDS | Exploiter une base relationnelle gérée | Aucun moteur MongoDB. L'expression « RDS pour MongoDB » dans la consigne est inexacte. |
| DocumentDB | Base documentaire gérée compatible avec des API MongoDB | Tester types, commandes, index et comportement du pilote. Ce n'est pas MongoDB identique. |
| ECS | Orchestrer des conteneurs, sur EC2 ou Fargate | La persistance de la base exige une conception distincte de la tâche d'import. |

### Compte et tarification

La procédure envisagée commence par l'inscription officielle AWS, la vérification de l'adresse électronique, les renseignements de compte, la vérification d'identité et les éléments de facturation demandés. Ces opérations restent à la charge du propriétaire du compte. Après activation : activer MFA pour root, utiliser une identité dédiée au travail courant et créer des alertes de budget. Une alerte de budget n'arrête pas automatiquement les dépenses.

Le coût dépend de la région, de la durée, de la capacité et du trafic. DocumentDB facture selon sa configuration le calcul, le stockage, les entrées-sorties et les sauvegardes. S3 facture notamment les Go-mois, les requêtes et certains transferts. Fargate facture les ressources demandées pendant la durée d'exécution. Ajouter ECR, CloudWatch, Secrets Manager, KMS et les éventuels coûts réseau. Aucun prix mensuel fiable n'est calculable sans hypothèses de dimensionnement.

Pour établir un devis : choisir une région, estimer stockage initial et croissance, nombre d'instances, vCPU et mémoire de l'import, heures mensuelles, trafic et rétention ; saisir ces hypothèses dans AWS Pricing Calculator ; comparer les solutions et dater l'estimation. Ne pas supposer une gratuité permanente.

### MongoDB dans un conteneur ECS

Scénario documentaire : construire l'image d'import et l'envoyer dans ECR ; créer un cluster ECS avec capacité EC2 ; préparer le stockage EBS durable sur les hôtes ; définir une tâche MongoDB avec montage du stockage et secrets ; autoriser le réseau privé entre tâches ; lancer la tâche d'import séparée ; comparer les comptes et contenus ; tester arrêt, redémarrage et restauration. Un montage lié à un hôte nécessite une stratégie de placement et de reprise après perte de cet hôte. Une seule tâche ne crée pas un replica set ni du sharding.

Alternative : ECS exécute seulement l'import et DocumentDB gère la base. Avant de la retenir, tester ReplaceOne/upsert, Decimal128, les index et les options de connexion avec TLS sur un jeu non sensible. Cette architecture nécessite une adaptation : le Compose local n'est pas un fichier de déploiement ECS prêt à l'emploi.

### Sauvegardes et surveillance

DocumentDB permet une rétention des sauvegardes automatiques de 1 à 35 jours et des snapshots manuels. Une restauration crée une cible à contrôler ; ne pas assimiler l'existence d'une sauvegarde à un test de reprise. Pour MongoDB autogéré sur ECS, l'équipe doit organiser une sauvegarde cohérente, son chiffrement, son stockage externe et les essais de restauration.

Avec CloudWatch : suivre CPU, mémoire disponible, connexions, stockage et latences ; définir seuils, durée de dépassement, destinataire et procédure de traitement. Fixer avec le client la perte de données acceptable et le délai de reprise. La recommandation reste conditionnelle : comparer l'effort d'exploitation de MongoDB autogéré à la compatibilité et au coût de DocumentDB, sans transfert de données médicales pendant cette étude.

## Références

OPENCLASSROOMS, s. d. Projet 5 Maintenez et documentez un système de stockage des données sécurisé et performant. Site de l'école OpenClassrooms, consignes consultées le 22 septembre 2026.

DOCKER, s. d. Explore the Containers view in Docker Desktop. https://docs.docker.com/desktop/use-desktop/container/ (consulté le 22 septembre 2026).

AMAZON WEB SERVICES, s. d. Sign up for AWS. https://docs.aws.amazon.com/accounts/latest/reference/getting-started.html (consulté le 22 septembre 2026).

AMAZON WEB SERVICES, s. d. What is Amazon RDS. https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Welcome.html (consulté le 22 septembre 2026).

AMAZON WEB SERVICES, s. d. Amazon DocumentDB compatibility with MongoDB. https://docs.aws.amazon.com/documentdb/latest/devguide/compatibility.html (consulté le 22 septembre 2026).

AMAZON WEB SERVICES, s. d. Use bind mounts with Amazon ECS. https://docs.aws.amazon.com/AmazonECS/latest/developerguide/bind-mounts.html (consulté le 22 septembre 2026).

AMAZON WEB SERVICES, s. d. Backing up and restoring in Amazon DocumentDB. https://docs.aws.amazon.com/documentdb/latest/devguide/backup_restore.html (consulté le 22 septembre 2026).

AMAZON WEB SERVICES, s. d. Monitoring Amazon DocumentDB with CloudWatch. https://docs.aws.amazon.com/documentdb/latest/devguide/cloud_watch.html (consulté le 22 septembre 2026).

AMAZON WEB SERVICES, s. d. Tarifications DocumentDB, S3 et Fargate. https://aws.amazon.com/documentdb/pricing/ ; https://aws.amazon.com/s3/pricing/ ; https://aws.amazon.com/fargate/pricing/ (consultés le 22 septembre 2026).
