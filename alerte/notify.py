"""
Notifications Discord (webhook) + affichage console.

Discord accepte au maximum 10 embeds par message : les annonces sont donc
envoyees par paquets de 10, avec une pause entre chaque message et le respect
du `retry_after` en cas de 429.

Mise en page d'une annonce :
  - le prix ouvre le titre, c'est ce qu'on lit en premier ;
  - la description porte les badges (bonne affaire, faible kilometrage...)
    calcules par rapport au reste du stock suivi ;
  - deux rangees de trois champs pour les caracteristiques, puis le lieu ;
  - grande photo quand il y a peu d'annonces, vignette quand il y en a
    beaucoup, pour qu'un lot de 20 reste parcourable.
"""
from __future__ import annotations

import os
import statistics
import time

import requests

from .format import (
    anciennete,
    date_longue,
    decimal,
    euros,
    jauge,
    jours_depuis,
    kilometres,
    mois_annee,
    nom_propre,
    nombre,
    phrase,
    telephone,
    titre_vehicule,
)

MAX_EMBEDS = 10
SEUIL_GRANDE_IMAGE = 3  # au-dela, on passe en vignettes

COULEUR_NOUVELLE = 0x00A651   # vert Renault
COULEUR_BAISSE = 0xE8792A     # orange
COULEUR_HAUSSE = 0x8E9196     # gris
COULEUR_DISPARUE = 0x4A4E52   # gris fonce
COULEUR_INFO = 0x0B79D0       # bleu

EVENEMENTS = {
    "nouvelle": ("🚗  Nouvelle annonce", COULEUR_NOUVELLE),
    "baisse": ("📉  Baisse de prix", COULEUR_BAISSE),
    "hausse": ("📈  Hausse de prix", COULEUR_HAUSSE),
    "disparue": ("🏁  Annonce retirée", COULEUR_DISPARUE),
    "info": ("⭐  Sélection du moment", COULEUR_INFO),
}


class Notificateur:
    """Envoie les alertes vers Discord. En mode `dry_run`, n'envoie rien."""

    def __init__(self, webhook_url: str | None = None, dry_run: bool = False):
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
        self.dry_run = dry_run

    @property
    def actif(self) -> bool:
        return bool(self.webhook_url) and not self.dry_run

    # -- envoi bas niveau --------------------------------------------------
    def _poster(self, payload: dict) -> None:
        for _ in range(4):
            try:
                r = requests.post(self.webhook_url, json=payload, timeout=30)
            except requests.RequestException as e:
                print("  [discord] erreur réseau : %s" % e)
                return
            if r.status_code in (200, 204):
                return
            if r.status_code == 429:
                attente = 2.0
                try:
                    attente = float(r.json().get("retry_after", 2)) + 0.5
                except ValueError:
                    pass
                print("  [discord] limite de débit, pause %.1fs" % attente)
                time.sleep(attente)
                continue
            print("  [discord] échec %s : %s" % (r.status_code, r.text[:200]))
            return
        print("  [discord] abandon après plusieurs tentatives")

    def envoyer(self, contenu: str, embeds: list[dict] | None = None) -> None:
        embeds = embeds or []
        if not self.actif:
            if not self.webhook_url and not self.dry_run:
                print("  [discord] DISCORD_WEBHOOK_URL absent du .env : pas d'envoi")
            return
        if not embeds:
            self._poster({"content": contenu[:1900]})
            return
        for i in range(0, len(embeds), MAX_EMBEDS):
            paquet = embeds[i:i + MAX_EMBEDS]
            payload = {"embeds": paquet}
            if i == 0 and contenu:
                payload["content"] = contenu[:1900]
            self._poster(payload)
            if i + MAX_EMBEDS < len(embeds):
                time.sleep(1.0)


