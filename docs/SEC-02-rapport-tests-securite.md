# SEC-02 T6 — Rapport de tests de sécurité

Bilan des contrôles de sécurité **effectivement exécutés** sur RAG_System_Bank :
contrôles vérifiés, résultats, limites restantes et tests qui ne peuvent pas encore
être exécutés. Ce rapport ne décrit que ce qui est démontré par le code et les
tests ; les dépendances externes sont signalées comme telles.

## 1. Contexte

| Élément | Valeur |
|---|---|
| Date d'exécution | 2026-11-02 |
| Branche | `temgoua_feat/auth-user-access-audit` |
| Commit de référence | HEAD `2017860ed14cd9ca1ebaea88c4c058d6f0b4d809` : les travaux de SEC-01, SEC-02 et US 07 (T1 à T5) sont désormais commités (commits 1 à 11 du plan) |
| Environnement backend | Python 3.12.14, pytest 9.1.1, base SQLite en mémoire par test |
| Environnement frontend | Node 24.21.0, vitest 2.1.9 |
| Fournisseur LLM | **Aucun** (décision H1 ouverte) : un faux fournisseur capture le prompt |
| Route de chat | **Aucune** (tâche I-4) ; la passerelle est testée comme service interne |
| Récupération RAG | **Aucune** (tâche I-2) ; un faux récupérateur renvoie des extraits synthétiques |

## 2. Méthode

- **Tests automatisés** rejouables : `cd backend && uv run pytest` ;
  `cd frontend && pnpm test` (ou `node_modules/.bin/vitest run`).
- **Contrôles statiques** : Ruff (format et lint) pour le backend ; ESLint,
  `tsc --noEmit` et build Vite pour le frontend (mêmes commandes que la CI).
- **Composants simulés**, jamais présentés comme réels : faux fournisseur LLM, faux
  récupérateur et faux assembleur de prompt (passerelle) ; modules factices
  `llama_index`, `qdrant_client` et `dotenv` (préparation des données RAG).
- **Données synthétiques** uniquement : noms fictifs, adresses `example.org`, IBAN et
  numéros de carte d'exemple, marqueurs uniques par couche. Le corpus réel n'est lu
  que pour vérifier l'étiquetage (N1) et la préservation du contenu métier des PDF
  privés fictifs.
- **Revues de sécurité indépendantes**, en lecture seule, sur T3, T4 et T5 ; chaque
  défaut retenu a été corrigé puis couvert par un test (voir § 4).

## 3. Contrôles réalisés et résultats

### 3.1 Contrôle d'accès et cloisonnement de la bibliothèque (SEC-02 T1 à T3)

Matrice des droits et correspondance règles ↔ tests :
`docs/SEC-02-matrice-des-droits.md` §§ 1 à 3.

| Domaine | Fichiers | Tests | Résultat |
|---|---|---|---|
| Cloisonnement strict par département (privé **et** public), lecture, contenu, recherche | `tests/test_documents.py` | 37 | ✅ réussis |
| Gestion des utilisateurs réservée à `ADMIN`, invitations | `tests/test_users.py`, `tests/test_invitations.py` | 22 + 6 | ✅ réussis |
| Authentification : jeton absent, invalide, expiré, révoqué, compte inactif | `tests/test_auth.py` | 11 | ✅ réussis |

### 3.2 Journalisation et détection (SEC-01, utilisées par SEC-02)

| Domaine | Fichiers | Tests | Résultat |
|---|---|---|---|
| Refus 401/403 journalisés une seule fois, sans jeton ni secret ; format du journal ; filtres | `tests/test_audit.py` | 40 | ✅ réussis |
| Chaque action (16, dont `LLM_CALL`) enregistrée, consultable via `GET /audit` et filtrable | `tests/test_audit_traceability.py` | 34 | ✅ réussis |
| Détection R1 à R5, énumération de documents, rafale de recherches, garde-fous | `tests/test_suspicious_activity.py` | 137 | ✅ réussis |
| Interface d'audit : 16 actions traduites, filtres, dates, libellés `LLM_CALL` / `LLM_GATEWAY` | `frontend/src/pages/admin/AdminAuditPage.test.tsx` | 17 | ✅ réussis |

### 3.3 Non-divulgation : contrôles amont de la préparation des données (SEC-02 T4)

| Contrôle | Fichier | Tests | Résultat |
|---|---|---|---|
| N1 étiquetage obligatoire ; N2 `file_path` exclu de la vue LLM ; N3 étiquettes de couche dans Qdrant (constat `file_path` stocké) | `tests/test_rag_ingestion_security.py` | 11 | ✅ réussis |

