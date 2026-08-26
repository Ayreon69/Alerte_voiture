"""
Source renew.auto (Renault Occasions).

Utilise l'API publique du site : GET /wired/commerce/v1/products
Le filtre `q` est du RSQL ; les valeurs contenant des espaces doivent etre
entre guillemets (sinon l'API repond 400 "Filter request format not valid").

L'API expose des donnees tres completes : VIN, kilometrage, prix, concession,
et surtout l'etat de sante de la batterie (battery.soh), precieux sur un VE.
"""
from __future__ import annotations

import time
from datetime import datetime

import requests

URL_API = "https://fr.renew.auto/wired/commerce/v1/products"
URL_DETAIL = "https://fr.renew.auto/achat-vehicules-occasions/details.html?productId={pid}"
TAILLE_PAGE = 500
# Une poignee d'annonces portent un prix a 0, 1 ou 2 EUR : c'est un
# "prix non communique", pas une affaire. On les garde mais sans prix.
PRIX_MIN_PLAUSIBLE = 3000
# Niveaux de finition Megane E-Tech, du plus simple au plus equipe.
FINITIONS = ("Equilibre", "Evolution ER", "Evolution", "Techno",
             "esprit Alpine", "Iconic", "Luxury")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ErreurSource(RuntimeError):
    """Echec de recuperation des annonces."""


