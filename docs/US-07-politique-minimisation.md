# US 07 — Politique de minimisation des données (T2)

Règles fixant, pour chaque catégorie de données de RAG_System_Bank, ce qui peut
être **conservé**, **consulté**, **utilisé** par l'application et
éventuellement **transmis** à un futur LLM. Cette politique s'appuie sur la
cartographie `docs/US-07-cartographie-donnees-sensibles.md` (US 07 T1), dont
elle reprend les classifications C0 à C3, les écarts E1 à E11 et les
incertitudes U1 à U8.

> **Statut (2026-11-02) : aucun LLM n'est intégré au projet.** Aucun prompt
> n'est construit et aucune donnée n'est transmise à un fournisseur de LLM.
> Les règles de transmission sont des **exigences** pour US 07 T3 à T5 et pour
> les tâches I-2 à I-5. Les décisions H1 à H12 (§ 10) sont **ouvertes** : ce
> document ne les tranche pas.

## 1. Objet et périmètre

La minimisation doit réduire l'exposition des données **sans retirer au futur
RAG ce dont il a besoin** pour répondre à une question autorisée. Ce document
distingue donc :

- les informations **nécessaires** à une réponse autorisée : la question, le
  texte des extraits de la couche de l'utilisateur, la source et la page pour
  la citer ;
- les données à **exclure** : elles n'ont aucune utilité pour la réponse ;
- les données à **transformer** (masquer ou pseudonymiser) : leur valeur exacte
  n'est pas nécessaire, mais leur présence l'est pour le sens de la réponse.

Le contenu métier d'un document autorisé (seuils, procédures, critères) **est**
l'information recherchée : il n'est pas masqué lorsqu'il est transmis pour la
couche à laquelle il appartient.

## 2. Sources et attribution

- Cartographie US 07 T1 : `docs/US-07-cartographie-donnees-sensibles.md`.
- Brouillon du coéquipier : `docs/SPRINT2_SSI.md` § 7 (règles M1 à M8 et
  exigences de la passerelle) et § 4.3 (règles du journal), branche
  `myyr_feat/sprint2-ssi`, commit `0adb97e`, auteur myyrcorp. Le document
  original n'est pas modifié et la branche n'est pas fusionnée.

Reprise du brouillon :

| Règle du brouillon | Constat | Reprise dans ce document |
|---|---|---|
| M1 — extraits strictement nécessaires | Valable | `MIN-LLM-03` |
| M2 — limiter nombre et taille, mesurer le prompt | Valable, sans valeur chiffrée | `MIN-LLM-04` ; valeurs : décision H6 |
| M3 — retirer les métadonnées sensibles | Trop générale | Liste fermée par champ : `MIN-META-01` à `MIN-META-05` |
| M4 — filtre de couche avant la recherche, revérification avant l'envoi | Valable ; la correspondance couches ↔ départements n'existe pas (T1 E3) | `MIN-LLM-01`, `MIN-LLM-02` ; dépendance H10 |
| M5 — pseudonymiser les entités | Ne distingue pas masquage et pseudonymisation | `MIN-PROS-01` à `MIN-PROS-06`, conformes à T1 § 5 |
| M6 — dé-pseudonymiser côté serveur | Incomplète | `MIN-LLM-07` : ne réinsérer que les valeurs que l'utilisateur a le droit de voir |
| M7 — aucun secret dans le prompt | Valable | `MIN-SEC-01` |
| M8 — journaliser chaque appel, jamais le contenu | Valable ; le schéma d'audit actuel ne le permet pas encore | `MIN-LLM-09` |
| § 4.3 — stocker « la longueur ou une empreinte » de la requête | Une empreinte d'une requête courte peut être retrouvée par dictionnaire | `MIN-AUD-04` (pas d'empreinte) ; longueur : décision H8 |
| Exigences de la passerelle | Relèvent de US 07 T5 | Citées en `MIN-LLM-10` comme dépendances |

Le brouillon ne traitait ni les comptes, ni le journal, ni les secrets, ni
Qdrant, ni la conservation, ni les responsabilités : ces règles sont propres à
ce document.

## 3. Statuts des règles

Chaque règle porte un identifiant unique `MIN-<catégorie>-<numéro>` et l'un des
statuts suivants :