### 3.4 Pseudonymisation et masquage (US 07 T3)

`backend/app/services/pseudonymization.py` — `tests/test_pseudonymization.py`,
**109 tests réussis**.

- Masquage irréversible : IBAN (format, clé non vérifiée), carte (clé Luhn exigée, y
  compris au milieu d'une suite de chiffres), email, téléphone international.
- Pseudonymisation réversible par une table en mémoire propre à chaque instance ;
  jetons porteurs d'un identifiant aléatoire de 128 bits ; un jeton étranger,
  inventé, déformé ou préexistant n'est jamais restauré.
- Contenu métier préservé (montants, seuils, références, PDF privés fictifs).
- Arguments non conformes refusés explicitement, sans protection partielle ; aucune
  valeur dans `repr`, `str`, les erreurs ou les journaux.

### 3.5 Minimisation du contexte RAG (US 07 T4)

`backend/app/services/rag_context.py` — `tests/test_rag_context.py`,
**121 tests réussis**.

- Cloisonnement après récupération : tout extrait sans couche ou hors des couches
  autorisées est écarté (`test_extracts_of_another_layer_are_discarded`,
  `test_extracts_without_a_layer_are_discarded`).
- Sources exclues écartées avant toute limite et jamais pseudonymisées
  (`test_excluded_sources_are_discarded_before_any_limit`).
- Métadonnées : `source` et `page` seulement ; `file_path`, `chunk_index` et
  identifiants jamais transmis (`test_internal_metadata_are_never_sent_even_with_a_vault`).
- Limites obligatoires, jamais de troncature (`test_an_extract_is_never_truncated`) ;
  doublons retirés ; tri par score sans score inventé ; compteurs sans contenu.

### 3.6 Passerelle LLM interne et cloisonnement dans le prompt (US 07 T5, SEC-02 T5)

`backend/app/services/llm_gateway.py` — `tests/test_llm_gateway.py`,
**98 tests réussis**, avec un faux fournisseur qui capture le prompt réellement
transmis.

| Contrôle démontré | Test(s) |
|---|---|
| Aucun extrait d'une autre couche, d'une source exclue, sans couche ou sans source dans le prompt capturé | `test_only_authorised_layers_reach_the_captured_prompt` |
| Le filtre de couche est réappliqué à chaque requête | `test_each_request_applies_its_own_layer_filter` |
| Question masquée et pseudonymisée avant le récupérateur et l'assembleur | `test_the_question_is_protected_before_retrieval_and_assembly` |
| Aucune métadonnée interne transmise | `test_internal_metadata_never_reach_the_assembler` |
| Aucun appel au fournisseur sans contexte autorisé, ou au-delà de la taille maximale du prompt | `test_no_authorised_context_means_no_provider_call`, `test_a_prompt_over_the_limit_is_refused_without_provider_call` |
| Restauration limitée aux jetons du prompt envoyé, une seule fois par réponse | `test_only_tokens_of_the_sent_prompt_are_restored`, `test_restore_is_called_once_per_answer` |
| Table de pseudonymisation abandonnée après succès, erreur ou interruption ; aucune donnée brute dans les cadres conservés | `test_any_failure_drops_the_vault_and_hides_the_original_error`, `test_v2_…`, `test_v3_…` |
| Compte rendu sans contenu | `test_the_report_holds_no_content` |

### 3.7 Journalisation des appels à la passerelle (US 07 T5, étape 3)

`log_gateway_call` (`backend/app/services/audit.py`) — `tests/test_gateway_audit.py`,
**27 tests réussis**, et 2 scénarios `LLM_CALL` dans `tests/test_audit_traceability.py`.

- Un événement `LLM_CALL` / `LLM_GATEWAY` par appel ; `SUCCESS` pour `ANSWERED`,
  `NO_AUTHORIZED_CONTEXT` et `PROMPT_TOO_LONG`, `FAILURE` pour `FAILED`.
- `details.llm_call` : liste fermée de 11 clés (énumérations, entiers non négatifs,
  `null`) ; extraits écartés comptés par motif.
- Aucune donnée sensible dans les colonnes, `GET /audit` ni l'export CSV ; un
  `caller_id` incohérent est refusé sans écriture.
- R1 à R4 non déclenchées par `LLM_CALL` ; R5 inclut `LLM_CALL` (60 événements en
  10 minutes par défaut).

## 4. Synthèse des résultats

| Contrôle | Résultat |
|---|---|
| Suite backend complète (`pytest`) | **653 réussis**, 0 échec |
| Suite frontend complète (`vitest run`) | **24 réussis**, 0 échec |
| Ruff `format --check` / `check` | conformes (80 fichiers) |
| ESLint (`--max-warnings 0`) / `tsc --noEmit` / build Vite | conformes |

Défauts trouvés par les revues indépendantes, tous corrigés et couverts par des tests :

| Revue | Défauts | Exemples |
|---|---|---|
| T3 | F1 à F6 et constat A | Carte cachée par un nombre voisin ; montant pris pour un téléphone ; jetons non liés à leur table ; chaîne seule acceptée comme liste de valeurs connues |
| T4 | F1 à F5 (F6 à F8 reportés à T5 et I-2) | Valeurs connues en générateur laissées en clair ; métadonnées non comptées dans le budget ; sous-classe de `str` contournant le budget |
| T5 | V1 à V7, et une fuite révélée par les tests | Question brute dans la trace d'une erreur ; table conservée par une exception ou un cadre gardé par un composant ; nom de classe d'exception divulgué |

## 5. Limites restantes

- **Rien n'est branché en production** : aucune route n'appelle la passerelle ni
  `log_gateway_call` (I-4). Les garanties de T3 à T5 sont démontrées sur les modules,
  pas sur un parcours utilisateur réel.
- **Composants simulés** : le comportement d'un vrai modèle, d'un vrai récupérateur et
  du filtrage réel par Qdrant n'est pas testé.
- **`file_path` stocké dans Qdrant** : non corrigé (I-1) ; T4 empêche sa transmission
  au LLM (`docs/SEC-02-matrice-des-droits.md` § 5.3).
- **Défaut connu, non corrigé** : un document existant dont le fichier a disparu
  provoque une erreur 500 sans événement d'audit (`FILE_NOT_FOUND` non atteignable,
  `docs/SEC-01-detection-comportements-suspects.md`, Limites).
