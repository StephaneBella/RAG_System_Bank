# SEC-01 — Détection des comportements suspects

Détection **calculée à la demande** à partir de la table `audit_logs`, sans
table d'alertes ni écriture. Consultation réservée aux administrateurs :

```
GET /audit/alerts?lookback_hours=24&rule=<RÈGLE>&severity=<MEDIUM|HIGH>
```

- `lookback_hours` : période d'analyse, 1 à 168 h (défaut `ALERT_LOOKBACK_HOURS=24`).
- Réponse triée par sévérité (HIGH d'abord) puis par dernière occurrence.
- Une alerte par couple règle × sujet (utilisateur ou IP), décrivant la fenêtre
  la plus dense : `event_count`, `threshold`, `window_minutes`, `first_seen`,
  `last_seen`, `details`.
- Les alertes sont construites uniquement à partir des colonnes d'audit, qui
  ne contiennent ni jeton, ni mot de passe, ni secret.

Code : `backend/app/services/suspicious_activity.py`. Seuils :
`backend/app/core/config.py` (variables `ALERT_*`, voir `backend/.env.example`).

Les seuils sont validés au démarrage (une valeur invalide empêche l'application
de démarrer) : seuils, fenêtres, jours de référence et nombre minimal de
fenêtres actives ≥ 1 ; facteur R5 ≥ 1 ; `ALERT_LOOKBACK_HOURS` entre 1 et 168 ;
`ALERT_SPIKE_BASELINE_DAYS × 1440` divisible par `ALERT_SPIKE_WINDOW_MINUTES`.

## Principe commun

Pour chaque sujet, les événements sont triés par date et parcourus avec une
fenêtre glissante semi-ouverte `[t, t + W)`. Le pic est le nombre maximal
d'événements (ou d'éléments distincts pour R3) dans une telle fenêtre.
Sévérité : `HIGH` si pic ≥ 2 × seuil, sinon `MEDIUM`.

## Règles

| Règle | Événements comptés | Sujet | Seuil par défaut |
|---|---|---|---|
| **R1a `LOGIN_BRUTE_FORCE`** | `LOGIN` + `FAILURE` | `user_id` (compte visé) / IP | 5 en 15 min / 10 en 15 min |
| **R1b `TOKEN_ABUSE`** | `AUTHENTICATION_FAILED` : jeton présent mais rejeté (jamais `SESSION_EXPIRED`) | `user_id` connu (jeton révoqué, compte inactif) / IP pour les événements anonymes | 5 en 15 min / 10 en 15 min |
| R1b (jeton absent) | `TOKEN_MISSING` | IP | 30 en 15 min (`details.token = "missing"`) |
| **R2 `REPEATED_FORBIDDEN`** | Union des événements R3 résolus, R4 et `ACCESS_DENIED` | `user_id` | 10 en 30 min |
| **R3 `CROSS_DEPARTMENT_PROBING`** | `DOCUMENT_VIEW` + `FAILURE` d'un EMPLOYEE, document résolu dans un autre département existant | `user_id` | 3 documents **distincts** en 30 min ; HIGH si ≥ 2 départements étrangers |
| **R4 `ADMIN_FEATURE_PROBING`** | `ACCESS_DENIED` ; `DOCUMENT_CREATE/UPDATE/DELETE` + `FAILURE` d'un EMPLOYEE | `user_id` | 3 en 60 min |
| **R5 `ACTIVITY_SPIKE`** | Tous les événements de l'utilisateur | `user_id` | voir formule |

### Garde-fous contre les faux positifs

- **Sessions expirées** : un jeton authentique expiré est journalisé
  `SESSION_EXPIRED` et n'alimente aucune règle (rafales de 401 à l'expiration,
  onglets multiples, IP partagée). PyJWT vérifie la signature avant
  l'expiration : un jeton falsifié reste `AUTHENTICATION_FAILED`.
- **Jetons absents** : journalisés `TOKEN_MISSING` (onglets restés ouverts après
  une déconnexion ou une expiration dans un autre onglet, sondes, Swagger sans
  authentification). Ils ont leur propre seuil par IP, plus élevé, et ne sont
  jamais additionnés aux jetons rejetés. Anonymes par nature, ils n'alimentent
  aucune autre règle.
- **R3** : le département est lu dans l'index courant via `load_index()` (lecture
  seule, jamais `sync_index_with_folder()`). Ne sont **pas** comptés : identifiant
  absent de l'index (jamais attribué ou document supprimé), index absent ou
  corrompu, nom de fichier mal formé, département sans correspondance en base,
  échec dans le propre département (fichier manquant). Ces identifiants sont
  seulement listés dans `details.unresolved_document_ids` d'une alerte R3.
  Le même document refusé plusieurs fois ne compte qu'une fois. Un index JSON
  valide mais mal structuré est traité comme inexploitable : rien n'est résolu,
  sans erreur serveur.
- **R2** : chaque événement est compté une seule fois, même s'il relève à la
  fois de R3 et de R4.
- **R4** : `ACCESS_DENIED` n'est émis que par `require_permission` et correspond
  toujours à une opération interdite au rôle. Les échecs `DOCUMENT_*` ne comptent
  que pour un EMPLOYEE (le contrôle de permission est la première instruction
  de ces routes) ; pour un ADMIN ce sont des erreurs fonctionnelles (404, 409,
  400). `USER_*` et `EXPORT` en échec sont exclus.
- **Admins** : exclus de R3 et des échecs documentaires de R4 / R2.

## R5 — formule de référence

Notations : `t` instant d'évaluation, `L` période d'analyse, `W` fenêtre
(10 min), `D` historique (7 jours).

