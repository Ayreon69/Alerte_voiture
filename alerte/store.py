"""
Memoire des annonces deja vues.

Un simple fichier JSON (data/state.json) suffit : quelques milliers d'annonces,
lues et reecrites en entier a chaque execution. L'ecriture est atomique pour ne
pas corrompre l'etat si le script est interrompu en plein run.

Compare l'inventaire courant a l'etat precedent et en deduit :
  - les nouvelles annonces,
  - les changements de prix,
  - les annonces disparues (vendues ou retirees).
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from .identite import cles_identite, plaque_de, plaques_incompatibles

CHAMPS_SUIVIS = ("prix", "km", "reserve")

# Champs conservés d'une exécution à l'autre. Inutile de recopier toute
# l'annonce : il faut de quoi comparer (prix, km, reserve) et de quoi décrire
# une annonce disparue, qui par définition n'est plus dans l'inventaire.
# Garder les 34 champs faisait un état de 1 Mo, trop lourd à versionner à
# chaque passage quand la surveillance tourne sur GitHub Actions.
CHAMPS_MEMOIRE = (
    "id", "url", "titre", "prix", "km", "annee", "batterie_soh",
    "ville", "departement", "reserve",
    # De quoi reconnaitre le vehicule meme si l'identifiant change (voir
    # alerte/identite.py). La plaque n'est PAS stockee en clair : cet etat est
    # versionne, et une plaque est une donnee personnelle qui resterait a vie
    # dans l'historique git. Seule son empreinte est conservee, ce qui suffit
    # a comparer.
    "date_1re_immat",
)

# L'index de renew.auto n'est pas parfaitement stable : une annonce peut
# manquer d'un passage puis réapparaître au suivant (un doublon dans leur index
# pousse une autre annonce hors de la dernière page). Sans délai de grâce, ce
# clignotement enverrait plusieurs fois la même voiture.
# Une annonce absente reste donc « connue » pendant ce délai : elle n'est
# déclarée disparue qu'au-delà, et ne redéclenche jamais d'alerte entre-temps.
JOURS_AVANT_DISPARITION = 2


class Etat:
    def __init__(self, chemin: Path):
        self.chemin = Path(chemin)
        self.annonces: dict[str, dict] = {}
        self.premier_run = True
        self.derniere_execution: str | None = None
        self._charger()

    def _charger(self) -> None:
        if not self.chemin.exists():
            return
        try:
            data = json.loads(self.chemin.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Etat illisible : on repart de zero plutot que de planter.
            return
        self.annonces = data.get("annonces") or {}
        self.derniere_execution = data.get("derniere_execution")
        self.premier_run = not self.annonces

    def sauvegarder(self) -> None:
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        contenu = {
            "derniere_execution": datetime.now().isoformat(timespec="seconds"),
            "nb_annonces": len(self.annonces),
            "annonces": self.annonces,
        }
        tmp = self.chemin.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(contenu, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        os.replace(tmp, self.chemin)

    # -- identite ----------------------------------------------------------
    def _index_identites(self) -> dict:
        """Clés d'identité des annonces connues -> leur identifiant en mémoire."""
        index = {}
        for ident, fiche in self.annonces.items():
            for cle in cles_identite(fiche):
                index.setdefault(cle, ident)
        return index

    def _retrouver(self, a: dict, index: dict) -> dict | None:
        """La fiche connue décrivant ce véhicule, quel que soit son identifiant.

        L'identifiant seul ne suffit pas : quand renew.auto perd une annonce le
        temps d'un passage, sa jumelle AutoScout24 la remplace avec un autre
        identifiant. Sans cette reconnaissance, la voiture serait annoncée
        comme neuve alors qu'elle est connue et déjà notifiée.
        """
        connu = self.annonces.get(a["id"])
        if connu is not None:
            return connu
        for cle in cles_identite(a):
            ident = index.get(cle)
            fiche = self.annonces.get(ident) if ident else None
            if fiche is not None and not plaques_incompatibles(fiche, a):
                return fiche
        return None

    # -- comparaison -------------------------------------------------------
    def comparer(self, annonces: list[dict]) -> dict:
        """Retourne {nouvelles, changements, disparues} sans modifier l'etat."""
        courant = {a["id"]: a for a in annonces}
        index = self._index_identites()
        connues = {a["id"]: self._retrouver(a, index) for a in annonces}
        nouvelles = [a for i, a in courant.items() if connues[i] is None]

        changements = []
        for ident, a in courant.items():
            ancien = connues[ident]
            if not ancien:
                continue
            diffs = {
                champ: (ancien.get(champ), a.get(champ))
                for champ in CHAMPS_SUIVIS
                if ancien.get(champ) != a.get(champ)
            }
            if diffs:
                changements.append({"annonce": a, "avant": ancien, "diffs": diffs})

        # Disparue = absente depuis assez longtemps pour que ce ne soit pas
        # un simple clignotement de l'index. Une fiche encore representee par
        # une annonce d'un autre site n'a evidemment pas disparu.
        retrouvees = {id(f) for f in connues.values() if f is not None}
        disparues = [
            fiche
            for ident, fiche in self.annonces.items()
            if ident not in courant and id(fiche) not in retrouvees
            and _absente_depuis_longtemps(fiche)
        ]
        return {
            "nouvelles": nouvelles,
            "changements": changements,
            "disparues": disparues,
            "total": len(courant),
        }

    def appliquer(self, annonces: list[dict]) -> None:
        """Met l'etat a jour avec l'inventaire courant.

        Les annonces absentes ne sont pas supprimees tout de suite : elles sont
        marquees, gardees le temps du delai de grace, puis oubliees.
        """
        maintenant = datetime.now().isoformat(timespec="seconds")
        courant = {a["id"] for a in annonces}
        index = self._index_identites()
        nouvel_etat = {}
        # Fiches connues reprises sous un autre identifiant : a ne pas garder
        # en double, sans quoi l'ancienne entree survivrait au delai de grace
        # et pourrait realerter plus tard.
        remplacees = set()

        for a in annonces:
            ancien = self._retrouver(a, index) or {}
            if ancien and ancien.get("id") and ancien["id"] != a["id"]:
                remplacees.add(ancien["id"])
            fiche = {c: a.get(c) for c in CHAMPS_MEMOIRE}
            empreinte = plaque_de(a)
            if empreinte:
                fiche["plaque_empreinte"] = empreinte
            fiche["premiere_vue"] = ancien.get("premiere_vue") or maintenant
            fiche["derniere_vue"] = maintenant
            nouvel_etat[a["id"]] = fiche

        for ident, fiche in self.annonces.items():
            if ident in courant or ident in remplacees:
                continue
            if _absente_depuis_longtemps(fiche):
                continue  # oubliee pour de bon
            fiche = dict(fiche)
            fiche.setdefault("absente_depuis", maintenant)
            nouvel_etat[ident] = fiche

        self.annonces = nouvel_etat


def _absente_depuis_longtemps(fiche: dict) -> bool:
    """Le delai de grace est-il ecoule pour cette annonce absente ?"""
    depuis = fiche.get("absente_depuis")
    if not depuis:
        return False  # absente pour la premiere fois : on attend
    try:
        debut = datetime.fromisoformat(depuis)
    except (ValueError, TypeError):
        return True
    return (datetime.now() - debut) >= timedelta(days=JOURS_AVANT_DISPARITION)


def journaliser(chemin: Path, evenements: list[dict]) -> None:
    """Ajoute les evenements au journal CSV (historique complet, jamais purge)."""
    if not evenements:
        return
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    colonnes = [
        "horodatage", "evenement", "id", "titre", "prix", "prix_avant",
        "km", "annee", "batterie_soh", "ville", "departement", "url",
    ]
    existe = chemin.exists()
    with open(chemin, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", extrasaction="ignore")
        if not existe:
            w.writeheader()
        w.writerows(evenements)
