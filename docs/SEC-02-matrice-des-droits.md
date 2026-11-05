# SEC-02 — Matrice des droits

Référence des règles d'accès appliquées par le backend et des tests automatisés
qui les vérifient. Toute modification d'une règle doit mettre à jour cette
matrice **et** le test correspondant.

## 1. Modèle d'accès

| Dimension | Valeurs |
|---|---|
| Rôle | `ADMIN`, `EMPLOYEE` |
| Département (couche métier) | Front Office, Credit & Risques, Direction Conformite, Direction Generale |
| Visibilité d'un document | `public`, `private` |

**Règle de cloisonnement strict (décision SEC-02, option A)** : un `EMPLOYEE`
n'accède qu'aux documents de **son propre département**, quelle que soit leur
visibilité. La visibilité `public` n'ouvre **aucun** accès inter-départements.
Seul `ADMIN` accède à tous les départements.

Toutes les règles sont appliquées côté backend ; le frontend n'est pas une
frontière de sécurité.

Sources des règles dans le code :

| Fichier | Mécanisme |
|---|---|
| `backend/app/api/dependencies.py` | `get_current_user` (401), `require_permission` (403) |
| `backend/app/api/routes/documents.py` | `ROLE_PERMISSIONS`, `_require_permission`, `_scoped_documents`, `_get_accessible_document` |
| `backend/app/api/routes/users.py` | `ROLE_PERMISSIONS` (aucun droit pour `EMPLOYEE`) |
| `backend/app/api/routes/audit.py` | `ROLE_PERMISSIONS` (lecture `ADMIN` uniquement) |
| `backend/app/api/routes/departments.py` | `ROLE_PERMISSIONS` (lecture pour les deux rôles) |

## 2. Matrice des permissions

Légende : ✅ autorisé · ❌ refusé · 🔒 refusé **et** journalisé (`FAILURE` dans `audit_logs`)

### 2.1 Documents

| Action | Endpoint | Anonyme | EMPLOYEE — même département | EMPLOYEE — autre département | ADMIN |
|---|---|---|---|---|---|
| Lister | `GET /documents/get_all` | ❌ 401 | ✅ (privés et publics) | — filtré (non listé) | ✅ tous |
| Rechercher | `GET /documents/search_document` | ❌ 401 | ✅ (privés et publics) | — filtré (aucun résultat) | ✅ tous |
| Consulter les métadonnées | `GET /documents/{id}` | ❌ 401 | ✅ | 🔒 403 (privé **et** public) | ✅ |
| Consulter le contenu | `GET /documents/{id}/content` | ❌ 401 | ✅ | 🔒 403 (privé **et** public) | ✅ |
| Créer | `POST /documents/add` | ❌ 401 | 🔒 403 | 🔒 403 | ✅ |
| Modifier | `PUT /documents/update/{id}` | ❌ 401 | 🔒 403 | 🔒 403 | ✅ |
| Supprimer | `DELETE /documents/delete/{id}` | ❌ 401 | 🔒 403 | 🔒 403 | ✅ |

Les consultations et recherches réussies sont journalisées (`SUCCESS`).

### 2.2 Utilisateurs, invitations, départements, audit

