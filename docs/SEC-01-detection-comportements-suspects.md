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
| **R5 `ACTIVITY_SPIKE`** | Tous les événements de l'utilisateur, appels à la passerelle LLM (`LLM_CALL`) compris | `user_id` | voir formule |
| **`DOCUMENT_ENUMERATION`** | `DOCUMENT_VIEW` + `FAILURE` d'un EMPLOYEE sur un document **inexistant** (voir garde-fous) | `user_id` | 4 identifiants **distincts** en 30 min (`ALERT_DOCUMENT_ENUMERATION_*`) |
| **`SEARCH_BURST`** | `DOCUMENT_SEARCH` | `user_id` | 30 en 1 min (`ALERT_SEARCH_BURST_*`) |

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
- **`DOCUMENT_ENUMERATION`** : un événement est une tentative sur un document
  inexistant uniquement si :
  - il porte le motif `DOCUMENT_NOT_FOUND` (identifiant absent de l'index au
    moment de la requête). Les motifs `DEPARTMENT_FORBIDDEN`, `FILE_NOT_FOUND`
    et `ROLE_FORBIDDEN` ne comptent jamais ;
  - **ou**, pour un événement antérieur aux motifs (sans `reason`), son
    identifiant est absent de l'index **actuel**, lu par `load_index()` en
    lecture seule. Un identifiant encore présent dans l'index ne compte jamais
    (refus ancien, fichier manquant…). Si l'index ne permet pas de le prouver
    (fichier absent, illisible, structure inattendue ou index vide), l'événement
    est **ignoré** : l'ambiguïté est irréductible.

  Les admins sont exclus. L'alerte liste `document_ids` et, parmi eux,
  `inferred_document_ids` (identifiants déduits d'événements anciens). Le même
  identifiant demandé plusieurs fois ne compte qu'une fois.
- **`SEARCH_BURST`** : fenêtre glissante d'une minute ; des recherches réparties
  sur plusieurs minutes ne déclenchent rien. Ne concerne que les rafales courtes,
  le volume global restant couvert par R5.

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

**Appels à la passerelle LLM.** R5 compte tous les événements de l'utilisateur
sans filtre d'action : les événements `LLM_CALL` (voir « Format du journal »)
entrent donc dans le pic et dans l'historique, quel que soit leur résultat. Avec
les valeurs par défaut (`ALERT_SPIKE_MIN_EVENTS=60`,
`ALERT_SPIKE_WINDOW_MINUTES=10`), une alerte est levée à partir de **60 événements
en 10 minutes**, ou davantage si l'historique de l'utilisateur relève le seuil
(`F · μ(u)`). Un usage **légitime et intensif** du chat peut donc déclencher une
alerte `ACTIVITY_SPIKE` ; à l'inverse, un usage régulier du chat élève la moyenne
de référence de l'utilisateur. Les autres règles filtrent des actions précises et
ne comptent jamais `LLM_CALL` (vérifié par `backend/tests/test_gateway_audit.py`).

**Appels fournisseur de l'agent RAG.** R5 n'est pas modifiée : elle compte chaque
événement `LLM_CALL`, donc **chaque appel fournisseur**, et non chaque question. Une
question qui produit `k` appels pèse `k` événements : avec 4 appels par question,
le plancher de 60 événements en 10 minutes est atteint dès 15 questions. Une alerte
`ACTIVITY_SPIKE` portant sur des événements `provider_call` doit donc être lue en
regroupant ses événements par `request_id` avant de conclure à une rafale de
requêtes. L'adaptation éventuelle de R5 (compter les requêtes plutôt que les appels,
ou une règle dédiée) reste une décision ouverte ; aucun seuil n'est fixé ici
(vérifié par `backend/tests/test_provider_call_audit.py::test_r5_still_counts_each_provider_call_event`).

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
| `details` | JSON structuré uniquement : `reason` (motif de refus) et/ou `document_ids` (identifiants des documents de la page renvoyée par une recherche), ou `llm_call` pour un appel à la passerelle LLM, ou `provider_call` pour un appel fournisseur de l'agent RAG (voir ci-dessous ; jamais les deux à la fois). Aucun texte libre, aucun texte de recherche, aucun contenu. |

`resource_department_id` est renseigné pour `DOCUMENT_VIEW` sur un document identifié
(succès, refus `DEPARTMENT_FORBIDDEN`, fichier absent) et pour les `DOCUMENT_CREATE`,
`DOCUMENT_UPDATE` (département après modification) et `DOCUMENT_DELETE` réussis.

