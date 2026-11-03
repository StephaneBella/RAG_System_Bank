# US 07 — Cartographie des données sensibles (T1)

Inventaire des données sensibles **réellement présentes** dans RAG_System_Bank,
de leur emplacement, de leur classification et du traitement exigé avant toute
transmission à un fournisseur de LLM. Ce document est la base des tâches US 07
suivantes : T2 (politique de minimisation), T3 (anonymisation /
pseudonymisation), T4 (minimisation du contexte RAG) et T5 (passerelle LLM
interne).

> **État à la date de rédaction (2026-11-02) : aucun LLM n'est intégré au
> projet.** Aucun prompt n'est construit et aucun chunk n'est transmis à un
> fournisseur de LLM. Les traitements « avant LLM » décrits ci-dessous sont des
> **exigences** pour T3 à T5, pas des protections en place.

## 1. Périmètre et sources

### 1.1 Base documentaire reprise

Ce document reprend comme base la section 6 « US 07 – Cartographie des données
sensibles » du brouillon `docs/SPRINT2_SSI.md` de la branche
`myyr_feat/sprint2-ssi` (commit `0adb97e`, auteur : myyrcorp). Le document
original n'est pas modifié et la branche n'est pas fusionnée. Chaque catégorie
du brouillon a été confrontée au code et au corpus actuels :

