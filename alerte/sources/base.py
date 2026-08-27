"""
Socle commun aux sources d'annonces.

Une source récupère des annonces sur un site et les rend au format normalisé
attendu par le reste du programme (voir `SourceRenew.normaliser`). Le filtrage
sur les critères de recherche, lui, est identique pour toutes : il vit ici, pour
qu'ajouter un site ne veuille pas dire réimplémenter — ni faire diverger — les
règles de sélection.
"""
from __future__ import annotations

import time

import requests

TENTATIVES = 3
ATTENTE_REESSAI = 2.0          # secondes, doublée à chaque essai
REESSAYABLES = {429, 500, 502, 503, 504}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ErreurSource(RuntimeError):
    """Echec de recuperation des annonces : la source est en panne ou a change."""


class SourceBase:
    nom = "base"
    libelle = "source"
    # Codes HTTP qu'il vaut la peine de rejouer. Attribut de classe et non
    # constante figee : un site peut repondre par un code que lui seul
    # considere comme passager (LeBonCoin et son 403 lie aux cookies).
    reessayables = REESSAYABLES

    def __init__(self, cfg):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})

    def chercher(self) -> list[dict]:
        raise NotImplementedError

    # -- reseau ------------------------------------------------------------
    def avant_tentative(self) -> None:
        """Appele avant chaque tentative de `get`, y compris les reessais.

        Sans point d'accroche ici, un reessai rejouerait la requete a
        l'identique — donc avec les memes cookies, donc avec le meme refus
        pour les sites qui bloquent la-dessus.
        """

    def get(self, url: str, **kwargs) -> requests.Response:
        """GET avec réessais.

        Les coupures passagères sont fréquentes ("Response ended prematurely",
        502, 504). Sans réessai, un incident réseau d'une seconde ferait échouer
        le passage entier — inacceptable pour une surveillance horaire dont
        personne ne relance les exécutions à la main.
        """
        kwargs.setdefault("timeout", 40)
        derniere = None
        for tentative in range(TENTATIVES):
            self.avant_tentative()
            try:
                r = self.session.get(url, **kwargs)
            except requests.RequestException as e:
                derniere = "%s injoignable : %s" % (self.libelle, e)
            else:
                if r.status_code == 200:
                    return r
                if r.status_code not in self.reessayables:
                    raise ErreurSource(
                        "%s a répondu %s" % (self.libelle, r.status_code)
                    )
                derniere = "%s a répondu %s" % (self.libelle, r.status_code)
            if tentative < TENTATIVES - 1:
                time.sleep(ATTENTE_REESSAI * (tentative + 1))
        raise ErreurSource("%s (après %d tentatives)" % (derniere, TENTATIVES))

    # -- filtrage local, commun a toutes les sources -----------------------
    def correspond(self, a: dict) -> bool:
        cfg, rech = self.cfg, self.cfg.recherche

        # Une annonce sans page consultable n'a rien a faire dans une alerte.
        if a.get("publiee_sur_le_site") is False:
            return False

        if cfg.batterie_kwh and not est_batterie(a, cfg.batterie_kwh):
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

        # Le SoH n'est publie que par renew.auto : ne pas ecarter les annonces
        # des autres sites au motif qu'elles ne le renseignent pas.
        soh = a.get("batterie_soh")
        if rech.get("soh_min") and soh is not None and soh < float(rech["soh_min"]):
            return False

        deps = cfg.departements
        if deps and a.get("departement") not in deps:
            return False

        if rech.get("exclure_reserves") and a.get("reserve"):
            return False

        return True


def est_batterie(a: dict, kwh: int) -> bool:
    """EV60 ou EV40 ?

    Les sites nomment la meme voiture de trois façons : le code batterie
    ("EV60"), la capacite ("60 kWh") ou l'appellation commerciale Renault
    ("autonomie confort" pour l'EV60, "autonomie urbaine" pour l'EV40).
    La puissance tranche les cas restants : le 220 ch n'existe qu'en EV60.
    """
    kwh = int(kwh)
    if a.get("batterie_kwh") is not None:
        return a["batterie_kwh"] == kwh

    texte = (a.get("titre") or "").upper().replace(" ", "")
    if kwh == 60:
        return any(m in texte for m in ("EV60", "60KWH", "AUTONOMIECONFORT", "220CH"))
    if kwh == 40:
        return any(m in texte for m in ("EV40", "40KWH", "AUTONOMIEURBAINE"))
    return False


def entier(valeur):
    """Convertit en entier ce qui peut l'etre, sinon None."""
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None
