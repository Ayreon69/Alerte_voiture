# Comment fonctionne ce scraping

Document technique : par quelles portes le bot entre sur chaque site, ce qui
rend l'entrée possible, et ce qu'un exploitant de site devrait corriger pour la
fermer. Rien ici n'est théorique — tout est ce que fait réellement le code de
[`alerte/sources/`](../alerte/sources).

---

## 1. Le principe général

Le bot ne « lit pas des pages ». Il vise, dans l'ordre de préférence :

1. **une API JSON publique** non authentifiée (renew.auto) ;
2. à défaut, **l'état hydraté d'une application Next.js** — le bloc
   `<script id="__NEXT_DATA__">` que le serveur injecte dans le HTML et qui
   contient l'intégralité des données de la page, souvent plus que ce qui est
   affiché (AutoScout24, LeBonCoin).

Dans les deux cas la charge est **structurée** : ni sélecteur CSS, ni parsing de
balises. C'est la clé de la robustesse du bot — un redesign visuel du site ne le
casse pas, seul un changement de contrat de données le casserait.

Le pipeline est identique pour les trois sources :

```
requête HTTP  ->  JSON  ->  normaliser()  ->  correspond()  ->  dédoublonnage  ->  Discord
                            (mise à plat)     (filtre local)    (identite.py)
```

Le filtrage serveur est délibérément **minimal** (marque, modèle, carburant,
année) : il ne sert qu'à réduire le nombre de pages à télécharger. Tout le reste
est filtré en local. Conséquence côté défense : les requêtes du bot ressemblent à
des requêtes de catalogue très larges, pas à des recherches d'utilisateur.

### Empreinte technique du client

- `requests.Session` — pas de navigateur, **pas d'exécution JavaScript**
- User-Agent falsifié : Chrome 131 / Windows 10 ([`base.py:18`](../alerte/sources/base.py:18))
- 3 tentatives avec backoff sur 429/500/502/503/504 ([`base.py:53`](../alerte/sources/base.py:53))
- 1 s entre deux pages (`delai_entre_pages_s`)
- Tourne sur **GitHub Actions**, donc depuis une IP de datacenter, deux fois par heure

---

## 2. renew.auto — API ouverte

**Porte d'entrée :** `GET https://fr.renew.auto/wired/commerce/v1/products`
([`renew.py`](../alerte/sources/renew.py))

```
?locale=fr_FR&channel=main&pageSize=500&page=0
&q=productType==vehicle_uci;brand.label.raw=="RENAULT";...
```

C'est l'API interne du site, appelée directement. Pas de clé, pas de token, pas
de referer vérifié, pas de rate limit observé. Le paramètre `q` est du **RSQL** :
un langage de requête complet exposé au client, qui accepte des expressions
arbitraires sur l'index — y compris
`eligiblePlatforms[platform==NATIONAL].eligible==true`, un champ métier interne.

`pageSize=500` est accepté : **l'inventaire national entier (~1300 véhicules)
part en 3 requêtes.**

Les enregistrements renvoyés contiennent plus que la fiche publique : `vin`,
`registrationNumber` (la plaque en clair), `battery.soh`, le téléphone de la
concession, et les véhicules `eligible == false` — le stock réservé au réseau de
concessionnaires, dont la fiche publique renvoie pourtant un 410.

### Faiblesses

| # | Faiblesse | Ce qu'elle permet |
|---|---|---|
| 1 | API non authentifiée, sans quota | Aspiration complète, à volonté |
| 2 | `pageSize` non plafonné (500 accepté) | 1300 véhicules en 3 appels |
| 3 | RSQL brut exposé au client | Requêtes sur des champs internes, énumération de l'index |
| 4 | Données non filtrées par la couche de présentation | Fuite de VIN + **plaque d'immatriculation** = donnée à caractère personnel (RGPD) |
| 5 | Le stock non publié est servi quand même | Fuite du stock réseau (939 véhicules, mesuré) |

### Correctifs

- Plafonner `pageSize` (50) et la profondeur de pagination.
- Remplacer RSQL par une liste blanche de filtres nommés.
- **Retirer `vin` et `registrationNumber` de la réponse de liste** — c'est le
  correctif le plus urgent, et le seul à portée juridique.
- Filtrer `eligible == false` côté serveur, pas côté client.
- Quota par IP + token de session émis par la page.

---

## 3. AutoScout24 — `__NEXT_DATA__`

**Porte d'entrée :** page HTML de résultats `/lst/renault/megane`, dont on extrait
le JSON par une regex
([`autoscout24.py:26`](../alerte/sources/autoscout24.py:26)), puis
`props.pageProps.listings`.

Aucune protection rencontrée : ni cookie requis, ni 403, ni challenge. Le bot
pagine jusqu'à 20 pages d'affilée avec la même session.

Détail notable : `crossReferenceId` contient la **plaque d'immatriculation** au
format `AB-123-CD`. Elle n'est affichée nulle part sur la page — c'est un
identifiant technique de rapprochement inter-plateformes qui a fuité dans la
charge. C'est précisément ce champ qui permet au bot de dédoublonner avec
renew.auto ([`identite.py`](../alerte/identite.py)).