| Catégorie du brouillon | Constat dans le code / le corpus | Traitement dans ce document |
|---|---|---|
| Identité des clients | Absente du corpus et de la base | Conservée comme catégorie **prospective** (§ 5) |
| Comptes et paiement | Absente (le mot « RIB » n'apparaît que comme terme de procédure) | Prospective (§ 5) |
| Identifiants clients, numéros de dossier | Absents (seules des références de procédures existent : `CONF-LBC-001`, `DG-STRAT-2026-01`) | Prospective (§ 5) |
| Coordonnées | Absentes du corpus ; les **emails des employés** existent en base et dans le journal | Reformulée : § 4.1 et § 4.2 |
| Données RH | Aucune table ni aucun document RH | Prospective (§ 5) |
| Conformité LBC-FT | Présente sous forme de **procédure interne fictive**, sans alerte ni déclaration de soupçon réelle | Reformulée et nuancée : § 4.4.2 |
| Autre couche métier | Étiquette `business_layer` présente, **aucun filtrage appliqué** | § 4.4 et § 4.6 |
| Secrets techniques | Présents, liste du brouillon incomplète | Complétée : § 4.3 |
| Métadonnées (département, auteur, `file_path`) | Aucun champ « auteur » n'existe ; `file_path` exclu de la vue LLM mais stocké dans Qdrant | Corrigée : § 4.5 et § 4.6 |

### 1.2 Méthode

- Lecture du code de l'arbre de travail de la branche
  `temgoua_feat/auth-user-access-audit`. Les numéros de ligne renvoient à cet
  état. Certains fichiers cités portent des modifications **non encore
  commitées** (journal d'audit SEC-01 T1/T2/T5) ; leurs numéros de ligne
  peuvent évoluer au commit.
- Extraction du texte des 4 PDF privés (`pypdf`) et recherche par motifs
  (IBAN, emails, téléphones, montants, noms de personnes, mentions de
  classification). Cette recherche est indicative : elle ne garantit pas
  l'absence de toute donnée personnelle.
- Les chemins sont relatifs à la racine du dépôt.

## 2. Échelle de classification

| Niveau | Définition | Exemples dans le projet |
|---|---|---|
| **C0 Public** | Information publiée ou destinée à l'être | Règlements COBAC, rapport annuel COBAC (PDF `Public__*`) |
| **C1 Interne** | Usage interne, préjudice limité en cas de divulgation | Chemin relatif d'un document, liste des départements |
| **C2 Confidentiel** | Diffusion limitée à des fonctions identifiées ; préjudice réel en cas de divulgation | Procédures internes Crédit, Front Office, LBC-FT ; emails et IP des employés ; journal d'audit |
| **C3 Secret / Restreint** | Accès nominatif ou réservé à une direction ; préjudice grave ; ne doit jamais quitter le système | Secrets techniques, jetons, note stratégique « RESTREINT DIRECTION » |

La classification d'un document du corpus est une **proposition** fondée sur
son contenu et sur la mention qu'il porte lui-même. Elle doit être confirmée
par le propriétaire métier (§ 8).

Vocabulaire des traitements avant LLM :

- **Exclusion** : la donnée n'entre jamais dans un prompt.
- **Masquage** : la valeur est remplacée par un marqueur non réversible
  (`[IBAN]`, `[EMAIL]`).
- **Pseudonymisation** : la valeur est remplacée par un jeton dont la table de
  correspondance reste locale ; la réponse est dé-pseudonymisée côté serveur.
- **Autorisation explicite** : la donnée peut être transmise, uniquement dans
  les conditions fixées par la politique T2 (couche de l'utilisateur, extraits
  minimaux).

## 3. Synthèse

| Domaine | Classification max. | Présent aujourd'hui | Transmis à un LLM aujourd'hui | Traitement exigé avant LLM |
|---|---|---|---|---|
| 4.1 Comptes et authentification | C3 | Oui | Non | Exclusion |
| 4.2 Journal d'audit | C2 | Oui | Non | Exclusion |
| 4.3 Secrets et clés | C3 | Oui | Non | Exclusion absolue |
| 4.4 Corpus documentaire | C3 | Oui | Non | Selon niveau et couche (§ 4.4) |
| 4.5 Métadonnées des documents | C1 | Oui | Non | Autorisation explicite ou exclusion selon le champ |
| 4.6 Données stockées dans Qdrant | C3 | Oui (si le pipeline a été exécuté) | Non | Exclusion des champs techniques |
| 4.7 Données accessibles au frontend | C3 | Oui | Non | Sans objet (aucun appel LLM depuis le frontend) |
| 4.8 Données qui seraient transmises par une future passerelle | C3 | Non (aucun LLM) | Non | Voir § 4.8 |

## 4. Cartographie détaillée

### 4.1 Comptes et données d'authentification

| Donnée | Emplacement réel | Classif. | Risques | Traitement avant LLM |
|---|---|---|---|---|
| Email de l'employé | `backend/app/models/user.py:25` (table `users`) | C2 | Identification d'une personne ; cible de hameçonnage | Exclusion ; si l'identité est nécessaire, pseudonymisation (identifiant opaque) |
| Haché du mot de passe (argon2) | `backend/app/models/user.py:31` ; hachage `backend/app/core/security.py:10-14` | C3 | Attaque hors ligne en cas de fuite de la base | Exclusion absolue |
| Rôle, statut, département | `backend/app/models/user.py:36`, `:42`, `:48` | C1 | Cartographie de l'organisation, ciblage des comptes `ADMIN` | Exclusion (le département ne sert qu'au filtrage côté serveur) |
| Jeton d'invitation (clair) | Généré `backend/app/services/invitation_service.py:28`, renvoyé `backend/app/api/routes/users.py:179`, schéma `backend/app/schemas/invitation.py:15` | C3 | Prise de contrôle du compte invité tant que le jeton est valide | Exclusion absolue |
| Haché SHA-256 du jeton d'invitation | `backend/app/models/user_invitation.py:18` ; calcul `backend/app/services/invitation_service.py:41`, `:61` | C2 | Faible (jeton aléatoire de 32 octets) | Exclusion |
| Jeton d'accès JWT (HS256 : `sub` = identifiant numérique, `role`, `iat`, `exp`, `jti`) | `backend/app/core/security.py:21-35` ; `sub` = `str(user.id)` `backend/app/api/routes/auth.py:59-60` | C3 | Usurpation de session jusqu'à expiration | Exclusion absolue |
| `jti` des jetons révoqués | `backend/app/models/revoked_token.py:12-13` | C1 | Faible | Exclusion |

### 4.2 Journal d'audit

Table `audit_logs` (`backend/app/models/audit_log.py`), format détaillé dans
`docs/SEC-01-detection-comportements-suspects.md`, section « Format du journal ».

| Donnée | Emplacement réel | Classif. | Risques | Traitement avant LLM |
|---|---|---|---|---|
| `user_id` et email (par jointure) | `backend/app/models/audit_log.py:62` ; email exposé en API et CSV `backend/app/api/routes/audit.py:27-38` | C2 | Profilage de l'activité d'un employé | Exclusion |
| Adresse IP | `backend/app/models/audit_log.py:92` ; capture `backend/app/api/dependencies.py:37` | C2 | Donnée personnelle, localisation | Exclusion |
| Départements de l'acteur et de la ressource | `backend/app/models/audit_log.py:98`, `:104` | C1 | Faible | Exclusion |
| `duration_ms`, horodatage, action, résultat, `resource_id` | `backend/app/models/audit_log.py:60-117` | C1 | Reconstitution de l'activité | Exclusion |
| `details` (`reason`, `document_ids`) | `backend/app/models/audit_log.py:114` ; construction `backend/app/services/audit.py:31-37` | C1 | Faible : énumération fermée et entiers uniquement | Exclusion |
| Alertes de détection (email, IP) | `backend/app/schemas/security_alert.py:23-36` | C2 | Idem email / IP | Exclusion |

Accès : lecture, export et alertes réservés à `ADMIN`
(`backend/app/api/routes/audit.py:19-25`). Le journal n'a aucune raison
d'entrer dans un prompt : **exclusion totale**.

### 4.3 Secrets et clés

| Donnée | Emplacement réel | Classif. | Risques | Traitement avant LLM |
|---|---|---|---|---|
| `JWT_SECRET_KEY` | Lu `backend/app/core/config.py:15` ; valeur dans `backend/.env` | C3 | Forge de jetons pour n'importe quel compte | Exclusion absolue |
| Mot de passe de la base (dans `DATABASE_URL`) | `backend/app/core/config.py:13` ; valeur dans `backend/.env` | C3 | Accès complet à la base | Exclusion absolue |
| `SEED_ADMIN_PASSWORD` | `backend/app/core/config.py:23` ; utilisé `backend/app/db/seed.py` | C3 | Compte administrateur initial | Exclusion absolue |
| `QDRANT_URL`, `QDRANT_API_KEY` | Lus `backend/app/services/Agentic_RAG/Data_preparation/storing.py:18-20` | C3 | Lecture ou altération du corpus vectorisé | Exclusion absolue |

`backend/.env` est ignoré par Git (`.gitignore:13-17`) et seules les clés
`APP_ENV`, `APP_NAME`, `DATABASE_URL`, `JWT_SECRET_KEY` y sont définies.
`QDRANT_URL` et `QDRANT_API_KEY` ne figurent **ni** dans `backend/.env` **ni**
dans `backend/.env.example` (§ 7, écart E6).

### 4.4 Corpus documentaire et séparation par couche métier

Le corpus RAG est constitué de 8 PDF **commités dans Git** dans
`data/documents/`. Chacun est déclaré dans le registre `DOCUMENTS_METADATA`
(`backend/app/services/Agentic_RAG/Data_preparation/ingestion.py:27-56`) avec
un `doc_type` (`public` / `prive`) et une `business_layer` (`front_office`,
`credit_risques`, `conformite_lbc_ft`, `direction_generale`). Les 4 documents
privés se déclarent eux-mêmes **fictifs** (« document interne fictif »,
« document synthétique », « usage pédagogique »).

#### 4.4.1 Classification par document

| Document | Couche | Mention portée par le document | Contenu sensible constaté | Classif. proposée | Traitement avant LLM |
|---|---|---|---|---|---|
| `Public__Credits_&_risques_Règlement_COBAC.pdf` | `credit_risques` | Règlement public | Aucun | C0 | Autorisation explicite |
| `Public__Direction_Conformite_Règlement_COBAC.pdf` | `conformite_lbc_ft` | Règlement public | Aucun | C0 | Autorisation explicite |
| `Public__Direction_Generale_Strategie_Rapport_annuel_COBAC.pdf` | `direction_generale` | Rapport public | Aucun | C0 | Autorisation explicite |
| `Public__Front_Office_Gestionnaires_Comptes_BICEC.pdf` | `front_office` | Document public | Aucun | C0 | Autorisation explicite |
| `Prive__Front_Office_Gestionnaires_Comptes.pdf` | `front_office` | « CONFIDENTIEL INTERNE », « INTERNE — niveau FO-2 », public autorisé : gestionnaires et superviseurs Front Office | Parcours d'ouverture de compte, matrice de délégation, dérogations commerciales | C2 | Autorisation explicite **pour la couche Front Office uniquement** ; exclusion pour les autres couches |
| `Prive__Credit_Risques.pdf` | `credit_risques` | « INTERNE — Diffusion restreinte », droits RAG `CR_ANALYST`, `CR_MANAGER`, `RISK_MANAGER`, `COMMITTEE` | Seuils de délégation (ex. « ≤ 10 M FCFA »), signaux d'alerte, règles de dérogation et d'escalade | C2 | Autorisation explicite **pour la couche Crédit & Risques uniquement** ; exclusion sinon |
| `Prive__Direction_conformite.pdf` | `conformite_lbc_ft` | « Confidentiel — Interne », périmètre : personnel Front Office **et** fonctions Conformité | Voir § 4.4.2 | C2 | Voir § 4.4.2 |
| `Prive__Direction_Generale_Strategie.pdf` | `direction_generale` | « RESTREINT DIRECTION », accès réservé à la Direction Générale et aux responsables habilités | Principes de pilotage, tableau de bord, arbitrages, budgets et projections | C3 | Exclusion par défaut ; autorisation explicite seulement pour la couche Direction Générale si la décision D1 retient un modèle hébergé en interne |

#### 4.4.2 Cas des procédures LBC-FT

`Prive__Direction_conformite.pdf` (procédure fictive `CONF-LBC-001`) n'est
**pas** classé C3 en bloc. Le document se déclare « Confidentiel — Interne » et
son périmètre inclut explicitement le Front Office. Son contenu est hétérogène :

| Partie (pages) | Contenu | Classif. | Risque principal | Accès réellement nécessaire |
|---|---|---|---|---|
| Objet, principes directeurs (p. 1) | Règles générales de vigilance | C1 | Faible | Front Office et Conformité |
| § 1 Connaissance du client — KYC (p. 2) | Pièces à collecter, bénéficiaire effectif, mise à jour du dossier | C2 | Faible à modéré : procédure opérationnelle | Front Office (collecte) et Conformité |
| § 2 Grille de vigilance (p. 3) | Niveaux standard / renforcé / élevé, situations de vigilance renforcée | C2 | Divulgation des critères qui déclenchent une vigilance renforcée | Front Office et Conformité |
| § 3.1 Scénarios d'alerte (p. 4) | Fractionnement, hausse brutale des flux, contreparties multiples… | C2 (sensibilité élevée) | **Aide au contournement** : connaître les scénarios de détection permet de les éviter | Conformité ; le Front Office n'a besoin que de savoir qu'une alerte existe et à qui la transmettre |
| § 3.2–3.3 Traitement et délais des alertes (p. 4) | Circuit d'escalade, interdiction d'informer le client | C2 | Divulgation au client (« tipping-off ») | Front Office et Conformité |
| § 4–5 Gouvernance, confidentialité, traçabilité (p. 5) | Rôles, escalade à la Direction | C1 à C2 | Faible | Front Office et Conformité |

Ce qui relèverait de **C3** (alertes réelles nominatives, analyses de dossiers,
déclarations de soupçon) est **absent** du corpus actuel (§ 5).

Conséquences pour les tâches suivantes :

- L'étiquetage actuel est **par document** : tout le PDF porte
  `conformite_lbc_ft`. Si le filtre de couche (I-5) n'autorise que la couche de
  l'utilisateur, le Front Office n'aura accès à **aucune** partie de cette
  procédure, alors que le document l'inclut dans son périmètre. Le choix entre
  un accès Front Office partiel et une restriction à la Conformité relève de la
  matrice couche × documents (I-5) et du propriétaire métier (§ 8, U4).
- Un étiquetage **par section** (par exemple § 3.1 réservé à la Conformité)
  n'existe pas : les chunks n'héritent que des métadonnées de la page
  (`backend/app/services/Agentic_RAG/Data_preparation/chunking.py:37-47`).

#### 4.4.3 Séparation entre la bibliothèque documentaire et le corpus RAG

- La bibliothèque documentaire (API `/documents`) et le corpus RAG partagent le
  dossier `data/documents/` (`backend/app/services/document_service.py:13`).
- L'API ne reconnaît que les noms `visibility__département__titre` en
  minuscules (`backend/app/services/document_service.py:74-80`). Les noms des
  PDF du corpus (`Prive__X.pdf`, `Public__X.pdf`) n'ont que deux segments : ils
  sont **invisibles via l'API documents**.
- Inversement, l'ingestion refuse tout PDF absent du registre
  (`ingestion.py:64-68`) et ce refus **interrompt tout le lot**
  (`ingestion.py:96`). Un document téléversé par un utilisateur, qui pourrait
  contenir des données de clients, ne peut donc pas être ingéré sans
  déclaration explicite.
- Les noms des couches RAG ne correspondent pas aux noms des départements en
  base (`backend/app/db/seed.py:14-19`) : `credit_risques` face à
  « Credit & Risques », `conformite_lbc_ft` face à « Direction Conformite ».
  Aucune table de correspondance n'existe (§ 7, écart E3).

### 4.5 Métadonnées associées aux documents

Métadonnées produites par `normalize_metadata`
(`backend/app/services/Agentic_RAG/Data_preparation/ingestion.py:70-79`) et
par le chunking (`chunking.py:44`) :

| Champ | Contenu | Classif. | Visible par le LLM (configuration llama_index) | Traitement avant LLM |
|---|---|---|---|---|
| `source` | Nom du fichier PDF | C1 | Oui | Autorisation explicite (citation de la source) |
| `page` | Numéro de page | C0 | Oui | Autorisation explicite (citation) |
| `doc_type` | `public` / `prive` | C1 | Oui | Autorisation explicite, à confirmer en T2 (utile au modèle ? sinon exclusion) |
| `business_layer` | Couche métier | C1 | Oui | Autorisation explicite pour la couche de l'utilisateur ; ne doit jamais révéler une autre couche, ce qui suppose le filtrage I-5 |
| `chunk_index` | Position du chunk dans la page | C1 | Oui | Exclusion recommandée (identifiant technique sans utilité pour la réponse) |
| `file_path` | **Chemin absolu** du fichier sur le serveur d'ingestion | C2 | **Non** (`excluded_llm_metadata_keys`, `ingestion.py:79`) | Exclusion |

Le brouillon mentionnait un champ « auteur » : **il n'existe pas** dans le
projet. Le « département propriétaire » correspond à `business_layer`.

Toutes les métadonnées sont exclues du texte vectorisé
(`excluded_embed_metadata_keys`, `ingestion.py:78`).

**Limite** : l'exclusion de `file_path` ne s'applique qu'au rendu des
métadonnées par llama_index (`MetadataMode.LLM`). Un futur module de
récupération qui lirait directement le payload Qdrant l'ignorerait (§ 4.6).

### 4.6 Données stockées dans Qdrant

Chaque point écrit par `store_nodes`
(`backend/app/services/Agentic_RAG/Data_preparation/storing.py:63-68`) contient :

| Donnée | Emplacement | Classif. | Risques | Traitement avant LLM |
|---|---|---|---|---|
| `text` : texte **intégral** du chunk, documents privés compris | `storing.py:66` | Celle du document (C0 à C3) | Copie du corpus confidentiel hors de l'application ; dépend de l'hébergement de Qdrant | Selon § 4.4, après filtrage de couche (I-5) et minimisation (T4) |
| Métadonnées : `source`, `page`, `doc_type`, `business_layer`, `chunk_index` | `storing.py:66` (`**n.metadata`) | C1 | Faible | Selon § 4.5 |
| `file_path` (chemin absolu) | `storing.py:66` | C2 | Révèle l'arborescence du serveur ; transmis tel quel si le payload est relu sans filtrage | Exclusion |
| Identifiant du point (UUID v5 dérivé du texte) | `chunking.py:47` | C1 | Faible | Exclusion |
| Vecteur d'embedding | `storing.py:65` | C2 | Inversion partielle possible du texte | Exclusion |

Index de payload sur `source`, `doc_type`, `business_layer`, `page`
(`storing.py:37-43`), prévus pour le filtrage par couche **mais utilisés par
aucun code à ce jour**.

Le constat sur `file_path` est déjà documenté dans
`docs/SEC-02-matrice-des-droits.md` § 5.3.

**Hébergement inconnu** : le serveur est désigné par `QDRANT_URL`, absent du
dépôt. S'il s'agit d'un service hébergé par un tiers, le texte intégral des
documents privés sort de l'infrastructure de la banque. Ce n'est pas un
éditeur de LLM, mais c'est un **transfert à un tiers** à décider explicitement
(§ 8, U1).

**Embeddings** : le modèle par défaut
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
(`backend/app/services/Agentic_RAG/Data_preparation/vectorisation.py:6-14`) est
exécuté **localement** par `HuggingFaceEmbedding`. Seuls les poids du modèle
sont téléchargés ; aucun texte n'est envoyé à un service d'embedding. La
variable `EMBEDDING_MODEL` permet de changer de modèle, mais reste une
exécution locale.

### 4.7 Données accessibles au frontend

| Donnée | Emplacement réel | Classif. | Risques | Remarque |
|---|---|---|---|---|
| Jeton JWT | `frontend/src/api/tokenStore.ts:1`, `:8`, `:18` (`localStorage`, clé `rag.access_token`) | C3 | Vol du jeton par une faille XSS | Hors LLM ; écart E9 |
| `file_path` relatif (`data/documents/…`) | Construit `backend/app/services/document_service.py:95` ; exposé `backend/app/schemas/document.py:18` ; typé `frontend/src/api/types.ts:37` | C1 | Faible (chemin relatif) | Utilisé pour l'extension `frontend/src/pages/DocumentDetailPage.tsx:39` |
| Contenu des documents de la bibliothèque | `GET /documents/{id}/content` `backend/app/api/routes/documents.py:320` | Celle du document | Accès hors département | Cloisonnement strict appliqué (`documents.py:260`) |
| Terme de recherche `q` | `GET /documents/search_document?q=…` `backend/app/api/routes/documents.py:574-577` | C1 à C2 (saisie libre) | Le terme figure dans l'URL, donc potentiellement dans les journaux d'accès | Non stocké dans l'audit (seuls les `document_ids` le sont) ; écart E8 |
| Pour `ADMIN` : emails, IP, alertes, export CSV | `frontend/src/api/types.ts:127`, `:132` | C2 | Exposition à un compte administrateur compromis | Réservé à `ADMIN` |
| Pour `ADMIN` : jeton d'invitation en clair | `frontend/src/api/types.ts:76` | C3 | Remis une seule fois, à transmettre hors application | Choix de conception (pas d'envoi d'email) |

Le frontend n'appelle que le backend (`frontend/src/api/client.ts:5-8`). Il ne
contient aucun appel à un fournisseur de LLM.

### 4.8 Données susceptibles d'être transmises à un fournisseur de LLM

**État réel : aucune.**

- `backend/app/services/Agentic_RAG/rag_agent.py`, `retriever_tools.py` et
  `augmentation_tool.py` sont **vides (0 ligne)**.
- Aucun SDK de LLM n'est présent dans le code ni dans
  `backend/pyproject.toml`. Aucune route de chat n'existe.
- `llama_index`, `qdrant_client` et `python-dotenv` ne sont pas déclarés dans
  les dépendances du backend (`backend/pyproject.toml`) : le pipeline de
  préparation ne s'exécute pas dans l'environnement du backend.

**Ce qu'une future passerelle (I-2 à I-4, US 07 T5) transmettrait**, et le
traitement exigé :

| Donnée | Origine | Traitement exigé |
|---|---|---|
| Question de l'utilisateur | Saisie libre | Autorisation explicite **après** pseudonymisation des entités sensibles (T3) : l'utilisateur peut saisir un nom ou un numéro de compte |
| Texte des chunks récupérés | Payload Qdrant `text` | Autorisation explicite, limitée aux chunks de la couche de l'utilisateur (I-5) et au strict nécessaire (T4) ; C3 exclu par défaut |
| Métadonnées des chunks | Payload Qdrant | `source`, `page` : autorisation explicite ; `file_path`, `chunk_index`, identifiant de point : exclusion |
| Identité de l'utilisateur (email, rôle, département) | Session | Exclusion ; la couche sert au filtrage côté serveur et n'a pas à figurer dans le prompt |
| Historique de conversation | Inexistant à ce jour | À cartographier quand il existera |
| Secrets, jetons, journal d'audit | — | Exclusion absolue |

## 5. Catégories prospectives (absentes aujourd'hui)

Issues du brouillon, ces catégories ne sont **pas présentes** dans le corpus ni
dans la base actuels. Elles deviendraient réelles si des documents clients
étaient téléversés puis ingérés, ou si une conversation contenait de telles
données.

| Catégorie | Exemples | Classif. | Traitement avant LLM |
|---|---|---|---|
| Identité des clients | Noms, prénoms, dates de naissance | C2 | Pseudonymisation (table de correspondance locale) |
| Comptes et paiement | Numéros de compte, IBAN, cartes | C3 | Masquage |
| Identifiants clients, numéros de dossier | Identifiant client, référence de dossier de crédit | C2 | Pseudonymisation |
| Coordonnées des clients | Emails, téléphones, adresses | C2 | Masquage |
| Données RH | Salaires, évaluations | C3 | Exclusion |
| Données LBC-FT nominatives | Alertes, analyses, déclarations de soupçon | C3 | Exclusion |

## 6. État des protections

### 6.1 Protections effectivement implémentées

| Protection | Code | Test |
|---|---|---|
| Mots de passe hachés (argon2) | `backend/app/core/security.py:10-18` | `backend/tests/test_invitations.py:73` (`test_invitation_lifecycle_and_single_use`) |
| Jeton d'invitation stocké haché (SHA-256), usage unique | `backend/app/services/invitation_service.py:41`, `:61` | `backend/tests/test_invitations.py:73`, `:119`, `:128` |
| JWT signé, révocable (`jti`), sujet = identifiant numérique | `backend/app/core/security.py:21-43`, `backend/app/api/routes/auth.py:59-60` | `backend/tests/test_auth.py:178`, `:226`, `:234` |
| Secrets hors Git | `.gitignore:13-17` | — |
| Aucun jeton ni secret dans le journal ; `details` limité à une énumération et des entiers | `backend/app/services/audit.py:31-37` | `backend/tests/test_audit.py:390`, `:594` |
| Aucune donnée secrète dans les alertes | `backend/app/schemas/security_alert.py:24` | `backend/tests/test_suspicious_activity.py:648` |
| Cloisonnement strict par département pour la bibliothèque | `backend/app/api/routes/documents.py:193`, `:260` | `backend/tests/test_documents.py:186`, `:202` |
| Étiquetage obligatoire de chaque PDF du corpus (refus sinon) | `ingestion.py:27-56`, `:64-68` | `backend/tests/test_rag_ingestion_security.py:107`, `:115`, `:125`, `:131` |
| `file_path` exclu de la vue LLM de llama_index | `ingestion.py:79` | `backend/tests/test_rag_ingestion_security.py:144` |
| Métadonnées exclues des vecteurs | `ingestion.py:78` | `backend/tests/test_rag_ingestion_security.py:154` |
| Étiquettes de couche indexées dans Qdrant | `storing.py:37-43` | `backend/tests/test_rag_ingestion_security.py:197` |
| Embeddings calculés localement | `vectorisation.py:6-14` | — |

### 6.2 Protections partielles ou insuffisantes

| Constat | Pourquoi c'est insuffisant | Preuve |
|---|---|---|
| `file_path` exclu de la vue LLM mais stocké dans Qdrant | L'exclusion ne vaut que si la récupération passe par le rendu llama_index | `storing.py:66` ; `backend/tests/test_rag_ingestion_security.py:205` |
| Étiquettes de couche indexées | Aucun filtre ne les utilise : aucun module de récupération n'existe | `storing.py:37-43` ; `retriever_tools.py` vide |
| Étiquetage par document | Ne permet pas de restreindre une section (ex. § 3.1 LBC-FT) | `chunking.py:37-47` |
| JWT dans `localStorage` | Exposé à une faille XSS | `frontend/src/api/tokenStore.ts:8`, `:18` |

### 6.3 Fonctionnalités absentes

- Intégration d'un LLM, construction de prompt, endpoint de chat (I-3, I-4).
- Récupération et filtrage pré- et post-récupération par couche (I-2, I-5).
- Module d'anonymisation / pseudonymisation (US 07 T3).
- Minimisation du contexte (US 07 T4).
- Passerelle LLM interne (US 07 T5).
- Correspondance couches RAG ↔ départements (I-5).

## 7. Écarts et rattachement aux tâches

| ID | Écart | Tâche concernée |
|---|---|---|
| E1 | `file_path` stocké dans le payload Qdrant | US 07 T4 (retrait avant transmission) et I-1 (ne plus le stocker) ; constat SEC-02 § 5.3 |
| E2 | Aucun filtrage par couche à la récupération | I-5 (pré- et post-récupération), US 07 T4 |
| E3 | Noms des couches RAG différents des noms des départements | I-5 (matrice couche × documents) |
| E4 | Aucune pseudonymisation de la question ni des chunks | US 07 T3 |
| E5 | Aucune règle sur les extraits et métadonnées autorisés dans un prompt | US 07 T2 (politique), puis T4 |
| E6 | `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `EMBEDDING_MODEL` non documentés dans `backend/.env.example` | I-1 (préparation des données) |
| E7 | Hébergement de Qdrant non décidé (transfert possible du corpus à un tiers) | US 07 T5 / décision D1 du brouillon |
| E8 | Terme de recherche `q` dans l'URL, potentiellement dans les journaux d'accès | SEC-01 (journalisation) ; configuration de déploiement |
| E9 | JWT dans `localStorage` | Hors périmètre US 07 : aucune tâche existante, à inscrire au backlog SSI |
| E10 | Étiquetage par document uniquement (pas de restriction par section LBC-FT) | I-5 (matrice), US 07 T4 |
| E11 | `llama_index`, `qdrant_client`, `python-dotenv` absents des dépendances du backend | I-1 |

## 8. Incertitudes et décisions ouvertes

| ID | Question | Impact |
|---|---|---|
| U1 | Où Qdrant est-il hébergé (local, infrastructure de la banque, service tiers) ? | Si tiers : transfert du corpus privé hors de la banque (§ 4.6) |
| U2 | Quel fournisseur de LLM (modèle interne ou API externe) ? Décision D1 du brouillon | Détermine si C3 peut être autorisé et le niveau de pseudonymisation exigé |
| U3 | Les classifications C0 à C3 proposées (§ 4.4.1) sont-elles validées par les propriétaires métier ? | Les traitements avant LLM en dépendent |
| U4 | Le Front Office doit-il accéder via le chat aux parties KYC et traitement des alertes de la procédure LBC-FT ? | Matrice I-5 ; étiquetage par section éventuel |
| U5 | Les rôles cités par `Prive__Credit_Risques.pdf` (`CR_ANALYST`, `CR_MANAGER`, `RISK_MANAGER`, `COMMITTEE`) n'existent pas dans l'application (`ADMIN`, `EMPLOYEE` uniquement) | Le contrôle d'accès RAG ne peut s'appuyer que sur le département |
| U6 | Visibilité du dépôt GitHub contenant les 8 PDF | Faible tant que les documents privés restent fictifs |
| U7 | Les journaux d'accès d'uvicorn sont-ils activés et conservés en production ? | Écart E8 |
| U8 | La recherche par motifs n'exclut pas toute donnée personnelle dans les PDF | Revue manuelle recommandée avant d'ingérer de nouveaux documents |

## 9. Documents liés

- `docs/SEC-01-detection-comportements-suspects.md` — format du journal
  d'audit et détection.
- `docs/SEC-02-matrice-des-droits.md` — cloisonnement, § 5 contrôles amont du
  pipeline RAG (N1 à N3) et constat `file_path`.
- `docs/Sprint-Backlog2.txt` — tâches I-1 à I-5 et US 07.
- Brouillon d'origine : `docs/SPRINT2_SSI.md` § 6 et § 7 (branche
  `myyr_feat/sprint2-ssi`, commit `0adb97e`).