- **Délai d'appel** transmis au fournisseur mais non imposé par la passerelle.
- **Composants de confiance** : un composant exécuté dans le même processus peut
  atteindre n'importe quel objet pendant son exécution.
- **R5** : un usage légitime et intensif du chat peut déclencher `ACTIVITY_SPIKE`.
- **Valeurs non décidées** : limites (H6), conservation (H7), longueur de la question
  (H8), couches d'un utilisateur (H10), périmètre de l'`ADMIN` (H11).

## 6. Tests encore impossibles à exécuter

| Scénario (`docs/SEC-02-matrice-des-droits.md` § 5.4) | Dépendance bloquante |
|---|---|
| Cloisonnement inter-couches **via le chat**, de bout en bout | I-2 (récupération), I-4 (route), H10 (couches d'un utilisateur) |
| Filtrage **avant** la recherche | I-5 (filtre dans la requête Qdrant), H10 |
| Plusieurs tours de conversation dans un même échange | I-4 (historique, décision D3) |
| Énumération (« le document X existe-t-il ? ») sur une réponse réelle | I-4 et H1 (vrai modèle) |
| Injection directe et indirecte | H1 (vrai modèle) |
| Jeton absent, invalide ou d'un autre rôle : 401/403 et **aucun** appel au fournisseur | I-4 (route authentifiée) |

## 7. Bilan par tâche SEC-02

| Tâche | État |
|---|---|
| T1 Matrice des droits | ✅ Terminée |
| T2 Scénarios RBAC | ✅ Terminée |
| T3 Automatisation | ✅ Terminée |
| T4 Non-divulgation et injection | 🟡 Partielle : contrôles amont (N1 à N3) et non-divulgation au niveau de la passerelle démontrés ; résistance à l'injection en attente de H1 |
| T5 Cloisonnement inter-couches via le chat | 🟡 Partielle : démontré au niveau de la passerelle ; « via le chat » en attente de I-2, I-4 et H10 |
| T6 Rapport de tests de sécurité | ✅ Ce document |

## 8. Documents liés

- `docs/SEC-02-matrice-des-droits.md` — matrice, correspondance règles ↔ tests, § 5.
- `docs/SEC-01-detection-comportements-suspects.md` — journal et détection.
- `docs/US-07-cartographie-donnees-sensibles.md` — cartographie (T1).
- `docs/US-07-politique-minimisation.md` — politique et statuts des règles (T2).
