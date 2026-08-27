"""
Source LeBonCoin (fr).

Comme AutoScout24, le site est un Next.js : la page de résultats embarque ses
annonces en JSON dans `<script id="__NEXT_DATA__">`. On lit ce JSON plutôt que
le HTML — c'est la structure de données de la page, pas sa mise en page.

Apport par rapport aux deux autres sources : les **particuliers**, absents de
renew.auto comme d'AutoScout24, et les concessions multimarques qui ne publient
que là. C'est aussi le plus gros gisement des trois en volume brut.

Trois particularités à connaître :

- **Les cookies déclenchent le blocage.** Réutiliser la même session d'une page
  à l'autre fait basculer le site en 403 dès la deuxième requête ; une session
  neuve à chaque fois passe sans encombre. D'où `avant_tentative()`, qui repart
  d'une session vierge avant chaque requête, réessais compris : contre-intuitif
  — rejouer un échec à l'identique ne servirait à rien ici — mais c'est ce qui
  a été mesuré.
- **La pagination s'arrête à la 19e page.** Au-delà, le site répond 403, quels
  que soient le rythme des requêtes et la patience : ce n'est pas un quota mais
  un plafond, vérifié comme tel. Une recherche donnant plus de 665 résultats
  est donc *tronquée en silence* si on se contente de paginer — c'est le cas
  ici, le stock dépasse le millier. `_tranches()` contourne le plafond en
  découpant la recherche par tranches de prix, chacune redécoupée en deux tant
  qu'elle ne tient pas sous la limite.
- **Pas de plaque d'immatriculation.** LeBonCoin ne la publie pas, donc le
  dédoublonnage par plaque ne peut pas s'appliquer aux annonces venant d'ici :
  c'est l'empreinte de repli (voir `run.cles_identite`) qui prend le relais.

Ce que ce site ne donne pas : l'état de santé de la batterie, le téléphone du
vendeur (masqué derrière un bouton) et le prix catalogue d'origine. Ces champs
restent vides plutôt qu'inventés.
"""
from __future__ import annotations

import json
import re
import time

import requests

from .base import UA, ErreurSource, SourceBase, entier

URL_RECHERCHE = "https://www.leboncoin.fr/recherche"
CATEGORIE_VOITURES = "2"
CARBURANT_ELECTRIQUE = "4"
PAGES_MAX = 19           # au-dela, LeBonCoin repond 403 (plafond, pas quota)
PAR_PAGE = 35
MAX_PAR_TRANCHE = PAGES_MAX * PAR_PAGE   # 665 annonces atteignables d'une traite

# Bornes de la decoupe par prix quand la configuration n'en impose pas.
# Le plafond est volontairement tres large : une annonce au-dessus serait
# perdue, alors qu'une tranche haute vide ne coute qu'une requete.
PRIX_PLANCHER = 0
PRIX_PLAFOND = 200_000

MOTIF_NEXT = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