| Action | Endpoint | Anonyme | EMPLOYEE | ADMIN |
|---|---|---|---|---|
| Lister les utilisateurs | `GET /users` | ❌ 401 | ❌ 403 | ✅ |
| Modifier un utilisateur | `PATCH /users/{id}` | ❌ 401 | ❌ 403 | ✅ (journalisé) |
| Activer / désactiver | `POST /users/{id}/status` | ❌ 401 | ❌ 403 | ✅ (journalisé ; dernier admin actif protégé) |
| Inviter un utilisateur | `POST /users/invitations` | ❌ 401 | ❌ 403 | ✅ (journalisé) |
| Accepter une invitation | `POST /auth/accept-invitation` | ✅ (jeton d'invitation valide, usage unique) | — | — |
| Lister les départements | `GET /departments` | ❌ 401 | ✅ | ✅ |
| Consulter le journal d'audit | `GET /audit` | ❌ 401 | ❌ 403 | ✅ |
| Exporter le journal d'audit | `GET /audit/export` | ❌ 401 | ❌ 403 | ✅ (export journalisé) |
| Consulter les alertes de sécurité | `GET /audit/alerts` | ❌ 401 | 🔒 403 | ✅ |

### 2.3 Authentification et session

| Cas | Résultat |
|---|---|
| Connexion valide | ✅ jeton JWT, journalisé `SUCCESS` |
| Mauvais mot de passe / email inconnu | ❌ 401, journalisé `FAILURE` |
| Compte inactif | ❌ 401 |
| Jeton absent (pas d'en-tête, ou schéma autre que Bearer) | ❌ 401, journalisé `TOKEN_MISSING` (anonyme, valeur d'en-tête jamais stockée) |
| Jeton présent mais rejeté (invalide, signature falsifiée, claims manquants) | ❌ 401, journalisé `AUTHENTICATION_FAILED` (anonyme) |
| Jeton authentique mais expiré | ❌ 401, journalisé `SESSION_EXPIRED` (anonyme) |
| Jeton révoqué (après déconnexion) | ❌ 401 |
| Déconnexion | ✅ révocation du jeton, journalisée `SUCCESS` |

## 3. Correspondance règles ↔ tests automatisés

Exécution : `cd backend && uv run pytest`.

| Règle | Test(s) |
|---|---|
| EMPLOYEE ne liste que son département | `tests/test_documents.py::test_employee_sees_only_own_department_documents` |
| Recherche limitée au département de l'EMPLOYEE, et journalisée | `tests/test_documents.py::test_search_respects_department_scope_and_is_audited` |
| Document privé d'un autre département → 403 + audit `FAILURE` | `tests/test_documents.py::test_employee_cannot_view_private_document_from_another_department`, `::test_content_respects_department_scope` |
| Document **public** d'un autre département → 403 + audit `FAILURE` (`/documents/{id}` et `/content`) | `tests/test_documents.py::test_employee_cannot_access_public_document_from_another_department[]`, `[/content]` |
| EMPLOYEE accède aux documents de son département (`/documents/{id}` et `/content`) | `tests/test_documents.py::test_employee_can_access_own_department_documents[]`, `[/content]` |
| ADMIN lit tous les contenus | `tests/test_documents.py::test_admin_can_read_document_content` |
| EMPLOYEE ne peut ni créer, ni modifier, ni supprimer (403 + audit `FAILURE`) | `tests/test_documents.py::test_employee_cannot_create_update_or_delete_documents` |
| ADMIN crée / modifie / supprime (journalisé) | `tests/test_documents.py::test_admin_can_upload_document_and_upload_is_audited`, `::test_update_archives_old_file_and_preserves_document_id`, `::test_delete_removes_file_and_index_entry_and_is_audited` |
| Routes documents exigent l'authentification | `tests/test_documents.py::test_document_routes_require_authentication`, `::test_content_requires_authentication` |
| Gestion des utilisateurs réservée à ADMIN | `tests/test_users.py::test_users_require_authentication`, `::test_employee_user_list_permission_is_explicitly_checked`, `::test_employee_cannot_update_user`, `::test_employee_cannot_change_user_status` |
| Dernier admin actif protégé | `tests/test_users.py::test_cannot_deactivate_last_active_admin`, `::test_cannot_demote_last_active_admin` |
| Départements lisibles par tout utilisateur authentifié | `tests/test_users.py::test_department_list_requires_authentication`, `::test_employee_can_list_departments` |
| Invitations réservées à ADMIN | `tests/test_invitations.py::test_invitation_requires_admin`, `::test_employee_cannot_create_invitation` |
| Journal d'audit réservé à ADMIN | `tests/test_audit.py::test_audit_requires_admin`, `::test_audit_export_requires_admin` |
| Alertes de sécurité réservées à ADMIN | `tests/test_suspicious_activity.py::test_alerts_require_admin` |
| Jeton expiré journalisé `SESSION_EXPIRED`, jamais `AUTHENTICATION_FAILED` | `tests/test_audit.py::test_expired_token_is_audited_as_session_expiry_anonymously` |
| Règles de détection R1a à R5, `DOCUMENT_ENUMERATION` et `SEARCH_BURST` | `tests/test_suspicious_activity.py` (voir `docs/SEC-01-detection-comportements-suspects.md`) |
| Authentification et session | `tests/test_auth.py` (connexion, échec, compte inactif, jeton invalide / expiré / révoqué, déconnexion) |
| Jeton absent : un seul événement `TOKEN_MISSING`, jamais `AUTHENTICATION_FAILED` | `tests/test_audit.py::test_missing_token_is_audited_as_token_missing`, `::test_non_bearer_scheme_is_audited_as_token_missing` |
| Jeton présent mais rejeté : un seul événement `AUTHENTICATION_FAILED` persisté (anonyme si le jeton n'est pas vérifié) | `tests/test_audit.py::test_invalid_token_is_audited`, `::test_expired_token_with_invalid_signature_is_an_authentication_failure`, `::test_revoked_token_is_audited_with_user`, `::test_inactive_user_is_audited_with_user` |
| Chaque 403 de `require_permission` produit un seul événement `ACCESS_DENIED` persisté | `tests/test_audit.py::test_employee_denied_audit_log_is_audited_once`, `::test_employee_denied_user_update_is_audited_once` |
| Refus documents : un seul `DOCUMENT_VIEW FAILURE`, sans `ACCESS_DENIED` en doublon | `tests/test_documents.py::test_document_denial_is_audited_once_without_generic_refusal[]`, `[/content]` |
| Aucun jeton, mot de passe ni secret dans le journal ou l'export | `tests/test_audit.py::test_refusal_events_never_store_token_or_secret` |
| Requêtes autorisées : aucun événement de refus | `tests/test_audit.py::test_authorized_requests_create_no_refusal_event` |
| Filtrage du journal sur les actions de refus | `tests/test_audit.py::test_audit_filters_on_refusal_actions` |

## 4. Limites connues

- Les refus 401 (`get_current_user`) et 403 (`require_permission`) sont
  journalisés de façon centralisée dans `backend/app/api/dependencies.py`
  (`TOKEN_MISSING` / `AUTHENTICATION_FAILED` / `SESSION_EXPIRED` / `ACCESS_DENIED`),
  avec leur motif dans `details.reason` (voir
  `docs/SEC-01-detection-comportements-suspects.md`, « Format du journal »).
  La route appelée n'est pas enregistrée.
- Les appels anonymes répétés sont tous journalisés, sans limitation de débit :
  la détection (`GET /audit/alerts`) les signale mais ne les bloque pas.
- Les permissions sont déclarées dans plusieurs dictionnaires `ROLE_PERMISSIONS`
  répartis par route ; il n'existe pas encore de source unique.
- Le chat RAG existe dans le code (`POST /rag/chat`, `POST /rag/chat/stream`,
  `backend/app/api/rag.py`) mais n'est **pas ouvert aux utilisateurs** : il est
  désactivé par défaut (`RAG_ENABLED=false`, réponse 503) et, une fois activé, il
  refuse **tout** utilisateur, `ADMIN` compris : `require_permission("search")`
  puis contrôle du périmètre par département, dont la table
  (`DEPARTMENT_LAYERS`, `Agentic_RAG/retriever_tools.py`) est **vide** tant que la
  décision H10 n'est pas prise (403, événement `ACCESS_DENIED` /
  `DEPARTMENT_FORBIDDEN`). Le seul fournisseur du code est Groq (`ChatGroq`), dont
  l'utilisation exige `ALLOW_EXTERNAL_LLM=true` (faux par défaut) ; la décision
  H1 reste ouverte. Les tests via le chat existent avec un faux fournisseur et une
  correspondance département → couche **propre aux tests** (§ 5.6). Les
  identifiants de couche du pipeline RAG (`business_layer` dans
  `Agentic_RAG/Data_preparation/ingestion.py`) ne correspondent toujours pas aux
  noms de départements en base (H10).

## 5. SEC-02 T4 — Non-divulgation et résistance à l'injection (clôture partielle)

Aucun prompt n'a été envoyé à un véritable fournisseur LLM lors des tests : tous
les tests utilisent un faux fournisseur. Le code de l'agent RAG
(`backend/app/services/Agentic_RAG/`) n'enverrait un prompt à Groq que si le RAG
est activé, avec `ALLOW_EXTERNAL_LLM=true`, et pour un utilisateur dont le
département a une couche (H10, non décidée). La passerelle interne (US 07 T5,
`llm_gateway.py`) construit un prompt et l'envoie à un fournisseur **fourni par
l'appelant** ; elle n'est appelée par aucune route. L'agent RAG passe par un autre
chemin : le contexte de requête (`llm_request.py`) et les adaptateurs
(`Agentic_RAG/llm_adapters.py`), § 5.6. SEC-02 T4 reste **close partiellement** :
contrôles **amont** sur la préparation des données
(`Agentic_RAG/Data_preparation/`, §§ 5.1 à 5.3), au niveau de la passerelle
(§ 5.5) et de l'agent RAG (§ 5.6) ; la résistance à l'injection face à un vrai
modèle reste à tester. Bilan complet : `docs/SEC-02-rapport-tests-securite.md`.

### 5.1 Contrôles réalisés

Fichier : `backend/tests/test_rag_ingestion_security.py` (`llama_index`,
`qdrant_client` et `dotenv` remplacés par des modules factices ; aucun serveur
contacté ; aucun code de production modifié).

| Contrôle | Vérifie | Tests |
|---|---|---|
| **N1** Étiquetage obligatoire | Un PDF absent du registre `DOCUMENTS_METADATA` est refusé à l'ingestion ; chaque document déclaré a un `doc_type` (`public`/`prive`) et une `business_layer` valides ; le registre couvre exactement les PDF de `data/documents` ; la normalisation applique les étiquettes du registre | `test_n1_undeclared_pdf_is_refused[…]`, `test_n1_every_declared_document_has_valid_labels`, `test_n1_registry_covers_the_whole_corpus`, `test_n1_normalisation_applies_the_registry_labels` |
| **N2** Métadonnées visibles par le LLM | Après `normalize_metadata`, `file_path` figure dans `excluded_llm_metadata_keys` : seules `source`, `page`, `doc_type` et `business_layer` restent visibles ; aucune métadonnée n'est intégrée aux vecteurs | `test_n2_file_path_is_excluded_from_llm_metadata`, `test_n2_no_metadata_is_embedded_in_vectors` |
| **N3** Points Qdrant | Chaque point stocké porte la `business_layer` et le `doc_type` de son document ; ces deux champs sont indexés (`keyword`) pour un filtrage par couche ; présence de `file_path` constatée (voir 5.3) | `test_n3_every_stored_point_carries_its_layer_labels`, `test_n3_layer_labels_are_indexed_for_filtering`, `test_n3_known_finding_file_path_is_stored_in_qdrant` |

### 5.2 Limites de ces contrôles

- Ce sont des **prérequis**, pas une preuve de non-transmission : aucun test ne
  vérifie ce qui est réellement envoyé à un fournisseur LLM, puisque rien ne l'est.
- N2 vérifie la **déclaration** d'exclusion (`excluded_llm_metadata_keys`) ;
  l'application effective de cette exclusion dépend de `llama_index`, qui n'est pas
  installé et n'est pas exécuté par les tests.
- Les modules factices remplacent `llama_index` et `qdrant_client` : le découpage
  sémantique, la vectorisation et le filtrage réel par Qdrant ne sont pas testés.
- Les couches RAG (`business_layer`) ne correspondent pas encore aux départements
  en base (voir 4.) : le filtrage par couche ne peut pas encore être relié à
  l'utilisateur connecté.

### 5.3 Constat : `file_path` stocké dans Qdrant (non corrigé)

`store_nodes` enregistre dans le payload Qdrant le texte **et toutes les
métadonnées**, y compris `file_path` (chemin interne de la machine d'ingestion).
Ce champ est exclu de la vue LLM par `llama_index`, mais il reste présent dans les
données récupérables : un futur retriever qui transmettrait les payloads tels quels
le divulguerait. Ce constat est vérifié par
`test_n3_known_finding_file_path_is_stored_in_qdrant` et **n'est pas corrigé** ; il
relève de la minimisation du contexte (US 7, T4).

Mise à jour (SEC-02 T6) : la minimisation du contexte (`rag_context.py`, US 7 T4)
ne transmet que `source` et `page` et écarte toujours `file_path`
(`tests/test_rag_context.py::test_internal_metadata_are_never_sent_even_with_a_vault`,
`tests/test_llm_gateway.py::test_internal_metadata_never_reach_the_assembler`). Le
**stockage** de `file_path` dans Qdrant reste, lui, inchangé (tâche I-1).

### 5.4 Scénarios en attente de l'endpoint RAG/chat et de la passerelle

Ils nécessitent la récupération (I-2), la construction du prompt (I-3), l'endpoint
(I-4), le filtrage par couche (I-5) et, pour les données sensibles, la
pseudonymisation, la minimisation et la passerelle (US 7, T3 à T5). Ils seront
testés avec un faux fournisseur LLM qui capture les prompts, sur un corpus
synthétique contenant un marqueur unique par couche :

| Scénario | Critère de réussite | État (SEC-02 T6) |
|---|---|---|
| Question dont la réponse est dans une autre couche | Aucun extrait de cette couche dans le prompt capturé ni dans la réponse | 🟡 Démontré pour le **prompt capturé** au niveau de la passerelle (§ 5.5) ; réponse d'un vrai modèle et parcours via le chat en attente (H1, I-2, I-4) |
| Filtrage avant **et** après récupération | Un extrait hors couche est écarté avant l'envoi au fournisseur | 🟡 **Après** récupération : démontré (§ 5.5, § 5.6) ; **avant** (filtre dans la requête Qdrant) : implémenté et démontré sur un Qdrant local en mémoire avec une correspondance de test (§ 5.6) ; pour de vrais utilisateurs, en attente de H10 |
| Plusieurs tours de conversation | Le filtre s'applique à chaque tour | 🟡 Démontré requête par requête (§ 5.5) ; conversation à plusieurs tours en attente (I-4, décision D3) |
| Énumération (« le document X existe-t-il ? ») | La réponse ne confirme pas l'existence d'un document refusé | ⏳ En attente (I-4, H1). Seul fait démontré : sans extrait autorisé, le fournisseur n'est pas appelé |
| Données sensibles dans les extraits | Aucune donnée sensible non masquée ni métadonnée interne (`file_path`…) dans le prompt capturé | ✅ Démontré au niveau de la passerelle (§ 5.5), pour les valeurs que T3 détecte ou que l'appelant déclare |
| Injection directe dans la question | Aucun document hors couche, aucun contournement des consignes | ⏳ En attente d'un vrai modèle (H1). Le contenu hors couche n'atteint jamais le prompt, ce qui n'est pas une preuve de résistance à l'injection |
| Injection indirecte dans un document autorisé | La consigne cachée n'est pas exécutée et l'incident est journalisé | ⏳ En attente (H1) ; aucune détection d'injection n'est prévue dans la passerelle (décision D1) |
| Jeton absent, invalide ou d'un autre rôle | 401/403 et **aucun** appel au fournisseur | 🟡 Route authentifiée (`get_current_user`, `require_permission`) : sans jeton, 401 sans appel au service ; utilisateur sans périmètre, 403 audité sans appel au service (§ 5.6). Jeton invalide sur `/rag/*` non testé spécifiquement (même dépendance que les autres routes) |

### 5.5 Contrôles au niveau de la passerelle (US 7, T3 à T5)

Fichiers : `backend/app/services/llm_gateway.py`, `rag_context.py`,
`pseudonymization.py` ; tests `backend/tests/test_llm_gateway.py` (98),
`test_rag_context.py` (121), `test_pseudonymization.py` (109),
`test_gateway_audit.py` (27). Un **faux fournisseur** capture le prompt réellement
transmis ; un **faux récupérateur** renvoie des extraits synthétiques portant un
marqueur unique par couche. Aucune route, aucun vrai modèle.

| Contrôle | Vérifie | Tests |
|---|---|---|
| Cloisonnement dans le prompt | Aucun extrait d'une autre couche, d'une source exclue (note C3), sans couche ou sans source dans le prompt capturé ; filtre réappliqué à chaque requête | `test_llm_gateway.py::test_only_authorised_layers_reach_the_captured_prompt`, `::test_each_request_applies_its_own_layer_filter` |
| Question protégée | Noms déclarés, email et IBAN absents de la question reçue par le récupérateur et l'assembleur, et du prompt | `test_llm_gateway.py::test_the_question_is_protected_before_retrieval_and_assembly` |
| Métadonnées | Seules `source` et `page` sont transmises ; jamais `file_path`, `chunk_index` ni identifiant | `test_llm_gateway.py::test_internal_metadata_never_reach_the_assembler` |
| Pas d'appel inutile | Aucun appel au fournisseur sans extrait autorisé, ni au-delà de la taille maximale du prompt | `::test_no_authorised_context_means_no_provider_call`, `::test_a_prompt_over_the_limit_is_refused_without_provider_call` |
| Restauration | Seuls les jetons présents dans le prompt envoyé sont restaurés, en un seul passage ; jetons étrangers, inventés ou d'une requête précédente laissés tels quels | `::test_only_tokens_of_the_sent_prompt_are_restored`, `::test_a_token_of_a_previous_request_is_never_restored`, `::test_restore_is_called_once_per_answer` |
| Données après la requête | Table de pseudonymisation abandonnée après succès, erreur ou interruption ; aucune question brute ni valeur connue dans les cadres conservés | `::test_any_failure_drops_the_vault_and_hides_the_original_error`, `::test_v1_…`, `::test_v2_…`, `::test_v3_…` |
| Journalisation | Un événement `LLM_CALL` sans contenu par appel ; extraits écartés comptés par motif | `test_gateway_audit.py` (27), scénarios `LLM_CALL` de `test_audit_traceability.py` |

Limites de ces contrôles :

- Ils portent sur le **prompt capturé**, pas sur la réponse d'un vrai modèle.
- Les couches autorisées sont fournies par l'appelant : leur calcul à partir de
  l'utilisateur connecté n'existe pas (H10).
- La passerelle n'est appelée par aucune route : l'agent RAG utilise le contexte
  de requête et les adaptateurs (§ 5.6), pas `llm_gateway.ask`.
- Les données sensibles non déclarées et non détectées par T3 (noms sans liste de
  valeurs connues, adresses, téléphones nationaux) ne sont pas masquées
  (décisions D1 à D3 de T3).

### 5.6 Contrôles au niveau de l'agent RAG

Fichiers : `backend/app/api/rag.py`, `Agentic_RAG/rag_agent.py`,
`Agentic_RAG/llm_adapters.py`, `Agentic_RAG/retriever_tools.py`,
`Agentic_RAG/search_tools.py`. Tests (groupe de dépendances `rag`, exécutés par une
étape dédiée de la CI) : `backend/tests/test_rag_access.py`, `test_rag_audit.py`,
`test_rag_llm_adapters.py`, `test_main_rag.py`. Faux fournisseur, Qdrant local en
mémoire, correspondance département → couche **propre aux tests** : aucun vrai
modèle, aucun utilisateur réel.

| Contrôle | Vérifie | Tests |
|---|---|---|
| Fournisseur unique | Groq via `ChatGroq` seulement, jusqu'au client du SDK ; aucun autre client | `test_rag_llm_adapters.py::test_the_request_really_goes_through_chatgroq`, `::test_no_direct_provider_client_outside_the_adapters` |
| Consentement H1 | Sans `ALLOW_EXTERNAL_LLM`, rien n'est construit ni envoyé | `test_rag_llm_adapters.py::test_without_external_consent_nothing_is_built_bound_or_sent` |
| Refus par défaut | Département sans couche (tous aujourd'hui), `ADMIN` compris : 403 audité, aucun appel | `test_rag_access.py::test_the_route_refuses_without_a_mapping_and_audits_it`, `::test_the_route_requires_authentication` |
| Cloisonnement de la récupération | Filtre d'accès dans la requête Qdrant, revérifié après ; le filtre du self-query ne peut que restreindre | `test_rag_access.py::test_a_user_cannot_retrieve_a_layer_outside_its_scope`, `::test_the_llm_filter_can_only_narrow_the_access_filter`, `test_rag_audit.py::test_route_sources_stay_in_the_user_scope` |
| Contexte envoyé | Passage par `rag_context.build_context` (T4) ; seules `source` et `page` transmises ; note C3 exclue | `test_rag_access.py::test_forbidden_metadata_are_never_sent` et tests voisins |
| Protection | Masquage et pseudonymisation avant envoi ; restauration des seuls jetons envoyés | `test_rag_llm_adapters.py::test_everything_sent_is_protected_before_the_provider`, `::test_only_the_tokens_sent_by_this_call_are_restored` |

Limites :

- **Noms de clients** : le contexte de requête du RAG ne reçoit aucune liste de
  valeurs connues ; seuls email, IBAN, carte et téléphone sont masqués, les noms
  ne sont pas pseudonymisés (`test_rag_llm_adapters.py::test_the_real_request_context_masks_before_the_provider`).
- **Streaming** non progressif : la réponse est émise en un seul fragment, après
  restauration.
- Couches d'un utilisateur (H10), périmètre de l'`ADMIN` (H11) et accès du Front
  Office à la procédure LBC-FT (H4) : **non décidés**.