### Faiblesses

| # | Faiblesse | Ce qu'elle permet |
|---|---|---|
| 1 | `__NEXT_DATA__` complet servi à tout client HTTP | Extraction sans navigateur ni JS |
| 2 | Aucune détection de bot sur le chemin `/lst/` | Pagination continue, même IP, même UA |
| 3 | Champ interne `crossReferenceId` porteur de la plaque | Fuite de donnée personnelle **et** corrélation entre plateformes |
| 4 | Téléphone du vendeur en clair dans la liste | Aspiration de base de prospection |

### Correctifs

- Ne mettre dans `__NEXT_DATA__` que ce que la page affiche ; charger le reste
  via une API appelée après hydratation (impose un vrai navigateur).
- Purger `crossReferenceId` — ou le hacher côté serveur, comme le bot le fait
  lui-même par précaution dans [`identite.py`](../alerte/identite.py).
- Masquer le téléphone derrière une interaction, comme le fait LeBonCoin.
- Détection comportementale : cadence, absence de requêtes sur les assets,
  absence de referer.

---

## 4. LeBonCoin — le seul qui résiste (à moitié)

Trois protections réelles, et comment le bot les contourne
([`leboncoin.py`](../alerte/sources/leboncoin.py)) :

**a) Blocage sur cookies.** Réutiliser la session d'une page à l'autre déclenche
un 403 dès la deuxième requête. Contournement : `avant_tentative()` **repart
d'une session vierge avant chaque requête**, réessais compris. Le blocage
pénalise donc la persistance, c'est-à-dire exactement le comportement d'un vrai
navigateur — un client sans état passe, un client normal se fait bloquer. La
protection est inversée.

**b) Plafond de pagination à la 19e page** (665 résultats). Contournement :
la recherche est découpée **par intervalles de prix**, chaque tranche trop dense
étant recoupée en deux jusqu'à passer sous la limite. Le plafond n'empêche pas
l'aspiration, il la rend seulement plus bavarde.

**c) Filtrage des IP de datacenter.** La seule qui tienne réellement : le site
répond 403 immédiat aux IP GitHub Actions. C'est pour cela que LeBonCoin est
désactivé dans [`config.yaml`](../config.yaml) — et qu'il **fonctionne
parfaitement depuis une connexion domestique**. Coût du contournement pour un
attaquant : un proxy résidentiel, quelques euros.

À son crédit, LeBonCoin ne publie ni la plaque ni le téléphone. Le bot doit s'en
remettre à une empreinte approximative (km exact + mois de mise en circulation +
département) pour reconnaître un véhicule — ce qui est précisément l'effet
recherché par une bonne minimisation des données.

### Faiblesses

| # | Faiblesse | Ce qu'elle permet |
|---|---|---|
| 1 | Blocage indexé sur les cookies | Récompense le client sans état ; contourné par une session neuve |
| 2 | Plafond de profondeur sans plafond de *couverture* | Contourné par découpe sur un filtre serveur (le prix) |
| 3 | Filtre IP = seule vraie barrière | Tombe avec un proxy résidentiel |
| 4 | `__NEXT_DATA__` complet, comme AutoScout24 | Extraction sans JS |

### Correctifs

- Inverser la logique du cookie : exiger un **token signé, à courte durée,
  délivré par la page** — bloquer l'absence d'état, pas sa présence.
- Limiter le nombre total de résultats **par recherche normalisée** et par IP,
  pas la profondeur de page : la découpe par prix devient alors inopérante.
- Ajouter un coût progressif (challenge JS, puis captcha) au-delà d'un volume,
  plutôt qu'un 403 binaire lié à l'origine de l'IP.

---

## 5. Ce qu'il faut retenir pour défendre un site

Par ordre d'efficacité constatée contre **ce** bot :

1. **Ne pas servir ce que la page n'affiche pas.** Toutes les fuites sensibles
   ici (VIN, plaque, téléphone, stock non publié) sont des champs que personne
   ne voit à l'écran. C'est gratuit à corriger et c'est ce qui a le plus de
   valeur pour l'attaquant.
2. **Exiger un navigateur** : charger les données après hydratation, derrière un
   token de session signé. Un client `requests` s'arrête là — il n'exécute pas de
   JS.
3. **Plafonner le volume, pas la profondeur.** Un plafond de pages se contourne
   en découpant la recherche sur n'importe quel filtre serveur.
4. **Bloquer sur le comportement, pas sur l'IP.** Le filtre IP de LeBonCoin est
   la mesure la plus efficace des trois sites… et la plus facile à louer.
5. **Ne pas exposer un langage de requête** (RSQL) au client : c'est donner un
   accès en lecture à l'index.

Le bot est poli — 1 s entre les pages, 2 passages par heure, réessais bornés — et
ne récupère que des annonces publiques. Il n'a jamais eu à contourner de captcha,
à exécuter de JS ni à falsifier autre chose qu'un User-Agent. C'est la mesure la
plus parlante de l'état des protections : **aucun des trois sites n'exige quoi
que ce soit qu'un script de 200 lignes ne puisse fournir.**