Motifs de refus et d'échec (`AuditReason`, liste fermée) :

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
| `DOCUMENT_NOT_FOUND` | `DOCUMENT_VIEW` en échec : identifiant absent de l'index (404) |
| `FILE_NOT_FOUND` | `DOCUMENT_VIEW` en échec : document existant dont le fichier est absent (404). **Non atteignable actuellement**, voir Limites. |

### Appels à la passerelle LLM (`LLM_CALL`)

Chaque appel à la passerelle interne (`backend/app/services/llm_gateway.py`, US 07 T5)
est journalisé par `log_gateway_call` (`backend/app/services/audit.py`) en **un seul
événement**, à partir du compte rendu que la passerelle renvoie (ou que porte la
`GatewayError` levée). La passerelle reste indépendante de HTTP et de la base :
c'est son appelant qui fournit l'utilisateur authentifié et l'IP, puis valide la
transaction.

| Champ | Valeur |
|---|---|
| `action` | `LLM_CALL` |
| `resource_type` | `LLM_GATEWAY` |
| `resource_id` | `NULL` |
| `user_id`, `user_department_id` | L'utilisateur authentifié. Le `caller_id` du compte rendu doit être son identifiant, sinon l'appel est refusé et rien n'est écrit. |
| `resource_department_id` | `NULL` : les couches métier ne sont pas reliées aux départements |
| `duration_ms` | Comme pour les autres événements (`NULL` hors requête HTTP) ; la durée propre à la passerelle est dans `details.llm_call.gateway_ms` |
| `result` | Selon l'issue de l'appel, voir ci-dessous |

| Issue (`outcome`) | `result` |
|---|---|
| `ANSWERED` : réponse du fournisseur, restaurée | `SUCCESS` |
| `NO_AUTHORIZED_CONTEXT` : aucun extrait autorisé, fournisseur non appelé | `SUCCESS` |
| `PROMPT_TOO_LONG` : prompt au-delà de la limite, fournisseur non appelé | `SUCCESS` |
| `FAILED` : échec d'une étape (récupérateur, assembleur, fournisseur…) | `FAILURE` |

`details` ne contient que la clé `llm_call`, une **liste fermée de 11 clés** :

| Clé | Contenu |
|---|---|
| `outcome` | `ANSWERED`, `NO_AUTHORIZED_CONTEXT`, `PROMPT_TOO_LONG` ou `FAILED` |
| `failed_stage` | `QUESTION`, `RETRIEVAL`, `CONTEXT`, `ASSEMBLY`, `PROVIDER` ou `RESTORE` en cas d'échec ; sinon `null` |
| `error_type` | En cas d'échec, nom d'une exception intégrée à Python (`TimeoutError`…) ou `ComponentError` pour toute autre classe, y compris un nom forgé dans le compte rendu ; sinon `null` |
| `received`, `retained` | Nombre d'extraits reçus du récupérateur et retenus dans le contexte ; `null` si l'appel a échoué avant la constitution du contexte |
| `excluded` | Nombre d'extraits écartés **par motif** (`EMPTY_TEXT`, `MISSING_SOURCE`, `EXCLUDED_SOURCE`, `MISSING_LAYER`, `LAYER_NOT_ALLOWED`, `DUPLICATE`, `MAX_EXTRACTS`, `CONTEXT_BUDGET`) ; seuls les motifs rencontrés figurent ; `{}` sinon |
| `prompt_chars`, `answer_chars` | Taille en caractères du prompt assemblé et de la réponse du fournisseur ; `null` si l'étape n'a pas été atteinte |
| `tokens_restored`, `tokens_withheld` | Nombre de jetons de pseudonymisation effectivement restaurés dans la réponse, et laissés tels quels (absents du prompt envoyé) |
| `gateway_ms` | Durée de l'appel mesurée par la passerelle |

Seules des **valeurs d'énumération, des entiers non négatifs et `null`** sont
journalisés. Un compte rendu incohérent (issue, étape et type d'erreur discordants,
compteur négatif ou non entier, type inattendu) est refusé **avant toute écriture**.

**Données interdites, jamais journalisées** : la question, sa longueur (décision
H8 ouverte), le prompt, la réponse, les valeurs sensibles (noms, emails,
identifiants…), les jetons de pseudonymisation et les noms de sources. Les extraits
écartés ne sont pas journalisés un par un : ils sont seulement comptés par motif.

Aucune migration : les valeurs `LLM_CALL` et `LLM_GATEWAY` tiennent dans les
colonnes `String(50)` existantes, et `details` est déjà une colonne `JSON`.
L'action est filtrable par `GET /audit?action=LLM_CALL`, incluse dans l'export
CSV, et affichée sous les libellés « Appel au LLM » et « Passerelle LLM » dans
l'interface d'audit. Tests : `backend/tests/test_gateway_audit.py` et les scénarios
`LLM_CALL` de `backend/tests/test_audit_traceability.py`.

