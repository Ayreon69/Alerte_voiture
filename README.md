# Alerte Mégane E-Tech EV60

Surveillance automatique des annonces de **Renault Mégane E-Tech électrique EV60, 2023 ou plus récente**, sur [renew.auto](https://fr.renew.auto) (le site officiel des occasions Renault). Dès qu'une annonce apparaît, une notification tombe sur Discord.

Environ **890 véhicules** correspondent aux critères en France, avec **~10 nouvelles annonces par jour**. Prix de 19 990 € à 35 990 €, médiane 25 390 €.

---

## Installation

```bash
pip install -r requirements.txt
```

### Créer le webhook Discord

1. Dans Discord : clic droit sur le salon → **Modifier le salon** → **Intégrations** → **Webhooks** → **Nouveau webhook**
2. **Copier l'URL du webhook**
3. Copier `.env.example` en `.env` et y coller l'URL :

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

Vérifier que ça marche :

```bash
python run.py --test-discord
```

---

## Utilisation

Une vérification ponctuelle :

```bash
python run.py
```

Surveillance continue (toutes les 30 min par défaut) :

```bash
python run.py --watch
```

Voir les 20 annonces les moins chères, sans rien notifier :

```bash
python run.py --lister 20
```

| Option | Effet |
|---|---|
| `--watch` | Boucle en continu (intervalle dans `config.yaml`) |
| `--intervalle 15` | Force l'intervalle en minutes |
| `--dry-run` | Aucune notification Discord, tout s'affiche en console |
| `--reset` | Oublie les annonces connues et repart de zéro |
| `--forcer-alertes` | Notifie même au premier lancement (déconseillé : ~890 annonces) |
| `--lister N` | Affiche les N annonces les moins chères puis sort |
| `--test-discord` | Envoie un message de test sur le webhook |

**Au premier lancement**, l'inventaire complet est enregistré comme référence : Discord reçoit seulement un récapitulatif et les 3 annonces les moins chères. Seules les annonces apparues **après** ce premier run déclenchent une alerte.

---

## Ce qui déclenche une alerte

- **Nouvelle annonce** correspondant aux critères → fiche verte
- **Changement de prix** sur une annonce déjà connue (≥ 200 € par défaut) → fiche orange, prix barré puis nouveau prix
- **Annonce disparue** (vendue ou retirée) → désactivé par défaut, à activer dans `config.yaml`

### Ce que contient une fiche

Prix en tête de titre, puis kilométrage (et km/an), mise en circulation (et âge),
santé batterie avec jauge, puissance de charge, couleur, concession avec téléphone,
finition, garantie et ancienneté de l'annonce.

Photo en grand quand il y a trois annonces ou moins, en vignette au-delà, pour
qu'un lot de vingt reste parcourable. Toutes les annonces publiées ont une photo ;
si l'une venait à en manquer, c'est signalé explicitement plutôt que de laisser un
trou dans la fiche.

### Les badges

Sous le titre, jusqu'à quatre badges, du plus rare au plus courant :

| Badge | Signification |
|---|---|
| 🔥 Parmi les 5 % les moins chères | Position réelle dans le stock suivi, recalculée à chaque passage |
| ⏳ En ligne depuis N jours | Au-delà de 90 jours : l'annonce ne part pas, la négociation est probable |
| 🔋 Batterie quasi neuve | SoH ≥ 97 % |
| 🛣️ Kilométrage parmi les plus bas | Dans les 15 % les moins roulées |
| 📉 −N % par rapport au neuf | Décote face au prix catalogue d'origine |
| 🌡️ Pompe à chaleur | Présente sur trois quarts du stock — son absence coûte de l'autonomie en hiver |

### Deux repères propres au VE

**La puissance de charge AC** distingue les versions *super charge* (22 kW) des
*optimum charge* (7 kW) : sur une borne publique, une heure branchée rapporte
environ 110 km dans un cas contre 35 km dans l'autre. Les deux tiers du stock
sont en 22 kW.

**Le SoH** (state of health) est mesuré par Renault et daté de moins de deux mois
sur la quasi-totalité du stock. La jauge est graduée de 80 à 100 % : sur une batterie de VE
tout se joue dans cet intervalle, une échelle partant de zéro serait toujours
pleine.

Une chose vérifiée et écartée : sur ce modèle la batterie est **toujours achetée
avec le véhicule**, jamais en location (contrairement aux Zoé). Tout le stock est
dans ce cas, il n'y a donc rien à surveiller de ce côté.

---

## Régler les critères

Tout se passe dans [`config.yaml`](config.yaml). Les filtres à `null` sont ignorés.

```yaml
recherche:
  batterie_kwh: 60          # 60 = EV60, 40 = EV40, null = les deux
  annee_min: 2023
  prix_max: null            # ex: 22000
  km_max: null              # ex: 60000
  soh_min: null             # santé batterie mini en %, ex: 92
  departements: null        # ex: [75, 77, 78, 91, 92, 93, 94, 95]
  exclure_reserves: false
```

Avec ~10 nouvelles annonces par jour sur toute la France, `prix_max`, `km_max` ou `departements` sont les leviers les plus efficaces pour calmer le flux.

`max_notifications` (défaut : 20) plafonne le nombre d'annonces détaillées envoyées par exécution — au-delà, l'en-tête indique le total réel et seules les moins chères sont détaillées.

**À noter :** une poignée d'annonces affichent un prix à 0 € (« prix non communiqué »). Elles sont conservées et signalées comme telles, et les filtres de prix ne s'y appliquent pas — mieux vaut une annonce à vérifier à la main qu'une bonne affaire écartée en silence.

---

## Faire tourner le bot 24 h/24, ordinateur éteint

La surveillance tourne sur **GitHub Actions** : les serveurs de GitHub exécutent
le script à heure fixe, gratuitement, sans machine à laisser allumée.

### Mise en place (une seule fois)

**1. Créer un dépôt privé sur GitHub** — [github.com/new](https://github.com/new),
nom au choix, visibilité **Private**, sans README ni .gitignore.

**2. Y envoyer le projet**, depuis le dossier `Alerte_Megane` :

Une commande à la fois : PowerShell n'accepte pas l'enchaînement `&&`.

```bash
git init -b main
```

```bash
git add .
```

```bash
git commit -m "Surveillance Megane E-Tech EV60"
```

```bash
git remote add origin https://github.com/TON-COMPTE/TON-DEPOT.git
```

```bash
git push -u origin main
```

Le fichier `.env` **ne part pas** : il est exclu par `.gitignore`. C'est voulu,
un webhook publié sur GitHub serait utilisable par n'importe qui.

**3. Déclarer le webhook comme secret** — dans le dépôt :
*Settings* → *Secrets and variables* → *Actions* → **New repository secret**
- Name : `DISCORD_WEBHOOK_URL`
- Secret : l'URL de ton webhook

**4. Autoriser le bot à écrire** — *Settings* → *Actions* → *General* →
*Workflow permissions* → cocher **Read and write permissions** → *Save*.
Sans ça, le bot ne peut pas mémoriser les annonces déjà vues d'une exécution à
l'autre, et te renverrait tout le stock à chaque passage.

**5. Lancer un premier passage** — onglet *Actions* → *Surveillance Mégane* →
**Run workflow**. Le message « Surveillance activée » qui arrive sur Discord
confirme que tout fonctionne.

À partir de là, c'est autonome : une vérification par heure, ton ordinateur peut
rester éteint.

### Bon à savoir

**La fréquence.** Une fois par heure par défaut (`cron: "7 * * * *"` dans
[`.github/workflows/surveillance.yml`](.github/workflows/surveillance.yml)).
Avec ~10 nouvelles annonces par jour, c'est largement suffisant. Pour passer à
30 minutes, mettre `"*/30 * * * *"` — un dépôt privé dispose de 2000 minutes
gratuites par mois et chaque passage en consomme environ une, donc 30 minutes
(~1440 min/mois) tient, mais sans grande marge. Un dépôt public a des minutes
illimitées.

**L'horaire est en UTC** et GitHub exécute les tâches planifiées « au mieux » :
un retard de 5 à 20 minutes est normal, sans conséquence ici.

**Après 60 jours sans activité**, GitHub désactive les workflows planifiés. Le
bot commite son état à chaque nouveauté, ce qui entretient l'activité ; si la
surveillance s'arrêtait malgré tout, un clic sur *Enable workflow* la relance.

**La mémoire du bot** (`data/state.json`) est recommittée automatiquement à
chaque changement. L'historique des annonces vues est donc consultable dans les
commits du dépôt, et `data/annonces.csv` est téléchargeable depuis la page de
chaque exécution (onglet *Artifacts*, conservé 7 jours).

### Les autres options

| Solution | Coût | Remarque |
|---|---|---|
| **GitHub Actions** | gratuit | Retenu ici : rien à administrer |
| VPS (Oracle Cloud Always Free, ou ~4 €/mois) | 0 à 4 €/mois | `python run.py --watch` dans un service systemd. Horaires précis, mais serveur à maintenir |
| Raspberry Pi | matériel | Revient à laisser une machine allumée |

---

## Lancer sur ta machine (ponctuel ou en continu)

Pour un usage manuel, sans dépendre du cloud :

```bash
python run.py --watch
```

La fenêtre doit rester ouverte. Pour une exécution planifiée par Windows,
utiliser `surveiller.bat` depuis le **Planificateur de tâches** (déclencheur
*Quand j'ouvre une session*, action *Démarrer un programme*, démarrer dans le
dossier du projet). Dans les deux cas, l'ordinateur doit être allumé — d'où
GitHub Actions ci-dessus.

---

## Fichiers produits

| Fichier | Contenu |
|---|---|
| `data/annonces.csv` | Inventaire courant complet, trié par prix, colonnes de décision en premier (ouvrable dans Excel) |
| `data/historique.csv` | Journal des événements : nouvelles annonces, changements de prix, disparitions |
| `data/state.json` | Mémoire des annonces déjà vues — ne pas éditer à la main |

---

## Comment ça marche

renew.auto expose une API JSON publique, celle qu'utilise son propre moteur de recherche :

```
GET https://fr.renew.auto/wired/commerce/v1/products
    ?locale=fr_FR&channel=main&pageSize=500&page=0
    &q=productType==vehicle_uci;brand.label.raw=="RENAULT";
       model.label.raw=="MEGANE E-TECH ELECTRIQUE";
       eligiblePlatforms[platform==NATIONAL].eligible==true
```

Le paramètre `q` est du RSQL — **les valeurs contenant des espaces doivent être entre guillemets**, sinon l'API répond `400 Filter request format not valid`.

### Le piège de l'éligibilité plateforme

L'index de l'API ne contient pas que les annonces du site : il contient aussi les
véhicules réservés au réseau des concessionnaires. Ceux-là ont
`eligiblePlatforms[NATIONAL].eligible == false` et **leur fiche renvoie une page 410**
sur fr.renew.auto.

Sans ce filtre, l'index brut du modèle passe de 1322 à 2261 entrées, et 621 des
1508 annonces retenues sur nos critères pointaient vers des pages inexistantes.
`vehicleStatus` (`A` ou `C`), qu'on pourrait croire responsable, n'y est pour rien :
des véhicules en statut `C` ont une fiche parfaitement valide. Seule l'éligibilité
`NATIONAL` compte. Elle est appliquée côté serveur **et** revérifiée en local avant
toute alerte : un lien mort dans une notification n'a aucune valeur.

Effet de bord : les annonces sans photo étaient précisément les non publiées.
Depuis la correction, la totalité du stock suivi a une photo.

Le reste du filtrage (batterie, année, prix, kilométrage, département) se fait en
local. C'est volontaire : l'API ne sait pas tout filtrer, et un filtre serveur trop
strict écarterait silencieusement les annonces aux données incomplètes.

L'API renvoie des données très riches, dont l'**état de santé de la batterie** (`battery.soh`) — rare sur les sites d'occasion classiques, et déterminant sur un VE.

### Deux protections contre les fausses alertes

L'index de renew.auto n'est pas parfaitement stable : mesuré sur trois relevés
consécutifs, une annonce apparaît puis disparaît d'un passage à l'autre (un
doublon dans leur index pousse une autre annonce hors de la dernière page).
Une annonce absente est donc gardée en mémoire **2 jours** avant d'être
déclarée disparue, et ne peut pas redéclencher d'alerte entre-temps. Sans ce
délai, la même voiture serait annoncée plusieurs fois.

Par ailleurs, si la source renvoie soudain **moins de la moitié** de l'inventaire
connu (panne partielle, index en reconstruction), le passage est abandonné sans
rien conclure ni notifier. Sinon le bot déclarerait des centaines d'annonces
disparues, les oublierait, puis les réannoncerait comme neuves au passage
suivant.

### Autres particularités des données

Pour distinguer EV60 et EV40, le champ `battery.type` fait foi : 155 EV60 du stock n'ont pas « EV60 » dans leur libellé de version, un filtrage sur le texte seul les manquerait.

---

## Ajouter un autre site

Chaque source est une classe dans `alerte/sources/` exposant `chercher()` et renvoyant des annonces au format normalisé (voir `SourceRenew.normaliser`). Il suffit ensuite de l'enregistrer dans `SOURCES` (`alerte/sources/__init__.py`) — le reste (comparaison, notifications, export) fonctionne sans modification.

Repérages déjà faits sur les autres sites :

| Site | Accessible en direct | Remarque |
|---|---|---|
| AutoScout24 | oui | Bon candidat pour la prochaine source |
| Autohero | oui | Stock national, à explorer |
| Leboncoin | non (403) | Protection anti-bot, nécessiterait Playwright |
| La Centrale | non (403) | Idem |