class SourceRenew:
    nom = "renew"
    libelle = "renew.auto"

    def __init__(self, cfg):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept": "application/json"})

    # -- requete -----------------------------------------------------------
    def _filtre_rsql(self) -> str:
        """Filtre applique cote serveur : marque, modele et plateforme.

        Le filtre de plateforme n'est PAS optionnel. L'index de l'API contient
        aussi les vehicules reserves au reseau des concessionnaires : ils ont
        `eligiblePlatforms[NATIONAL].eligible == false` et leur fiche renvoie
        une page 410 sur fr.renew.auto. Sans ce filtre, l'index brut du modele
        passe de 1322 a 2261 entrees, et 621 des 1508 annonces retenues sur
        nos criteres pointaient vers des pages inexistantes.

        Le reste (batterie, annee, prix...) reste filtre en local : l'API ne
        sait pas tout filtrer, et un filtre serveur trop strict ecarterait
        silencieusement les annonces aux donnees incompletes.
        """
        parties = ["productType==vehicle_uci"]
        if self.cfg.marque:
            parties.append('brand.label.raw=="%s"' % self.cfg.marque)
        if self.cfg.modele:
            parties.append('model.label.raw=="%s"' % self.cfg.modele)
        parties.append(
            "eligiblePlatforms[platform==%s].eligible==true" % self.cfg.plateforme
        )
        return ";".join(parties)

    def _page(self, page: int) -> dict:
        params = {
            "locale": "fr_FR",
            "channel": "main",
            "pageSize": TAILLE_PAGE,
            "page": page,
            "q": self._filtre_rsql(),
        }
        try:
            r = self.session.get(URL_API, params=params, timeout=60)
        except requests.RequestException as e:
            raise ErreurSource("renew.auto injoignable : %s" % e) from e
        if r.status_code != 200:
            raise ErreurSource(
                "renew.auto a repondu %s : %s" % (r.status_code, r.text[:200])
            )
        return r.json()

    def chercher(self) -> list[dict]:
        """Recupere tout l'inventaire du modele, normalise, puis filtre."""
        brutes, page = [], 0
        delai = float(self.cfg.execution.get("delai_entre_pages_s") or 1.0)
        while True:
            data = self._page(page)
            brutes.extend(data.get("data") or [])
            total_pages = int(data.get("totalPages") or 1)
            page += 1
            if page >= total_pages:
                break
            time.sleep(delai)
        annonces = [self.normaliser(b) for b in brutes]
        return [a for a in annonces if self.correspond(a)]

    # -- normalisation -----------------------------------------------------
    @staticmethod
    def normaliser(v: dict) -> dict:
        """Transforme un enregistrement de l'API en annonce a plat."""
        batterie = v.get("battery") or {}
        prix_bloc = (v.get("prices") or [{}])[0]
        prix_neuf = (v.get("originalPrices") or [{}])[0].get("priceWithTaxes")
        concession = v.get("dealer") or v.get("vehicleExhibitionSite") or {}
        adresse = concession.get("address") or {}
        cp = str(adresse.get("postalCode") or "")
        moteur = v.get("engine") or {}
        garantie = v.get("warrantyInfo") or {}

        photo = ""
        for asset in v.get("assets") or []:
            if asset.get("assetType") != "picture":
                continue
            for rendu in asset.get("renditions") or []:
                if rendu.get("resolutionType") in ("large", "medium"):
                    photo = rendu.get("url") or ""
                    break
            if photo:
                break

        immat = v.get("firstRegistrationDate") or ""
        pid = v.get("productId") or v.get("identifier") or v.get("vin") or ""
        version = ((v.get("version") or {}).get("label") or "").lower()

        # "super charge" = chargeur AC 22 kW, "optimum charge" = 7 kW.
        # Difference tres concrete au quotidien : 1 h de borne publique
        # rapporte ~110 km dans un cas, ~35 km dans l'autre.
        charge_ac = None
        if "super charge" in version:
            charge_ac = 22
        elif "optimum charge" in version:
            charge_ac = 7

        # `finishing` est souvent vide alors que la finition figure a la fin
        # du libelle de version ("... super charge Techno").
        finition = (v.get("finishing") or {}).get("label") or ""
        if not finition:
            for connue in FINITIONS:
                if version.endswith(connue.lower()):
                    finition = connue
                    break

        # La pompe a chaleur change l'autonomie reelle en hiver. Un quart du
        # stock n'en a pas. On ne l'affirme que si elle est listee : l'absence
        # peut venir d'une fiche incomplete.
        pompe = any(
            "chaleur" in ((e.get("label") or "") + (e.get("description") or "")).lower()
            for e in (v.get("equipments") or []) + (v.get("options") or [])
        )

        publiee = any(
            p.get("platform") == "NATIONAL" and p.get("eligible")
            for p in (v.get("eligiblePlatforms") or [])
        )

        prix = prix_bloc.get("customerDisplayPrice") or prix_bloc.get("priceWithTaxes")
        if prix is not None and float(prix) < PRIX_MIN_PLAUSIBLE:
            prix = None

        return {
            "source": "renew",
            "id": "renew:%s" % pid,
            "url": URL_DETAIL.format(pid=pid),
            "titre": ((v.get("version") or {}).get("label") or "").strip()
            or (v.get("model") or {}).get("label", ""),
            "finition": finition,
            "prix": prix,
            "prix_neuf": prix_neuf,
            "km": v.get("mileage"),
            "annee": int(immat[:4]) if immat[:4].isdigit() else None,
            "date_1re_immat": immat,
            "batterie_kwh": _entier(batterie.get("type")),
            "batterie_soh": batterie.get("soh"),
            "batterie_capacite_restante_kwh": batterie.get("autonomy"),
            "charge_ac_kw": charge_ac,
            "pompe_a_chaleur": pompe,
            "puissance_ch": moteur.get("powerOutputHp"),
            "couleur": (v.get("colorMarketing") or {}).get("label", ""),
            "vin": v.get("vin", ""),
            "immatriculation": v.get("registrationNumber", ""),
            "concession": concession.get("name", ""),
            "ville": adresse.get("locality", ""),
            "code_postal": cp,
            "departement": cp[:2] if len(cp) >= 2 else "",
            "telephone": (concession.get("telephone") or {}).get("value", ""),
            "garantie": garantie.get("label", ""),
            "publiee_sur_le_site": publiee,
            "reserve": bool(v.get("reserved")),
            "controle_technique": bool(v.get("technicalControl")),
            "accidente": bool(v.get("hasCollision")),
            "date_publication": v.get("publicationDate", ""),
            "photo": photo,
            "vu_le": datetime.now().isoformat(timespec="seconds"),
        }

    # -- filtrage local ----------------------------------------------------
    def correspond(self, a: dict) -> bool:
        cfg, rech = self.cfg, self.cfg.recherche

        # Ceinture et bretelles avec le filtre serveur : une annonce non
        # publiee sur le site n'a pas de fiche consultable, donc rien a
        # signaler. Ne jamais alerter sur un lien mort.
        if not a.get("publiee_sur_le_site"):
            return False

        if cfg.batterie_kwh and not _est_batterie(a, cfg.batterie_kwh):
            return False

        annee = a.get("annee")
        if rech.get("annee_min") and (annee is None or annee < int(rech["annee_min"])):
            return False
        if rech.get("annee_max") and (annee is None or annee > int(rech["annee_max"])):
            return False

        # Prix inconnu : on ne filtre pas dessus, mieux vaut une annonce a
        # verifier a la main qu'une bonne affaire silencieusement ecartee.
        prix = a.get("prix")
        if prix is not None:
            if rech.get("prix_max") and prix > float(rech["prix_max"]):
                return False
            if rech.get("prix_min") and prix < float(rech["prix_min"]):
                return False

        km = a.get("km")
        if rech.get("km_max") and (km is None or km > float(rech["km_max"])):
            return False

        soh = a.get("batterie_soh")
        if rech.get("soh_min") and (soh is None or soh < float(rech["soh_min"])):
            return False

        deps = cfg.departements
        if deps and a.get("departement") not in deps:
            return False

        if rech.get("exclure_reserves") and a.get("reserve"):
            return False

        return True


def _entier(valeur):
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None


def _est_batterie(a: dict, kwh: int) -> bool:
    """EV60/EV40 : `battery.type` est la source fiable, le libelle de version
    sert de repli (155 EV60 du stock n'ont pas "EV60" dans leur libelle)."""
    if a.get("batterie_kwh") is not None:
        return a["batterie_kwh"] == int(kwh)
    return "EV%d" % int(kwh) in (a.get("titre") or "").upper()