### Appels fournisseur de l'agent RAG (`LLM_CALL` avec `provider_call`)

L'agent RAG appelle le fournisseur **plusieurs fois pour une seule question** :
étapes de l'agent, reformulation de la requête, filtres déduits (self-query),
fusion. Tous ces appels partagent le contexte de la requête
(`backend/app/services/llm_request.py`), qui produit un compte rendu sans texte par
appel. `log_request_calls` (`backend/app/services/audit.py`) les journalise **à la
fin de la requête**, un événement `LLM_CALL` par appel ; `log_provider_call`
journalise un seul appel. Les routes RAG (`POST /rag/chat`, `POST /rag/chat/stream`,
`backend/app/api/rag.py`) appellent `log_request_calls` une fois le contexte de la
requête fermé, y compris en cas d'erreur, de délai dépassé, de limite atteinte ou
d'interruption du flux ; une requête sans appel au fournisseur n'écrit rien.
`log_provider_call` n'est appelé par aucune route. Le RAG étant désactivé par
défaut et fermé à tous les utilisateurs tant que H10 n'est pas décidée, aucun
événement `provider_call` n'est produit aujourd'hui en dehors des tests
(`backend/tests/test_rag_audit.py`).

| Champ | Valeur |
|---|---|
| `action`, `resource_type`, `resource_id` | `LLM_CALL`, `LLM_GATEWAY`, `NULL` |
| `user_id`, `user_department_id` | L'utilisateur authentifié. Le `caller_id` du compte rendu (et celui du contexte) doit être **exactement** son identifiant, un entier (un booléen est refusé), sinon rien n'est écrit. |
| `resource_department_id` | `NULL` |
| `duration_ms` | Comme pour les autres événements ; la durée propre à l'appel est dans `details.provider_call.duration_ms` |
| `result` | Selon l'issue de l'appel, voir ci-dessous |

| Issue (`outcome`) | `result` |
|---|---|
| `COMPLETED` : réponse du fournisseur, restaurée | `SUCCESS` |
| `PROMPT_TOO_LONG` : prompt au-delà de la limite, fournisseur non appelé | `SUCCESS` |
| `CALL_LIMIT_EXCEEDED` : nombre maximal d'appels de la requête atteint, fournisseur non appelé | `FAILURE` |
| `FAILED` : échec de l'appel | `FAILURE` |

`details` ne contient que la clé `provider_call`, une **liste fermée de 11 clés**,
distincte de `llm_call` (qui reste inchangée) :

