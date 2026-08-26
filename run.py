"""
Alerte Mégane E-Tech EV60 : point d'entrée.

    python run.py                 une vérification, puis sortie
    python run.py --watch         boucle toutes les N minutes (config.yaml)
    python run.py --dry-run       aucune notification, tout s'affiche en console
    python run.py --reset         oublie les annonces connues (repart de zéro)
    python run.py --lister 20     affiche les 20 annonces les moins chères

Au tout premier lancement, l'inventaire est enregistré comme référence sans
déverser les ~900 annonces existantes sur Discord : seules celles apparues
APRÈS ce premier run déclenchent une alerte.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from alerte.config import DOSSIER_DATA, Config, charger_env, resume_criteres
from alerte.notify import (
    SEUIL_GRANDE_IMAGE,
    Contexte,
    Notificateur,
    afficher,
    embed_annonce,
    embed_changement_prix,
    entete_demarrage,
    entete_nouvelles,
)
from alerte.format import euros
from alerte.report import exporter_csv, par_prix
from alerte.sources import SOURCES, ErreurSource
from alerte.store import Etat, journaliser

# En dessous de cette fraction de l'inventaire connu, on considere que la
# source a un probleme plutot que le marche.
SEUIL_INVENTAIRE_SUSPECT = 0.5

FICHIER_ETAT = DOSSIER_DATA / "state.json"
FICHIER_CSV = DOSSIER_DATA / "annonces.csv"
FICHIER_JOURNAL = DOSSIER_DATA / "historique.csv"


def horodatage() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def collecter(cfg: Config) -> list[dict]:
    """Interroge toutes les sources configurées."""
    annonces = []
    for nom, classe in SOURCES.items():
        source = classe(cfg)
        print("  interrogation de %s..." % source.libelle, end=" ", flush=True)
        try:
            trouvees = source.chercher()
        except ErreurSource as e:
            print("ÉCHEC")
            print("    %s" % e)
            continue
        print("%d annonces correspondent aux critères" % len(trouvees))
        annonces.extend(trouvees)
    return annonces


def evenement(type_: str, a: dict, prix_avant=None) -> dict:
    return {
        "horodatage": datetime.now().isoformat(timespec="seconds"),
        "evenement": type_,
        "id": a.get("id"),
        "titre": a.get("titre"),
        "prix": a.get("prix"),
        "prix_avant": prix_avant,
        "km": a.get("km"),
        "annee": a.get("annee"),
        "batterie_soh": a.get("batterie_soh"),
        "ville": a.get("ville"),
        "departement": a.get("departement"),
        "url": a.get("url"),
    }


def verifier(cfg: Config, notif: Notificateur, forcer_alertes: bool = False) -> int:
    """Une passe complète. Retourne le nombre d'annonces suivies."""
    print("\n[%s] vérification" % horodatage())
    annonces = collecter(cfg)
    if not annonces:
        print("  aucune annonce récupérée : source indisponible ou critères trop stricts")
        return 0

    etat = Etat(FICHIER_ETAT)

    # Garde-fou : si la source renvoie soudain une fraction de l'inventaire
    # (panne partielle, index en cours de reconstruction), ne rien conclure.
    # Sans ça, le bot déclarerait des centaines d'annonces disparues, les
    # oublierait, puis les réannoncerait comme neuves au passage suivant.
    connues = len(etat.annonces)
    if connues and len(annonces) < connues * SEUIL_INVENTAIRE_SUSPECT:
        print(
            "  inventaire suspect : %d annonces contre %d au passage précédent."
            % (len(annonces), connues)
        )
        print("  état conservé en l'état, aucune alerte envoyée.")
        return connues

    contexte = Contexte(annonces)
    diff = etat.comparer(annonces)
    premier_run = etat.premier_run and not forcer_alertes

    nouvelles = diff["nouvelles"]
    baisses = []
    if cfg.alertes.get("baisses_de_prix", True):
        seuil = cfg.baisse_min_eur
        for ch in diff["changements"]:
            if "prix" not in ch["diffs"]:
                continue
            avant, apres = ch["diffs"]["prix"]
            if avant is None or apres is None:
                continue
            if abs(float(apres) - float(avant)) >= seuil:
                baisses.append(ch)

    disparues = diff["disparues"] if cfg.alertes.get("annonces_disparues") else []

    print(
        "  %d annonces suivies  |  %d nouvelle(s)  |  %d changement(s) de prix  |  %d disparue(s)"
        % (diff["total"], len(nouvelles), len(baisses), len(diff["disparues"]))
    )

    # -- journal + export ---------------------------------------------------
    # Au premier run, tout l'inventaire est "nouveau" : inutile de le deverser
    # dans l'historique, qui doit rester une liste d'evenements reels.
    evenements = [] if premier_run else [evenement("nouvelle", a) for a in nouvelles]
    evenements += [
        evenement("prix", ch["annonce"], ch["diffs"]["prix"][0]) for ch in baisses
    ]
    evenements += [evenement("disparue", a) for a in diff["disparues"]]
    journaliser(FICHIER_JOURNAL, evenements)
    exporter_csv(annonces, FICHIER_CSV)

    # -- notifications ------------------------------------------------------
    if premier_run:
        print("  premier lancement : inventaire enregistré comme référence")
        moins_cher = sorted(annonces, key=par_prix)[:3]
        notif.envoyer(
            entete_demarrage(resume_criteres(cfg), len(annonces), contexte),
            [embed_annonce(a, "info", contexte) for a in moins_cher],
        )
        for a in moins_cher:
            afficher(a)
    else:
        max_notif = cfg.max_notifications
        if nouvelles:
            print("\n  --- NOUVELLES ANNONCES ---")
            tri = sorted(nouvelles, key=par_prix)
            for a in tri:
                afficher(a)
            a_envoyer = tri[:max_notif]
            grande = len(a_envoyer) <= SEUIL_GRANDE_IMAGE
            notif.envoyer(
                entete_nouvelles(len(nouvelles), resume_criteres(cfg), len(a_envoyer)),
                [embed_annonce(a, "nouvelle", contexte, grande) for a in a_envoyer],
            )

        if baisses:
            print("\n  --- CHANGEMENTS DE PRIX ---")
            for ch in baisses:
                avant, apres = ch["diffs"]["prix"]
                print("  %s  ->  %s" % (euros(avant), euros(apres)))
                afficher(ch["annonce"])
            lot = baisses[:max_notif]
            grande = len(lot) <= SEUIL_GRANDE_IMAGE
            notif.envoyer(
                "### 📉  %d changement(s) de prix sur des annonces suivies"
                % len(baisses),
                [embed_changement_prix(ch, contexte, grande) for ch in lot],
            )

        if disparues:
            print("\n  --- ANNONCES RETIRÉES ---")
            for a in disparues:
                afficher(a)
            lot = disparues[:max_notif]
            notif.envoyer(
                "### 🏁  %d annonce(s) retirée(s) (vendues ou dépubliées)"
                % len(disparues),
                [embed_annonce(a, "disparue", None, False) for a in lot],
            )

    etat.appliquer(annonces)
    etat.sauvegarder()
    print("  export : %s" % FICHIER_CSV)
    return diff["total"]