| Statut | Signification |
|---|---|
| **RESPECTÉE** | Déjà appliquée par le code actuel ; la preuve (fichier, test) est citée |
| **APPLICABLE** | Ne dépend d'aucun composant manquant ; reste à implémenter ou à appliquer en procédure |
| **RETRIEVER** | Dépend du futur module de récupération et du filtrage par couche (I-2, I-5) |
| **FOURNISSEUR** | Dépend du choix du fournisseur de LLM et de la passerelle (décision D1 / H1, US 07 T5) |

Les numéros de ligne renvoient à l'arbre de travail de la branche
`temgoua_feat/auth-user-access-audit` ; certains fichiers cités portent des
modifications non encore commitées.

## 4. Principes généraux

| ID | Principe | Statut |
|---|---|---|
| MIN-GEN-01 | **Nécessité** : une donnée n'est collectée, conservée ou transmise que si elle sert une finalité identifiée dans ce document | APPLICABLE |
| MIN-GEN-02 | **Échec fermé** : une donnée ou un document non classé est traité comme exclu. Pour le corpus, c'est déjà le cas : un PDF absent du registre est refusé (`backend/app/services/Agentic_RAG/Data_preparation/ingestion.py:64-68`, test `backend/tests/test_rag_ingestion_security.py:107`) | RESPECTÉE (corpus) / APPLICABLE (autres données) |
| MIN-GEN-03 | **Cloisonnement** : un utilisateur n'obtient que des données de sa couche. Appliqué pour la bibliothèque (`backend/app/api/routes/documents.py:260`, tests `backend/tests/test_documents.py:186`, `:202`) ; pas encore pour le RAG | RESPECTÉE (bibliothèque) / RETRIEVER (RAG) |
| MIN-GEN-04 | **Aucun contenu dans les journaux** : ni texte de document, ni question, ni secret | RESPECTÉE (`backend/app/services/audit.py:31-37`, tests `backend/tests/test_audit.py:390`, `:594`) |
| MIN-GEN-05 | **Traitements côté serveur** : filtrage, masquage, pseudonymisation et appel au LLM ne sont jamais réalisés dans le frontend | RESPECTÉE (aucun appel LLM dans le frontend, T1 § 4.7) / FOURNISSEUR (passerelle) |
| MIN-GEN-06 | **La classification est fixée par le propriétaire métier** ; les niveaux C0 à C3 de T1 sont des propositions jusqu'à validation (H3) | APPLICABLE |

## 5. Matrice classification × usage

Classifications de T1 § 2.

| Niveau | Conserver | Consulter dans l'application | Utiliser dans l'application | Transmettre au LLM |
|---|---|---|---|---|
| **C0 Public** | Oui | Selon le cloisonnement par département en vigueur (SEC-02) | Oui | Autorisé (RETRIEVER, FOURNISSEUR) |
| **C1 Interne** | Oui, si nécessaire | Rôles et départements autorisés | Oui | Seulement si utile à la réponse ; les identifiants techniques sont exclus |
| **C2 Confidentiel** | Oui, accès restreint | Couche propriétaire uniquement (et `ADMIN` selon H11) | Oui, dans la couche | Autorisation explicite : couche de l'utilisateur, extraits minimaux. Données personnelles : masquées ou pseudonymisées |
| **C3 Secret / Restreint** | Oui, accès le plus restreint | Fonctions habilitées uniquement | Strict nécessaire | **Exclu par défaut.** Contenu documentaire C3 : seule une décision H1 (modèle hébergé en interne) avec l'accord du propriétaire pourrait l'autoriser. Secrets et jetons : **jamais** |

## 6. Règles par catégorie

### 6.1 Comptes et données d'authentification (T1 § 4.1)