| Clé | Contenu |
|---|---|
| `request_id` | Identifiant de la requête, **tiré au hasard côté serveur** (32 caractères hexadécimaux minuscules) ; jamais dérivé de la question, de l'utilisateur, de l'heure ni de l'identifiant de session fourni par le client |
| `call_index` | Rang de l'appel dans la requête, à partir de 0 |
| `call_kind` | `AGENT_STEP`, `QUERY_REWRITE`, `SELF_QUERY` ou `QUERY_FUSION` |
| `outcome` | `COMPLETED`, `PROMPT_TOO_LONG`, `CALL_LIMIT_EXCEEDED` ou `FAILED` |
| `error_type` | Seulement pour `FAILED` : nom d'une exception intégrée à Python ou `ComponentError` ; sinon `null` |
| `prompt_chars` | **Toujours `null`** (décision H8, voir ci-dessous) ; la clé est conservée pour garder la liste fermée de 11 clés |
| `answer_chars` | Taille de la réponse (contenu et arguments d'outils) ; `null` si aucune réponse n'a été restaurée |
| `tokens_sent`, `tokens_restored`, `tokens_withheld` | Nombre de jetons de pseudonymisation distincts envoyés, de jetons restaurés et de jetons laissés tels quels dans la réponse |
| `duration_ms` | Durée de l'appel |

Un compte rendu incohérent est refusé **avant toute écriture** : `request_id` mal
formé, compteur négatif ou non entier, type d'appel ou issue inattendus, type
d'erreur hors `FAILED` ou absent en `FAILED`, appel refusé pour limite qui déclare un
envoi ou une réponse, prompt trop long qui déclare une réponse ou des jetons envoyés,
appel terminé sans prompt, jetons restaurés ou laissés tels quels sans réponse
(`answer_chars` nul), jetons restaurés alors qu'aucun jeton n'a été envoyé. La taille
du prompt présente dans le compte rendu reste **validée** (entier non négatif,
cohérence avec l'issue), même si elle n'est pas journalisée.

`log_request_calls` exige un contexte **fermé** : tant qu'il est ouvert, un appel en
cours manquerait au journal ; un contexte ouvert est refusé avant toute écriture. Les
comptes rendus restent lisibles après la fermeture. La fonction valide ensuite
**tous** les comptes rendus de la requête avant d'en écrire un : même utilisateur,
`request_id` du contexte, `call_index` uniques ; au moindre refus, **aucun**
événement n'est ajouté. L'écriture
passe par `db.add()` et `db.flush()`, sans commit : l'appelant valide la transaction,
ou l'annule si l'écriture échoue. `log_audit_event` n'accepte pas de
`provider_call` : seules les deux fonctions dédiées, qui vérifient l'identité,
écrivent ces événements.

**Données interdites, jamais journalisées** : la question, **sa longueur**, le prompt,
**sa longueur**, la réponse, les arguments d'outils, les valeurs sensibles, les jetons
de pseudonymisation, les noms de sources et l'**identifiant de session fourni par le
client**.

**Décision H8 pour les appels fournisseur.** Le prompt d'une étape de l'agent ou
d'une reformulation est un texte constant (prompt système, modèle de requête) suivi
de la question : sa taille donnerait la longueur de la question, exactement ou à
quelques caractères près. `details.provider_call.prompt_chars` est donc enregistré à
`null` **quel que soit le résultat** de l'appel, pour ne révéler ni la longueur
exacte ni une approximation de la longueur de la question. Le compte rendu de
`llm_request.py` n'est pas modifié : la taille y reste disponible pour les limites
(`max_prompt_chars`) et pour la validation, mais ne sort pas du processus. La
passerelle (`details.llm_call.prompt_chars`, où le prompt mêle question et extraits)
n'est pas concernée par ce changement.

**Corrélation.** Les événements d'une même question ont le même `request_id` et des
`call_index` de 0 à n − 1 ; `call_index = 0` marque le premier appel de chaque
requête. **Plusieurs événements `LLM_CALL` ne signifient donc pas plusieurs requêtes
de l'utilisateur** : une seule question produit couramment plusieurs appels
fournisseur. Pour compter les requêtes, il faut compter les `request_id` distincts
(ou les événements de `call_index` 0), et non les événements. Tests :
`backend/tests/test_provider_call_audit.py`.

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
- **Passerelle T5 non branchée** : `log_gateway_call` existe et est testée, mais
  aucune route ne l'appelle ; l'agent RAG n'utilise pas la passerelle T5 et ses
  appels sont journalisés avec `provider_call` (voir « Appels fournisseur de
  l'agent RAG »).
- R5 compte les appels `LLM_CALL` : un usage légitime et intensif du chat peut
  produire une alerte `ACTIVITY_SPIKE`. Avec l'agent RAG, chaque question produit
  plusieurs appels fournisseur, donc plusieurs événements : l'effet est multiplié
  (voir « R5 — formule de référence »). Décision ouverte.
- **Appels fournisseur : branchés mais inactifs** : les routes RAG appellent
  `log_request_calls`, mais le RAG est désactivé par défaut et refuse tous les
  utilisateurs tant que H10 n'est pas décidée ; aucun événement `provider_call`
  n'est produit en dehors des tests. Un délai dépassé du SDK Groq est enregistré
  avec `error_type = "TimeoutError"`. Les actions de la mémoire de l'agent
  (enregistrement, recherche) ne sont pas journalisées.
- R1a et R1b peuvent produire deux alertes (compte et IP) pour une même rafale ;
  R1b peut produire deux alertes pour une même IP (jetons rejetés, jetons absents).
- Un changement de `JWT_SECRET_KEY` invalide tous les jetons en cours et peut
  provoquer des alertes R1b massives.
- **`DOCUMENT_ENUMERATION`, risque résiduel sur les événements anciens** : la
  déduction se fonde sur l'index **actuel**. Un document supprimé **après**
  l'événement (refus ancien sans motif, ou fichier manquant à l'époque) n'est
  plus dans l'index et peut être compté à tort comme inexistant. Les nouveaux
  événements, qui portent un motif, ne sont pas concernés.
- **Défaut connu, non corrigé (tâche distincte)** : si le fichier d'un document
  **existant** a disparu du disque, `GET /documents/{id}` et
  `GET /documents/{id}/content` renvoient **500** et **n'écrivent aucun
  événement d'audit**. `_parsed_document` (`backend/app/api/routes/documents.py`)
  appelle `document_file_times`, dont le `stat()` lève `FileNotFoundError` avant
  la branche « fichier absent ». Le motif `FILE_NOT_FOUND` est branché sur cette
  branche mais **ne peut donc pas être enregistré** dans ce scénario tant que le
  défaut n'est pas traité. Ce cas n'affecte pas `DOCUMENT_ENUMERATION`, qui ne
  compte jamais `FILE_NOT_FOUND`.