def lister(cfg: Config, nombre: int) -> None:
    annonces = collecter(cfg)
    tri = sorted(annonces, key=par_prix)
    print("\n%d annonces  ·  les %d moins chères :\n" % (len(annonces), min(nombre, len(tri))))
    for i, a in enumerate(tri[:nombre], 1):
        print("#%02d" % i)
        afficher(a)
        print()
    exporter_csv(annonces, FICHIER_CSV)
    print("export complet : %s" % FICHIER_CSV)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="Alerte Renault Mégane E-Tech d'occasion")
    p.add_argument("--watch", action="store_true", help="boucle en continu")
    p.add_argument("--intervalle", type=int, help="minutes entre deux vérifications")
    p.add_argument("--dry-run", action="store_true", help="aucune notification Discord")
    p.add_argument("--reset", action="store_true", help="oublie les annonces connues et repart de zéro")
    p.add_argument("--forcer-alertes", action="store_true",
                   help="notifie même au premier lancement")
    p.add_argument("--lister", type=int, metavar="N",
                   help="affiche les N annonces les moins chères et sort")
    p.add_argument("--test-discord", action="store_true",
                   help="envoie un message de test sur le webhook et sort")
    args = p.parse_args(argv)

    charger_env()
    cfg = Config.charger()

    if args.reset and FICHIER_ETAT.exists():
        FICHIER_ETAT.unlink()
        print("état remis à zéro (%s supprimé)" % FICHIER_ETAT.name)

    print("Critères : %s" % resume_criteres(cfg))

    if args.test_discord:
        notif = Notificateur()
        if not notif.webhook_url:
            print("DISCORD_WEBHOOK_URL absent : crée un fichier .env "
                  "(voir .env.example) avec l'URL de ton webhook")
            return 1
        notif.envoyer(
            "### ✅  Test de connexion réussi\n"
            "-# La surveillance %s est prête." % resume_criteres(cfg)
        )
        print("message de test envoyé, vérifie ton salon Discord")
        return 0

    if args.lister:
        lister(cfg, args.lister)
        return 0

    notif = Notificateur(dry_run=args.dry_run)
    if args.dry_run:
        print("mode dry-run : aucune notification ne sera envoyée")
    elif not notif.webhook_url:
        print("attention : DISCORD_WEBHOOK_URL absent du .env, notifications désactivées")

    if not args.watch:
        verifier(cfg, notif, args.forcer_alertes)
        return 0

    minutes = args.intervalle or int(cfg.execution.get("intervalle_minutes") or 30)
    print("surveillance active, vérification toutes les %d minutes (Ctrl+C pour arrêter)" % minutes)
    forcer = args.forcer_alertes
    while True:
        try:
            verifier(cfg, notif, forcer)
        except KeyboardInterrupt:
            print("\narrêt demandé")
            return 0
        except Exception as e:  # une erreur ponctuelle ne doit pas tuer la boucle
            print("  erreur inattendue : %s" % e)
        forcer = False
        try:
            time.sleep(minutes * 60)
        except KeyboardInterrupt:
            print("\narrêt demandé")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