| ID | Aspect | Règle | Statut | Preuve ou tâche |
|---|---|---|---|---|
| MIN-AUTH-01 | Nécessaire | Le compte se limite à : email, haché du mot de passe, rôle, statut, département | RESPECTÉE | `backend/app/models/user.py:25-48` |
| MIN-AUTH-02 | Exclure | Mots de passe et jetons jamais stockés en clair. Mot de passe : haché argon2 ; jeton d'invitation : haché SHA-256 | RESPECTÉE | `backend/app/core/security.py:10-18`, `backend/app/services/invitation_service.py:41` ; test `backend/tests/test_invitations.py:73` |
| MIN-AUTH-03 | Exclure | Le JWT ne contient aucune donnée personnelle : `sub` est l'identifiant numérique, pas l'email | RESPECTÉE | `backend/app/api/routes/auth.py:59-60`, `backend/app/core/security.py:24-30` |
| MIN-AUTH-04 | Exclure | Aucune donnée de compte (email, rôle, département, statut) n'entre dans un prompt. Le département ne sert qu'au filtrage côté serveur | FOURNISSEUR | US 07 T5 |
| MIN-AUTH-05 | Pseudonymiser | Si la passerelle doit identifier l'appelant, elle utilise un identifiant opaque, jamais l'email | FOURNISSEUR | US 07 T3, T5 |
| MIN-AUTH-06 | Masquer | Aucune donnée de compte à masquer : elle est exclue | — | — |
| MIN-AUTH-07 | Autorisation | Le jeton d'invitation en clair n'est remis qu'une fois, à l'`ADMIN` qui crée l'invitation | RESPECTÉE | `backend/app/api/routes/users.py:165-179` ; test `backend/tests/test_invitations.py:169`, `:182` |
| MIN-AUTH-08 | Conservation | Voir § 7 : jeton d'accès 60 min, invitation 24 h, aucune purge des invitations ni des jetons révoqués | — | § 7 |
| MIN-AUTH-09 | Contrôle | Tout ajout de champ au modèle `User` doit être justifié au regard de MIN-GEN-01 et de cette section | APPLICABLE | Revue de code |

### 6.2 Journaux d'audit (T1 § 4.2)

| ID | Aspect | Règle | Statut | Preuve ou tâche |
|---|---|---|---|---|
| MIN-AUD-01 | Nécessaire | Le journal contient ce qu'exigent la traçabilité et la détection SEC-01 : acteur, action, ressource, résultat, horodatage, IP, départements, durée, motif, identifiants des documents | RESPECTÉE | `backend/app/models/audit_log.py:60-117` ; `docs/SEC-01-detection-comportements-suspects.md` « Format du journal » |
| MIN-AUD-02 | Exclure | `details` n'accepte que `reason` (énumération fermée) et `document_ids` (entiers) : aucun texte libre | RESPECTÉE | `backend/app/services/audit.py:31-37` ; test `backend/tests/test_audit.py:594` |
| MIN-AUD-03 | Exclure | Aucun jeton, en-tête `Authorization`, mot de passe ni secret dans le journal ou les alertes | RESPECTÉE | Tests `backend/tests/test_audit.py:390`, `backend/tests/test_suspicious_activity.py:648` |
| MIN-AUD-04 | Exclure | Le terme recherché n'est pas journalisé, **ni en clair ni sous forme d'empreinte**. Seuls les identifiants des documents renvoyés le sont | RESPECTÉE | `backend/app/services/audit.py:36-37` ; tests `backend/tests/test_documents.py:642`, `:660`. Stocker la longueur seule : décision H8 |
| MIN-AUD-05 | Exclure | Aucune donnée du journal n'entre dans un prompt | FOURNISSEUR | US 07 T5 |
| MIN-AUD-06 | Masquer / pseudonymiser | Aucune transformation dans le journal : l'email et l'IP sont nécessaires à l'investigation. Leur exposition est restreinte par l'accès (MIN-AUD-07) | RESPECTÉE | — |
| MIN-AUD-07 | Autorisation | Lecture, export et alertes réservés à `ADMIN` | RESPECTÉE | `backend/app/api/routes/audit.py:19-25` |
| MIN-AUD-08 | Conservation | Durée minimale **technique** pour la détection : `ALERT_LOOKBACK_HOURS` + `ALERT_SPIKE_BASELINE_DAYS` (voir § 7). Durée maximale : **non définie**, décision H7 | — | § 7 |
| MIN-AUD-09 | Contrôle | Tout nouveau champ ou toute nouvelle clé de `details` doit respecter MIN-AUD-02 et être couvert par un test | APPLICABLE | Revue de code ; tests existants à étendre |

### 6.3 Secrets techniques (T1 § 4.3)