1. Période d'analyse `A = [t − L, t]` ; historique `B = [t − L − D, t − L)`.
   La période d'analyse (dont la fenêtre courante) est exclue de la référence.
2. Pic : `p(u) = max_τ |{ e de u : τ ≤ ts(e) < τ + W, e ∈ A }|`.
3. `B` est découpé en `N = D / W = 1008` tranches alignées
   `b_k = [t − L − D + k·W, t − L − D + (k+1)·W)` ; `c_k(u)` = événements de `u`
   dans `b_k`.
4. Tranches actives `K(u) = { k : c_k(u) ≥ 1 }` — **les tranches vides sont
   exclues** du dénominateur (sinon nuits et week-ends diluent la moyenne et le
   critère relatif devient inopérant).
   `μ(u) = Σ c_k(u) / |K(u)|`, défini si `|K(u)| ≥ K_min = 6`, sinon historique
   insuffisant.
5. Seuil `T(u) = max(N_min, F · μ(u))` avec `N_min = 60`, `F = 5` ; si historique
   insuffisant, `T(u) = N_min`.
6. Alerte si `p(u) ≥ T(u)` ; `HIGH` si `p(u) ≥ 2 · T(u)`.
   `details` : `baseline` (`ok` / `insufficient`), `baseline_mean`, `active_windows`.

L'historique n'est chargé que pour les utilisateurs dont `p(u) ≥ N_min`.

## Index

Migration `3a6cb1fbc969` : index `ix_audit_logs_timestamp` sur
`audit_logs.timestamp` (non destructive), utilisé par les requêtes par période.

## Format du journal

Migration `433466e39163` (non destructive : colonnes nullables, lignes existantes
inchangées). Champs ajoutés à `audit_logs`, exposés par `GET /audit` et l'export CSV :

| Champ | Contenu |
|---|---|
| `user_department_id` | Département de l'**utilisateur** au moment de l'événement (instantané, conservé s'il change ensuite de département) ; `NULL` pour un événement anonyme. Pour un `LOGIN` en échec, il s'agit du département du **compte visé** (comme `user_id`), pas nécessairement de celui de l'auteur réel de la tentative ; `NULL` si l'email est inconnu. |
| `resource_department_id` | Département du **document concerné**, uniquement quand la route a identifié le document et que le jeton de département de son nom de fichier correspond exactement à un département en base (règle existante de la bibliothèque) ; sinon `NULL`. Jamais déduit de l'utilisateur. |
| `duration_ms` | Temps écoulé entre la réception de la requête et l'écriture de l'événement (middleware `RequestTimingMiddleware`) ; `NULL` hors requête HTTP. |
| `details` | JSON structuré uniquement : `reason` (motif de refus) et/ou `document_ids` (identifiants des documents de la page renvoyée par une recherche). Aucun texte libre, aucun texte de recherche, aucun contenu. |

`resource_department_id` est renseigné pour `DOCUMENT_VIEW` sur un document identifié
(succès, refus `DEPARTMENT_FORBIDDEN`, fichier absent) et pour les `DOCUMENT_CREATE`,
`DOCUMENT_UPDATE` (département après modification) et `DOCUMENT_DELETE` réussis.

Motifs de refus (`AuditReason`, liste fermée) :

| Motif | Événement |
|---|---|
| `MISSING_TOKEN` | `TOKEN_MISSING` |
| `INVALID_TOKEN` | `AUTHENTICATION_FAILED` (jeton invalide ou falsifié) |
| `EXPIRED_TOKEN` | `SESSION_EXPIRED` |
| `REVOKED_TOKEN` | `AUTHENTICATION_FAILED` (jeton révoqué) |
| `INACTIVE_OR_UNKNOWN_USER` | `AUTHENTICATION_FAILED` (compte inactif ou supprimé) |
| `ROLE_FORBIDDEN` | `ACCESS_DENIED`, `DOCUMENT_CREATE/UPDATE/DELETE` refusés au rôle |
| `DEPARTMENT_FORBIDDEN` | `DOCUMENT_VIEW` d'un document d'un autre département |
| `INVALID_CREDENTIALS` | `LOGIN` en échec (email inconnu, mauvais mot de passe ou compte inactif, non distingués) |

Les couches de l'ingestion RAG (`business_layer`) ne sont **pas** reliées aux
départements : leurs identifiants diffèrent (`credit_risques` / `Credit & Risques`,
`conformite_lbc_ft` / `Direction Conformite`) et aucune correspondance n'est définie.

## Limites

- `duration_ms` mesure le traitement jusqu'à l'écriture de l'événement : la
  transmission du fichier de `/documents/{id}/content` n'est pas incluse.
- `resource_department_id` reflète le département du document au moment de
  l'événement selon l'index courant ; un document sans correspondance exacte
  reste à `NULL`.

- Détection uniquement : ni blocage, ni notification, ni acquittement ; une
  alerte disparaît quand ses événements sortent de la période d'analyse.
- Rôle et département **actuels** de l'utilisateur ; département **actuel** du
  document (un document déplacé depuis l'événement peut être mal attribué).
- IP issue de `request.client.host` (`NULL` si l'adresse du client est
  indisponible), déconnexion comprise depuis SEC-01 T1/T2 : derrière un reverse
  proxy, les règles par IP perdent leur sens.
- Seuils empiriques, à ajuster en recette ; R5 peut être contourné par une
  montée progressive de l'activité.
- R1a et R1b peuvent produire deux alertes (compte et IP) pour une même rafale ;
  R1b peut produire deux alertes pour une même IP (jetons rejetés, jetons absents).
- Un changement de `JWT_SECRET_KEY` invalide tous les jetons en cours et peut
  provoquer des alertes R1b massives.