class Contexte:
    """Situe une annonce par rapport au reste du stock suivi.

    Sans point de comparaison, "18 899 €" ne veut rien dire ; savoir que c'est
    moins cher que 92 % des annonces suivies, si.
    """

    def __init__(self, annonces: list[dict]):
        self.prix = sorted(a["prix"] for a in annonces if a.get("prix"))
        self.km = sorted(a["km"] for a in annonces if a.get("km"))
        self.total = len(annonces)

    @staticmethod
    def _rang(valeurs: list, v) -> float | None:
        """Part des annonces strictement moins bien placees, en %."""
        if not valeurs or v is None:
            return None
        dessous = sum(1 for x in valeurs if x < v)
        return 100.0 * dessous / len(valeurs)

    def percentile_prix(self, prix) -> float | None:
        return self._rang(self.prix, prix)

    def percentile_km(self, km) -> float | None:
        return self._rang(self.km, km)

    @property
    def prix_median(self):
        return statistics.median(self.prix) if self.prix else None

    def badges(self, a: dict) -> list[str]:
        """Quelques arguments courts, du plus rare au plus courant : les quatre
        premiers seulement sont affiches."""
        sortie = []
        p = self.percentile_prix(a.get("prix"))
        if p is not None:
            if p <= 5:
                sortie.append("🔥 **Parmi les 5 % les moins chères** du stock suivi")
            elif p <= 15:
                sortie.append("💶 Moins chère que %.0f %% des annonces suivies" % (100 - p))
        jours = jours_depuis(a.get("date_publication"))
        if jours and jours >= 90:
            sortie.append("⏳ En ligne depuis %d jours, marge de négociation probable" % jours)
        soh = a.get("batterie_soh")
        if soh is not None and soh >= 97:
            sortie.append("🔋 Batterie quasi neuve (%s %%)" % soh)
        k = self.percentile_km(a.get("km"))
        if k is not None and k <= 15:
            sortie.append("🛣️ Kilométrage parmi les plus bas du stock")
        if a.get("prix") and a.get("prix_neuf"):
            decote = 100 - (float(a["prix"]) / float(a["prix_neuf"]) * 100)
            if decote >= 40:
                sortie.append("📉 −%.0f %% par rapport au neuf (%s)" % (decote, euros(a["prix_neuf"])))
        if a.get("pompe_a_chaleur"):
            sortie.append("🌡️ Pompe à chaleur")
        return sortie[:4]


LIBELLES_SOURCES = {
    "renew": "renew.auto",
    "autoscout24": "AutoScout24",
}


def _pied_de_page(a: dict) -> str:
    """Provenance, date de publication si connue, immatriculation."""
    bouts = [LIBELLES_SOURCES.get(a.get("source"), a.get("source") or "?")]
    aussi = [LIBELLES_SOURCES.get(s, s) for s in (a.get("aussi_sur") or [])]
    if aussi:
        bouts.append("aussi sur " + ", ".join(aussi))
    if a.get("date_publication"):
        bouts.append("publiée le %s" % date_longue(a["date_publication"]))
    identifiant = a.get("immatriculation") or (a.get("vin") or "")[:8]
    if identifiant:
        bouts.append(identifiant)
    return "  ·  ".join(bouts)


# -- indicateurs derives ---------------------------------------------------
def jours_en_ligne(a: dict) -> int | None:
    """Depuis combien de jours l'annonce est publiee."""
    return jours_depuis(a.get("date_publication"))


def km_par_an(a: dict) -> int | None:
    """Intensite d'usage : 130 000 km sur 3 ans, ce n'est pas la meme voiture
    qu'un meme kilometrage sur 8 ans."""
    km, jours = a.get("km"), jours_depuis(a.get("date_1re_immat"))
    if not km or not jours or jours < 180:
        return None
    return int(round(float(km) / (jours / 365.25), -2))