| ID | Aspect | Règle | Statut | Preuve ou tâche |
|---|---|---|---|---|
| MIN-SEC-01 | Exclure | Aucun secret (`JWT_SECRET_KEY`, mot de passe de la base, `SEED_ADMIN_PASSWORD`, `QDRANT_API_KEY`, future clé du fournisseur de LLM) dans un prompt, un journal, une réponse d'API, le frontend ou Git | RESPECTÉE (Git, journal) / FOURNISSEUR (prompt) | `.gitignore:13-17` ; tests `backend/tests/test_audit.py:390`, `backend/tests/test_suspicious_activity.py:648` |
| MIN-SEC-02 | Nécessaire | Les secrets ne sont lus que côté serveur, depuis l'environnement | RESPECTÉE | `backend/app/core/config.py:13-23`, `backend/app/services/Agentic_RAG/Data_preparation/storing.py:18-20` |
| MIN-SEC-03 | Contrôle | Chaque variable d'environnement attendue figure dans `backend/.env.example` **sans valeur réelle**. `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION` et `EMBEDDING_MODEL` y manquent | APPLICABLE | T1 écart E6 ; tâche I-1 |
| MIN-SEC-04 | Masquer / pseudonymiser | Sans objet : un secret n'est jamais transformé, il est exclu | — | — |
| MIN-SEC-05 | Conservation | Rotation des secrets : **aucune procédure définie** | — | Décision H7 |

### 6.4 Documents privés et publics (T1 § 4.4)

| ID | Aspect | Règle | Statut | Preuve ou tâche |
|---|---|---|---|---|
| MIN-DOC-01 | Nécessaire | Tout document du corpus porte un `doc_type` et une `business_layer` déclarés dans le registre | RESPECTÉE | `ingestion.py:27-56` ; tests `backend/tests/test_rag_ingestion_security.py:115`, `:125`, `:131` |
| MIN-DOC-02 | Autorisation | C0 : transmissible pour toute couche autorisée par la matrice I-5 | RETRIEVER, FOURNISSEUR | I-5, US 07 T4 |
| MIN-DOC-03 | Autorisation | C2 (procédures Front Office, Crédit & Risques, LBC-FT) : texte transmissible **intégralement pour sa seule couche**, sans masquage du contenu métier (seuils, procédures), qui est l'information recherchée. Les autres couches : exclusion | RETRIEVER, FOURNISSEUR | I-5, US 07 T4 |
| MIN-DOC-04 | Exclure | C3 (`Prive__Direction_Generale_Strategie.pdf`) : exclu de toute transmission par défaut. Une exception ne peut résulter que des décisions H1 et H11 et de l'accord du propriétaire | FOURNISSEUR | Décisions H1, H11 |
| MIN-DOC-05 | Autorisation | LBC-FT (`Prive__Direction_conformite.pdf`) : l'accès du Front Office aux parties KYC et au traitement des alertes, et la restriction éventuelle des scénarios d'alerte (§ 3.1 du document) à la Conformité, restent à décider (T1 § 4.4.2) | RETRIEVER | Décision H4 ; I-5 |
| MIN-DOC-06 | Pseudonymiser / masquer | Les documents actuels ne contiennent aucune donnée personnelle de client (T1 § 1.2, analyse par motifs). Si un document en contient, les règles MIN-PROS s'appliquent | FOURNISSEUR | US 07 T3 |
| MIN-DOC-07 | Contrôle | Avant d'ajouter un document au registre : vérifier son contenu, faire valider sa classification par le propriétaire, déclarer sa couche | APPLICABLE | Procédure ; T1 U3, U8 |
| MIN-DOC-08 | Contrôle | La suppression ou le déclassement d'un document doit être répercuté dans Qdrant (MIN-QDR-05) | APPLICABLE | I-1 |
| MIN-DOC-09 | Conservation | Bibliothèque : la suppression **efface** le fichier (`backend/app/api/routes/documents.py:548`) ; la modification avec remplacement du fichier **archive** l'ancienne version dans `data/archive/` sans limite (`documents.py:496`, `backend/app/services/document_service.py:168-175`). Durée de conservation des archives : non définie | — | Décision H7 |

### 6.5 Métadonnées (T1 § 4.5)

| ID | Aspect | Règle | Statut | Preuve ou tâche |
|---|---|---|---|---|
| MIN-META-01 | Nécessaire | `source` et `page` sont transmis avec chaque extrait pour permettre la citation | RETRIEVER, FOURNISSEUR | US 07 T4 |
| MIN-META-02 | Exclure | `file_path` n'est jamais transmis. Il est déjà exclu de la vue LLM de llama_index | RESPECTÉE (llama_index) / RETRIEVER (lecture directe de Qdrant) | `ingestion.py:79` ; test `backend/tests/test_rag_ingestion_security.py:144` |
| MIN-META-03 | Exclure | `chunk_index` et l'identifiant du point Qdrant ne sont pas transmis : identifiants techniques sans utilité pour la réponse | RETRIEVER | US 07 T4 |
| MIN-META-04 | Autorisation | Transmission de `doc_type` et `business_layer` : à décider. Ils ne doivent en aucun cas révéler l'existence d'une autre couche | RETRIEVER | Décision H5 |
| MIN-META-05 | Exclure | Aucune métadonnée dans le texte vectorisé | RESPECTÉE | `ingestion.py:78` ; test `backend/tests/test_rag_ingestion_security.py:154` |
| MIN-META-06 | Contrôle | Toute nouvelle métadonnée doit être classée et ajoutée explicitement à la liste des champs transmis ou exclus (liste fermée) | APPLICABLE | Revue de code |

