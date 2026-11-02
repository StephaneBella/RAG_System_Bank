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
| Règles de détection R1a à R5 | `tests/test_suspicious_activity.py` (voir `docs/SEC-01-detection-comportements-suspects.md`) |
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
- Le chat RAG n'existe pas encore : les tests de non-divulgation et de
  cloisonnement inter-couches via le chat (SEC-02) restent à écrire. Les
  identifiants de couche du pipeline RAG (`business_layer` dans
  `Agentic_RAG/Data_preparation/ingestion.py`) ne correspondent pas encore aux
  noms de départements en base.