class SourceLeBonCoin(SourceBase):
    nom = "leboncoin"
    libelle = "LeBonCoin"
    # Le 403 de LeBonCoin est un blocage passager lie aux cookies, pas un refus
    # definitif : la meme requete sur session neuve passe. On le rend donc
    # reessayable ici, contrairement au cas general.
    reessayables = SourceBase.reessayables | {403}

    # -- reseau ------------------------------------------------------------
    def avant_tentative(self) -> None:
        """Une session neuve avant chaque tentative, reessais compris."""
        self._session_neuve()

    def _session_neuve(self) -> None:
        """Repart d'une session vierge.

        Sans cookie, LeBonCoin sert la page normalement ; avec ceux accumules
        par la requete precedente, il repond 403. Une session par page est donc
        la façon la plus simple de rester en 200.
        """
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        })

    # -- requete -----------------------------------------------------------
    def _params(self, page: int, tranche: tuple[int, int]) -> dict:
        bas, haut = tranche
        p = {
            "category": CATEGORIE_VOITURES,
            "u_car_brand": self.cfg.marque.upper(),
            "u_car_model": "%s_%s" % (
                self.cfg.marque.upper(), self.cfg.modele_court.capitalize()
            ),
            "fuel": CARBURANT_ELECTRIQUE,
            "page": str(page),
            # LeBonCoin veut un intervalle "min-max", les bornes ouvertes
            # s'ecrivant litteralement "min" et "max".
            "price": "%s-%s" % (
                "min" if bas <= PRIX_PLANCHER else bas,
                "max" if haut >= PRIX_PLAFOND else haut,
            ),
        }
        amin = self.cfg.critere("annee_min")
        amax = self.cfg.critere("annee_max")
        if amin or amax:
            p["regdate"] = "%s-%s" % (amin or "min", amax or "max")
        # Les autres criteres sont appliques en local, comme pour les autres
        # sources : le filtre serveur ne sert qu'a reduire le nombre de pages.
        return p

    def _page(self, page: int, tranche: tuple[int, int]) -> dict:
        r = self.get(URL_RECHERCHE, params=self._params(page, tranche))
        m = MOTIF_NEXT.search(r.text)
        if not m:
            raise ErreurSource(
                "LeBonCoin : données introuvables dans la page "
                "(le site a probablement changé de structure)"
            )
        try:
            return json.loads(m.group(1))["props"]["pageProps"]["searchData"]
        except (json.JSONDecodeError, KeyError) as e:
            raise ErreurSource("LeBonCoin : JSON illisible (%s)" % e) from e

    # -- decoupe -----------------------------------------------------------
    def _bornes_initiales(self) -> tuple[int, int]:
        """Intervalle de prix a couvrir, resserre par la configuration.

        Un `prix_max` configure evite d'explorer les tranches hautes pour rien.
        `prix_min`, en revanche, n'est pas repercute : le filtre local laisse
        deliberement passer les annonces sans prix, et les exclure ici les
        perdrait avant meme qu'il puisse s'exprimer.
        """
        haut = self.cfg.critere("prix_max")
        return PRIX_PLANCHER, int(haut) if haut else PRIX_PLAFOND

    def chercher(self) -> list[dict]:
        # Une annonce boostee remonte en tete et reapparait d'une page a
        # l'autre ; les bornes de tranches se chevauchent d'un euro. Dans les
        # deux cas c'est l'identifiant qui tranche.
        brutes, vues = [], set()
        delai = float(self.cfg.execution.get("delai_entre_pages_s") or 1.0)
        a_faire = [self._bornes_initiales()]

        while a_faire:
            tranche = a_faire.pop()
            sd = self._page(1, tranche)
            total = int(sd.get("total") or 0)
            bas, haut = tranche

            # Trop d'annonces pour la profondeur autorisee : on coupe en deux
            # plutot que de tronquer sans le dire.
            if total > MAX_PAR_TRANCHE:
                if haut - bas > 1:
                    milieu = (bas + haut) // 2
                    a_faire.extend([(bas, milieu), (milieu, haut)])
                    time.sleep(delai)
                    continue
                # Plus rien a couper : plus de 665 annonces au meme euro pres.
                # Invraisemblable, mais le dire vaut mieux que perdre le reste
                # sans que personne ne le sache.
                print("    LeBonCoin : %d annonces a %d EUR, seules les %d "
                      "premieres sont lues" % (total, bas, MAX_PAR_TRANCHE))

            pages = min(int(sd.get("max_pages") or 1), PAGES_MAX)
            for page in range(1, pages + 1):
                if page > 1:
                    time.sleep(delai)
                    sd = self._page(page, tranche)
                lot = sd.get("ads") or []
                if not lot:
                    break
                for a in lot:
                    if a.get("list_id") not in vues:
                        vues.add(a.get("list_id"))
                        brutes.append(a)
            time.sleep(delai)

        annonces = [self.normaliser(b) for b in brutes]
        return [a for a in annonces if self.correspond(a)]

    # -- normalisation -----------------------------------------------------
    @staticmethod
    def normaliser(a: dict) -> dict:
        att = {x.get("key"): x for x in (a.get("attributes") or [])}

        def val(cle, defaut=""):
            return (att.get(cle) or {}).get("value") or defaut

        def libelle(cle, defaut=""):
            return (att.get(cle) or {}).get("value_label") or defaut

        # `u_car_version` est la version commerciale complete ("Megane E-Tech
        # Electric EV60 220ch Iconic super charge") ; le titre libre saisi par
        # le vendeur ne sert que de repli.
        titre = val("u_car_version") or a.get("subject") or ""
        texte = titre.upper().replace(" ", "")

        # La puissance vient d'un champ structure, pas du titre libre : elle
        # tranche la ou le titre reste muet ou ecrit "220 iconic" sans le "ch"
        # que cherche la detection par mots-cles. Le 220 n'existe qu'en EV60,
        # verifie sur tout le stock renew.auto, ou la capacite est donnee par
        # l'API : aucune EV40 au-dela de 130 ch.
        puissance = entier(val("horse_power_din", None))

        batterie = None
        if any(m in texte for m in ("EV60", "60KWH", "AUTONOMIECONFORT", "220CH")):
            batterie = 60
        elif any(m in texte for m in ("EV40", "40KWH", "AUTONOMIEURBAINE")):
            batterie = 40
        elif puissance and puissance >= 200:
            batterie = 60

        # Les vendeurs LeBonCoin ecrivent aussi bien "super charge" que la
        # notation abregee "AC22" — les deux disent la meme chose.
        bas = titre.lower()
        if "super charge" in bas or "ac22" in texte.lower():
            charge = 22
        elif ("optimum charge" in bas or "standard charge" in bas
                or "ac7" in texte.lower()):
            charge = 7
        else:
            charge = None

        lieu = a.get("location") or {}
        proprio = a.get("owner") or {}
        images = a.get("images") or {}
        prix = a.get("price") or []
        cp = str(lieu.get("zipcode") or "")

        # Un particulier n'a pas de nom de concession : le dire explicitement
        # vaut mieux qu'un champ vide, la difference compte a l'achat.
        if proprio.get("type") == "pro":
            vendeur = val("store_name") or proprio.get("name") or ""
        else:
            vendeur = "Particulier"

        # `vehicle_specifications` est une liste fourre-tout ("Carnet
        # d'entretien disponible, Vehicule non fumeur, Sous garantie
        # constructeur"...) : on n'en retient que ce qui parle de garantie,
        # le reste noierait la fiche.
        garantie = ", ".join(
            bout.strip()
            for bout in libelle("vehicle_specifications").split(",")
            if "garantie" in bout.lower()
        )

        return {
            "source": "leboncoin",
            "id": "leboncoin:%s" % a.get("list_id", ""),
            "url": a.get("url") or "",
            "titre": titre,
            "finition": libelle("u_car_finition"),
            "prix": prix[0] if prix else None,
            "prix_neuf": None,
            "km": entier(val("mileage", None)),
            "annee": entier(val("regdate", None)),
            "date_1re_immat": _date_iso(val("issuance_date")),
            "batterie_kwh": batterie,
            "batterie_soh": None,          # non publié par LeBonCoin
            "batterie_capacite_restante_kwh": None,
            "charge_ac_kw": charge,
            "pompe_a_chaleur": False,
            "puissance_ch": puissance,
            "couleur": libelle("vehicule_color"),
            "vin": "",
            "immatriculation": "",         # non publiée
            "concession": vendeur,
            "ville": lieu.get("city") or "",
            "code_postal": cp,
            "departement": str(lieu.get("department_id") or cp[:2]).zfill(2),
            "telephone": "",               # masqué derrière un bouton
            "garantie": garantie,
            "reserve": False,
            "controle_technique": False,
            "accidente": False,
            "date_publication": _horodatage_iso(a.get("first_publication_date")),
            "photo": (images.get("urls") or [""])[0],
        }


def _date_iso(issuance: str) -> str:
    """'03/2023' -> '2023-03'.

    LeBonCoin ne donne que le mois de premiere mise en circulation : on garde
    cette precision plutot que d'inventer un jour, comme pour AutoScout24.
    """
    if not issuance or "/" not in issuance:
        return ""
    mois, annee = issuance.split("/", 1)
    return "%s-%s" % (annee.strip(), mois.strip().zfill(2))


def _horodatage_iso(valeur: str) -> str:
    """'2026-07-18 00:37:07' -> '2026-07-18T00:37:07'."""
    if not valeur:
        return ""
    return valeur.strip().replace(" ", "T")