### 6.6 Données stockées dans Qdrant (T1 § 4.6)

| ID | Aspect | Règle | Statut | Preuve ou tâche |
|---|---|---|---|---|
| MIN-QDR-01 | Nécessaire | Un point contient le texte du chunk, son vecteur et les métadonnées de filtrage et de citation (`source`, `page`, `doc_type`, `business_layer`) | RESPECTÉE | `storing.py:63-68` |
| MIN-QDR-02 | Exclure | `file_path` ne doit plus être stocké dans le payload | APPLICABLE | `storing.py:66` ; test (constat) `backend/tests/test_rag_ingestion_security.py:205` ; T1 E1 ; I-1 |
| MIN-QDR-03 | Nécessaire | Les étiquettes de couche sont indexées pour le filtrage | RESPECTÉE | `storing.py:37-43` ; test `backend/tests/test_rag_ingestion_security.py:197` |
| MIN-QDR-04 | Autorisation | Toute lecture de Qdrant applique le filtre de couche **dans la requête** (pré-récupération), puis une vérification avant l'envoi (post-récupération) | RETRIEVER | I-5 ; T1 E2 |
| MIN-QDR-05 | Conservation | Un document retiré ou déclassé doit être supprimé de Qdrant. Aucun mécanisme n'existe : l'écriture se fait par upsert uniquement | APPLICABLE | I-1 |
| MIN-QDR-06 | Autorisation | L'hébergement de Qdrant détermine si le texte des documents privés sort de l'infrastructure. Aucun document C2 ou C3 ne devrait être stocké chez un tiers sans décision explicite | APPLICABLE (règle) | Décision H2 ; T1 U1, E7 |
| MIN-QDR-07 | Masquer / pseudonymiser | Pas de transformation à l'ingestion pour le corpus actuel (aucune donnée personnelle). Pour de futurs documents contenant des données de clients, la transformation avant stockage ou avant envoi est à décider | FOURNISSEUR | US 07 T3 |

### 6.7 Données transmises à une future passerelle LLM (T1 § 4.8)

Aucune de ces règles n'est appliquée aujourd'hui : il n'existe ni passerelle,
ni prompt.

| ID | Aspect | Règle | Statut | Preuve ou tâche |
|---|---|---|---|---|
| MIN-LLM-01 | Autorisation | Filtre de couche appliqué **avant** la recherche (M4) | RETRIEVER | I-5 ; dépend de H10 |
| MIN-LLM-02 | Autorisation | Revérification de la couche de chaque extrait **avant** l'envoi ; un extrait hors couche est retiré et l'événement journalisé (M4) | RETRIEVER | I-5 ; US 07 T4 |
| MIN-LLM-03 | Nécessaire | Seuls les extraits nécessaires à la réponse sont transmis (M1) | RETRIEVER | US 07 T4 |
| MIN-LLM-04 | Nécessaire | Nombre et taille des extraits limités ; taille du prompt mesurée après assemblage (M2) | RETRIEVER | I-3 ; valeurs : décision H6 |
| MIN-LLM-05 | Pseudonymiser | La question de l'utilisateur est transmise après pseudonymisation des entités sensibles qu'elle pourrait contenir (noms, identifiants) | FOURNISSEUR | US 07 T3 |
| MIN-LLM-06 | Masquer | Numéros de compte, IBAN, cartes et coordonnées éventuellement présents dans la question ou les extraits sont masqués (non réversible) | FOURNISSEUR | US 07 T3 |
| MIN-LLM-07 | Pseudonymiser | La table de correspondance reste côté serveur ; la dé-pseudonymisation de la réponse ne réinsère que des valeurs que l'utilisateur a le droit de voir (M6 complétée) | FOURNISSEUR | US 07 T3 ; durée de vie : décision H12 |
| MIN-LLM-08 | Exclure | Ni identité de l'utilisateur, ni secret, ni journal, ni `file_path`, ni identifiant technique dans le prompt (M7) | FOURNISSEUR | US 07 T5 |
| MIN-LLM-09 | Contrôle | Chaque appel est journalisé sans contenu : utilisateur, couche, taille, nombre d'extraits, durée (M8). Le schéma d'audit actuel ne le permet pas : `details` n'accepte que `reason` et `document_ids`, et aucune action de type appel au LLM n'existe | FOURNISSEUR | US 07 T5 ; évolution du schéma SEC-01 |
| MIN-LLM-10 | Contrôle | Exigences de la passerelle reprises du brouillon : point d'entrée unique côté serveur, authentification, filtre de couche, clés du fournisseur côté serveur, délais, limite de débit et de taille | FOURNISSEUR | US 07 T5 |
| MIN-LLM-11 | Conservation | Conservation des prompts et des réponses par la passerelle et par le fournisseur : **non définie** | FOURNISSEUR | Décisions H1, H7 |
| MIN-LLM-12 | Contrôle | Scénarios de non-divulgation à exécuter dès que le chat existe | RETRIEVER, FOURNISSEUR | `docs/SEC-02-matrice-des-droits.md` § 5.4 ; SEC-02 T4, T5 |

