"""
Source AutoScout24 (fr).

Pas d'API publique, mais le site est un Next.js : chaque page de résultats
embarque ses annonces en JSON dans la balise `<script id="__NEXT_DATA__">`.
On lit ce JSON plutôt que le HTML — c'est la structure de données de la page,
pas sa mise en page, donc bien plus stable qu'un parsing de balises.

Apport par rapport à renew.auto : le stock des concessions (Autosphere
notamment) qui ne publient pas sur la plateforme nationale Renault. Mesuré à
l'ajout : 119 EV60 de 2023+ absentes de renew.auto.

Ce que ce site ne donne pas : l'état de santé de la batterie, la couleur et la
date de publication de l'annonce. Ces champs restent vides plutôt qu'inventés.
"""
from __future__ import annotations

import json
import re
import time

from .base import ErreurSource, SourceBase, entier

URL_RECHERCHE = "https://www.autoscout24.fr/lst/{marque}/{modele}"
URL_SITE = "https://www.autoscout24.fr"
PAGES_MAX = 20  # AutoScout24 ne pagine pas au-delà

MOTIF_NEXT = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
MOTIF_PLAQUE = re.compile(r"([A-Z]{2}-\d{3}-[A-Z]{2})")
FINITIONS = ("esprit Alpine", "Iconic", "Techno", "Equilibre", "Evolution")


class SourceAutoScout24(SourceBase):
    nom = "autoscout24"
    libelle = "AutoScout24"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9",
        })

    # -- requete -----------------------------------------------------------
    def _params(self, page: int) -> dict:
        p = {
            "atype": "C",        # voitures
            "fuel": "E",         # électrique
            "cy": "F",           # vendues en France
            "ustate": "U",       # occasions
            "page": page,
        }
        annee_min = self.cfg.critere("annee_min")
        if annee_min:
            p["fregfrom"] = str(annee_min)
        annee_max = self.cfg.critere("annee_max")
        if annee_max:
            p["fregto"] = str(annee_max)
        # Les autres critères sont appliqués en local : le filtre serveur ne
        # sert qu'à réduire le nombre de pages à télécharger.
        return p

    def _page(self, page: int) -> dict:
        url = URL_RECHERCHE.format(
            marque=_slug(self.cfg.marque), modele=_slug(self.cfg.modele_court)
        )
        r = self.get(url, params=self._params(page))
        m = MOTIF_NEXT.search(r.text)
        if not m:
            raise ErreurSource(
                "AutoScout24 : données introuvables dans la page "
                "(le site a probablement changé de structure)"
            )
        try:
            return json.loads(m.group(1))["props"]["pageProps"]
        except (json.JSONDecodeError, KeyError) as e:
            raise ErreurSource("AutoScout24 : JSON illisible (%s)" % e) from e

    def chercher(self) -> list[dict]:
        brutes, page = [], 1
        delai = float(self.cfg.execution.get("delai_entre_pages_s") or 1.0)
        while page <= PAGES_MAX:
            pp = self._page(page)
            lot = pp.get("listings") or []
            brutes.extend(lot)
            total_pages = int(pp.get("numberOfPages") or 1)
            if page >= min(total_pages, PAGES_MAX) or not lot:
                break
            page += 1
            time.sleep(delai)
        annonces = [self.normaliser(b) for b in brutes]
        return [a for a in annonces if self.correspond(a)]

    # -- normalisation -----------------------------------------------------
    @staticmethod
    def normaliser(l: dict) -> dict:
        vehicule = l.get("vehicle") or {}
        lieu = l.get("location") or {}
        vendeur = l.get("seller") or {}
        suivi = l.get("tracking") or {}

        titre = vehicule.get("modelVersionInput") or vehicule.get("modelVersionCustom") or ""
        # AutoScout24 met le modele a part : "Megane" + "E-Tech Electric EV60...".
        # On le recompose pour que la fiche se lise comme celles de renew.auto.
        modele = vehicule.get("model") or ""
        if modele and modele.lower() not in titre.lower():
            titre = ("%s %s" % (modele, titre)).strip()
        texte = titre.upper().replace(" ", "")

        batterie = None
        if any(m in texte for m in ("EV60", "60KWH", "AUTONOMIECONFORT", "220CH")):
            batterie = 60
        elif any(m in texte for m in ("EV40", "40KWH", "AUTONOMIEURBAINE")):
            batterie = 40

        bas = titre.lower()
        charge = 22 if "super charge" in bas else (
            7 if ("optimum charge" in bas or "standard charge" in bas) else None
        )

        finition = ""
        for f in FINITIONS:
            if f.lower() in bas:
                finition = f
                break

        plaque = MOTIF_PLAQUE.search(l.get("crossReferenceId") or "")
        cp = str(lieu.get("zip") or "")
        telephones = vendeur.get("phones") or []
        images = l.get("images") or []
        url = l.get("url") or ""

        return {
            "source": "autoscout24",
            "id": "autoscout24:%s" % l.get("id", ""),
            "url": URL_SITE + url if url.startswith("/") else url,
            "titre": titre,
            "finition": finition,
            "prix": (l.get("price") or {}).get("priceRaw"),
            "prix_neuf": None,
            "km": entier(suivi.get("mileage")),
            "annee": _annee(suivi.get("firstRegistration")),
            "date_1re_immat": _date_iso(suivi.get("firstRegistration")),
            "batterie_kwh": batterie,
            "batterie_soh": None,          # non publié par AutoScout24
            "batterie_capacite_restante_kwh": None,
            "charge_ac_kw": charge,
            "pompe_a_chaleur": False,
            "puissance_ch": _puissance(titre),
            "couleur": "",                 # non publiée dans les résultats
            "vin": "",
            "immatriculation": plaque.group(1) if plaque else "",
            "concession": vendeur.get("companyName") or "",
            "ville": lieu.get("city") or "",
            "code_postal": cp,
            "departement": cp[:2] if len(cp) >= 2 else "",
            "telephone": telephones[0].get("callTo", "") if telephones else "",
            "garantie": "",
            "reserve": False,
            "controle_technique": False,
            "accidente": False,
            "date_publication": "",        # non publiée
            "photo": images[0] if images else "",
        }


def _slug(texte: str) -> str:
    return (texte or "").strip().lower().replace(" ", "-")


def _annee(reg: str):
    """'06-2023' -> 2023."""
    if reg and "-" in reg:
        return entier(reg.split("-")[-1])
    return entier(reg)


def _date_iso(reg: str) -> str:
    """'06-2023' -> '2023-06'. AutoScout24 ne donne pas le jour : on garde la
    précision réelle plutôt que d'inventer un 1er du mois."""
    if not reg or "-" not in reg:
        return ""
    mois, annee = reg.split("-", 1)
    return "%s-%s" % (annee, mois.zfill(2))


def _puissance(titre: str):
    m = re.search(r"(\d{2,3})\s*ch", titre or "", re.I)
    return entier(m.group(1)) if m else None