# -- construction des embeds ----------------------------------------------
def embed_annonce(
    a: dict,
    evenement: str = "nouvelle",
    contexte: Contexte | None = None,
    grande_image: bool = True,
    description: str = "",
) -> dict:
    entete, couleur = EVENEMENTS.get(evenement, EVENEMENTS["nouvelle"])

    titre = titre_vehicule(a.get("titre")) or "Mégane E-Tech"
    if a.get("prix"):
        titre = "%s  ·  %s" % (euros(a["prix"]), titre)

    lignes = [description] if description else []
    if contexte:
        lignes.extend(contexte.badges(a))
    drapeaux = []
    if a.get("reserve"):
        drapeaux.append("⚠️ déjà réservée")
    if a.get("accidente"):
        drapeaux.append("⚠️ sinistre déclaré")
    if not a.get("photo"):
        drapeaux.append("📷 pas de photo en ligne")
    if drapeaux:
        lignes.append(" · ".join(drapeaux))

    soh = a.get("batterie_soh")
    batterie = "EV%s" % (a.get("batterie_kwh") or "?")
    if soh is not None:
        batterie = "%s · **%s %%**\n%s" % (batterie, soh, jauge(soh))
        if a.get("batterie_capacite_restante_kwh"):
            batterie += "\n%s utiles" % decimal(
                a["batterie_capacite_restante_kwh"], "kWh"
            )

    immat = a.get("date_1re_immat") or ""
    age = anciennete(immat)
    mise_en_circulation = mois_annee(immat) + (("\n%s" % age) if age else "")

    lieu = "**%s**" % (nom_propre(a.get("concession")) or "Concession inconnue")
    ville = nom_propre(a.get("ville"))
    if ville:
        lieu += "\n%s %s" % (a.get("code_postal") or "", ville)
    tel = telephone(a.get("telephone"))
    if tel:
        lieu += "\n☎ %s" % tel

    km_texte = kilometres(a.get("km"))
    rythme = km_par_an(a)
    if rythme:
        km_texte += "\n%s/an" % kilometres(rythme)

    charge = a.get("charge_ac_kw")
    charge_texte = "?" if not charge else "%s kW en AC\n%s" % (
        charge,
        "super charge" if charge >= 22 else "optimum charge",
    )

    champs = [
        {"name": "💰  Prix", "value": "**%s**" % euros(a.get("prix")), "inline": True},
        {"name": "🛣️  Kilométrage", "value": km_texte, "inline": True},
        {"name": "📅  Mise en circulation", "value": mise_en_circulation, "inline": True},
        {"name": "🔋  Batterie", "value": batterie, "inline": True},
        {"name": "🔌  Charge", "value": charge_texte, "inline": True},
        {"name": "🎨  Couleur", "value": phrase(a.get("couleur")) or "?", "inline": True},
        {"name": "📍  Où la voir", "value": lieu, "inline": False},
    ]

    bas = [
        {"name": "🏷️  Finition", "value": phrase(a.get("finition")) or "—", "inline": True},
        {"name": "🛡️  Garantie", "value": phrase(a.get("garantie")) or "—", "inline": True},
    ]
    jours = jours_en_ligne(a)
    if jours is not None:
        bas.append({
            "name": "⏳  En ligne depuis",
            "value": "%d jour%s" % (jours, "s" if jours > 1 else ""),
            "inline": True,
        })
    champs.extend(bas)

    embed = {
        "author": {"name": entete},
        "title": titre[:250],
        "url": a.get("url"),
        "color": couleur,
        "fields": champs,
        "footer": {"text": _pied_de_page(a)},
    }
    if lignes:
        embed["description"] = "\n".join(lignes)[:1000]
    if a.get("photo"):
        embed["image" if grande_image else "thumbnail"] = {"url": a["photo"]}
    return embed


def embed_changement_prix(
    changement: dict,
    contexte: Contexte | None = None,
    grande_image: bool = True,
) -> dict:
    a, avant = changement["annonce"], changement["avant"]
    p_avant, p_apres = avant.get("prix"), a.get("prix")
    ecart = (p_apres - p_avant) if (p_avant and p_apres) else 0
    baisse = ecart < 0
    description = "~~%s~~  →  **%s**   (%s%s)" % (
        euros(p_avant),
        euros(p_apres),
        "−" if baisse else "+",
        euros(abs(ecart)),
    )
    return embed_annonce(
        a,
        "baisse" if baisse else "hausse",
        contexte,
        grande_image,
        description=description,
    )


def entete_nouvelles(nombre_total: int, criteres: str, detaillees: int) -> str:
    """Ligne d'introduction au-dessus des embeds."""
    if nombre_total == 1:
        texte = "### 🚗  Une nouvelle Mégane correspond à ta recherche"
    else:
        texte = "### 🚗  %d nouvelles Mégane correspondent à ta recherche" % nombre_total
    texte += "\n-# %s" % criteres
    if detaillees < nombre_total:
        texte += "\n-# Les %d moins chères sont détaillées ci-dessous." % detaillees
    return texte


def entete_demarrage(criteres: str, total: int, contexte: Contexte) -> str:
    median = euros(contexte.prix_median)
    return (
        "### ✅  Surveillance activée\n"
        "-# %s\n"
        "**%s annonces** correspondent aujourd'hui, prix médian **%s**.\n"
        "Tu seras alerté dès qu'une nouvelle apparaît. "
        "Voici les 3 moins chères du moment :" % (criteres, nombre(total), median)
    )


# -- console ---------------------------------------------------------------
def afficher(a: dict, prefixe: str = "  ") -> None:
    """Affichage console : accents et € passent en cp1252, pas les emojis."""
    soh = a.get("batterie_soh")
    print(
        "%s%-13s %-12s %-16s SoH %-4s %s (%s)"
        % (
            prefixe,
            euros(a.get("prix")) if a.get("prix") is not None else "prix n.c.",
            kilometres(a.get("km")),
            mois_annee(a.get("date_1re_immat")),
            "%s%%" % soh if soh is not None else "?",
            nom_propre(a.get("ville")) or "?",
            a.get("departement") or "??",
        )
    )
    print("%s  %s" % (prefixe, titre_vehicule(a.get("titre"))))
    print("%s  %s" % (prefixe, a.get("url")))