### 6.8 Données prospectives de clients et de RH (T1 § 5)

Ces données **n'existent pas** aujourd'hui dans le corpus ni dans la base. Les
règles s'appliqueront dès qu'un document ou une question en contiendra.

| ID | Donnée | Classif. (T1) | Traitement avant LLM | Statut |
|---|---|---|---|---|
| MIN-PROS-01 | Identité des clients (noms, prénoms, dates de naissance) | C2 | Pseudonymisation | FOURNISSEUR (US 07 T3) |
| MIN-PROS-02 | Comptes et paiement (numéros de compte, IBAN, cartes) | C3 | Masquage | FOURNISSEUR (US 07 T3) |
| MIN-PROS-03 | Identifiants clients, numéros de dossier | C2 | Pseudonymisation | FOURNISSEUR (US 07 T3) |
| MIN-PROS-04 | Coordonnées des clients (emails, téléphones, adresses) | C2 | Masquage | FOURNISSEUR (US 07 T3) |
| MIN-PROS-05 | Données RH (salaires, évaluations) | C3 | Exclusion | FOURNISSEUR |
| MIN-PROS-06 | Données LBC-FT nominatives (alertes, analyses, déclarations de soupçon) | C3 | Exclusion | FOURNISSEUR |
| MIN-PROS-07 | Contrôle : tout document contenant de telles données est signalé lors de la revue préalable (MIN-DOC-07) avant d'être ajouté au registre | — | — | APPLICABLE |

La conservation de ces données relève des règles métier et réglementaires de
la banque : **aucune durée n'est fixée ici** (H7).

## 7. Conservation

### 7.1 Durées et mécanismes vérifiés dans le code

| Élément | Fait vérifié | Référence |
|---|---|---|
| Jeton d'accès | Validité 60 minutes par défaut, configurable | `backend/app/core/config.py:17` |
| Invitation | Validité 24 heures ; usage unique (`used_at`) | `backend/app/services/invitation_service.py:14`, `:66`, `:84` |
| Invitations utilisées ou expirées | Jamais purgées | Aucune suppression dans `backend/app/services/invitation_service.py` |
| Jetons révoqués | Conservés avec leur `expires_at`, jamais purgés | `backend/app/api/routes/auth.py:88-90`, `backend/app/models/revoked_token.py:12-13` |
| Comptes | Pas de suppression, seulement un changement de statut | `backend/app/api/routes/users.py` |
| Journal d'audit | Ajout seul ; aucune purge | `backend/app/services/audit.py` |
| Documents supprimés | Fichier effacé | `backend/app/api/routes/documents.py:548`, `backend/app/services/document_service.py:178-179` |
| Versions remplacées d'un document | Archivées dans `data/archive/`, sans limite | `backend/app/api/routes/documents.py:496`, `backend/app/services/document_service.py:168-175` |
| Points Qdrant | Upsert uniquement ; aucune suppression | `backend/app/services/Agentic_RAG/Data_preparation/storing.py` |

### 7.2 Règles techniques, sans durée réglementaire

| ID | Règle | Statut |
|---|---|---|
| MIN-CONS-01 | Une ligne de `revoked_tokens` n'a plus d'utilité après son `expires_at` : le jeton est alors refusé par l'expiration elle-même. Sa suppression après cette date ne réduit pas la sécurité | APPLICABLE (purge à implémenter si décidée) |
| MIN-CONS-02 | Après `used_at` ou `expires_at`, le haché du jeton d'invitation ne sert plus à l'authentification. Faut-il conserver la ligne comme trace ? Décision H7 | — |
| MIN-CONS-03 | La détection SEC-01 a besoin d'un historique d'audit d'au moins `ALERT_LOOKBACK_HOURS` + `ALERT_SPIKE_BASELINE_DAYS` (24 h + 7 jours par défaut, `backend/app/core/config.py:26`, `:43`, utilisés `backend/app/services/suspicious_activity.py:423`, `:472`). C'est un **minimum de fonctionnement**, pas une durée de conservation approuvée | RESPECTÉE (aucune purge) |
| MIN-CONS-04 | Les points Qdrant suivent le cycle de vie du document source (MIN-QDR-05) | APPLICABLE |

### 7.3 Durées non définies (décision H7)

Journal d'audit, invitations, archives de documents, points Qdrant, futurs
journaux de la passerelle, prompts et réponses chez le fournisseur, tables de
pseudonymisation, rotation des secrets. Aucune durée n'est vérifiée dans le
code ni dans une spécification validée ; ce document n'en propose aucune.

## 8. Responsabilités et contrôles

Rôles fonctionnels : les personnes qui les tiennent sont à désigner (H9).

| Rôle | Responsabilités |
|---|---|
| Propriétaire métier de chaque couche | Valider la classification de ses documents (MIN-GEN-06, MIN-DOC-07) ; décider des accès inter-couches (H4, H11) |
| Fonction sécurité / conformité | Valider cette politique ; décider des durées de conservation (H7) ; arbitrer les exceptions C3 (MIN-DOC-04) |
| Responsable projet | Décider du fournisseur de LLM et de l'hébergement de Qdrant (H1, H2) |
| Administrateur applicatif (`ADMIN`) | Gérer les comptes ; consulter le journal et les alertes ; ne pas diffuser les jetons d'invitation hors du canal prévu |
| Équipe de développement | Implémenter les règles APPLICABLE, RETRIEVER et FOURNISSEUR ; maintenir les tests ; appliquer la revue de code (MIN-AUTH-09, MIN-AUD-09, MIN-META-06) |

Contrôles automatisés existants : `backend/tests/test_audit.py`,
`backend/tests/test_suspicious_activity.py`, `backend/tests/test_documents.py`,
`backend/tests/test_invitations.py`, `backend/tests/test_rag_ingestion_security.py`
(références précises dans les tableaux du § 6). Contrôles à venir : scénarios
de non-divulgation de `docs/SEC-02-matrice-des-droits.md` § 5.4 et tests de
US 07 T3 à T5.

## 9. Synthèse de l'applicabilité

| Statut | Règles |
|---|---|
| **RESPECTÉE** | MIN-GEN-02 (corpus), 03 (bibliothèque), 04, 05 (frontend) ; MIN-AUTH-01, 02, 03, 07 ; MIN-AUD-01 à 04, 06, 07 ; MIN-SEC-01 (Git, journal), 02 ; MIN-DOC-01 ; MIN-META-02 (llama_index), 05 ; MIN-QDR-01, 03 ; MIN-CONS-03 |
| **APPLICABLE** | MIN-GEN-01, 02 (autres données), 06 ; MIN-AUTH-09 ; MIN-AUD-09 ; MIN-SEC-03 ; MIN-DOC-07, 08 ; MIN-META-06 ; MIN-QDR-02, 05, 06 ; MIN-PROS-07 ; MIN-CONS-01, 04 |
| **RETRIEVER** | MIN-GEN-03 (RAG) ; MIN-DOC-02, 03, 05 ; MIN-META-01 à 04 ; MIN-QDR-04 ; MIN-LLM-01 à 04, 12 |
| **FOURNISSEUR** | MIN-GEN-05 (passerelle) ; MIN-AUTH-04, 05 ; MIN-AUD-05 ; MIN-SEC-01 (prompt) ; MIN-DOC-02 à 04, 06 ; MIN-META-01 ; MIN-QDR-07 ; MIN-LLM-05 à 12 ; MIN-PROS-01 à 06 |
| **Sans statut** (renvoi au § 7, sans objet ou décision ouverte) | MIN-AUTH-06, 08 ; MIN-AUD-08 ; MIN-SEC-04, 05 ; MIN-DOC-09 ; MIN-CONS-02 |

Une règle peut porter deux statuts lorsqu'elle est déjà respectée dans un
périmètre et attend un composant dans un autre.

## 10. Décisions requérant une validation humaine (ouvertes)

| ID | Décision | Décideur pressenti | Règles concernées | Lien T1 |
|---|---|---|---|---|
| H1 | Fournisseur de LLM : modèle hébergé en interne ou API externe (D1 du brouillon) | Responsable projet, sécurité | MIN-DOC-04, MIN-LLM-05 à 11 | U2 |
| H2 | Hébergement de Qdrant | Responsable projet, sécurité | MIN-QDR-06 | U1, E7 |
| H3 | Validation des classifications C0 à C3 | Propriétaires métier | MIN-GEN-06, MIN-DOC-07 | U3 |
| H4 | Accès du Front Office aux sections LBC-FT ; étiquetage par section | Propriétaire Conformité | MIN-DOC-05 | U4, E10 |
| H5 | Transmission de `doc_type` et `business_layer` au LLM | Équipe projet | MIN-META-04 | — |
| H6 | Nombre et taille maximum des extraits ; taille maximale du prompt | Équipe projet | MIN-LLM-04 | — |
| H7 | Durées de conservation (§ 7.3) et rotation des secrets | Sécurité, conformité | MIN-AUD-08, MIN-SEC-05, MIN-DOC-09, MIN-LLM-11, MIN-CONS-02 | — |
| H8 | Journaliser la longueur de la question, ou rien | Sécurité | MIN-AUD-04 | E8 |
| H9 | Désignation des personnes responsables (§ 8) | Direction de projet | § 8 | — |
| H10 | Correspondance entre couches RAG et départements | Propriétaires métier, équipe projet | MIN-LLM-01 | E3 |
| H11 | Périmètre de l'`ADMIN` via le chat (toutes couches, y compris C3 ?) | Direction Générale, sécurité | MIN-DOC-04, matrice § 5 | — |
| H12 | Durée de vie de la table de pseudonymisation (requête ou session) | Sécurité | MIN-LLM-07 | — |

## 11. Traçabilité avec US 07 T1

| Écart T1 | Règles |
|---|---|
| E1 `file_path` dans Qdrant | MIN-QDR-02, MIN-META-02 |
| E2 aucun filtrage par couche | MIN-QDR-04, MIN-LLM-01, MIN-LLM-02 |
| E3 couches ≠ départements | MIN-LLM-01, H10 |
| E4 aucune pseudonymisation | MIN-LLM-05 à 07, MIN-PROS-01 à 04 |
| E5 aucune règle sur le contenu du prompt | MIN-LLM-03, 04, 08, MIN-META-01 à 04 |
| E6 variables Qdrant non documentées | MIN-SEC-03 |
| E7 hébergement de Qdrant | MIN-QDR-06, H2 |
| E8 terme de recherche dans l'URL | MIN-AUD-04, H8 |
| E9 JWT dans `localStorage` | Hors minimisation (protection du jeton) ; reste hors périmètre US 07 |
| E10 étiquetage par document | MIN-DOC-05, H4 |
| E11 dépendances du pipeline absentes | Hors minimisation ; tâche I-1 |

| Incertitude T1 | Décision |
|---|---|
| U1 | H2 |
| U2 | H1 |
| U3 | H3 |
| U4 | H4 |
| U5 rôles du PDF Crédit inexistants | Le filtrage ne peut s'appuyer que sur le département (MIN-LLM-01) |
| U6 visibilité du dépôt | Hors minimisation ; à confirmer |
| U7 journaux d'accès d'uvicorn | H8 |
| U8 limites de l'analyse par motifs | MIN-DOC-07 |

## 12. Documents liés

- `docs/US-07-cartographie-donnees-sensibles.md` — cartographie US 07 T1.
- `docs/SEC-01-detection-comportements-suspects.md` — format du journal et
  détection.
- `docs/SEC-02-matrice-des-droits.md` — cloisonnement et § 5 (contrôles amont
  du pipeline RAG, scénarios en attente).
- `docs/Sprint-Backlog2.txt` — tâches I-1 à I-5 et US 07.
- Brouillon d'origine : `docs/SPRINT2_SSI.md` § 4.3 et § 7 (branche
  `myyr_feat/sprint2-ssi`, commit `0adb97e`).
