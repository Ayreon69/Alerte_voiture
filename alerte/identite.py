"""
Reconnaître une même voiture d'une annonce à l'autre.

Deux besoins distincts s'appuient là-dessus :

- le **dédoublonnage** (`run.grouper`), qui rassemble les annonces d'un même
  véhicule publiées sur plusieurs sites ;
- la **mémoire** (`store.Etat`), qui doit retrouver un véhicule déjà connu même
  si l'annonce retenue pour le représenter a changé de site entre deux
  passages.

Ce second besoin n'est pas théorique : l'index de renew.auto clignote de
quelques annonces d'un passage à l'autre. Quand une voiture en disparaît un
instant, sa jumelle AutoScout24 prend le relais avec un identifiant différent
— et sans reconnaissance par identité, elle serait annoncée comme une
nouveauté. Mesuré le 27/08/2026 : 121 fausses nouvelles sur 122, puis 18 sur 21.
"""
from __future__ import annotations


def plaque_de(a: dict) -> str:
    return (a.get("immatriculation") or "").upper().replace(" ", "")


def cles_identite(a: dict) -> list:
    """Clés permettant de reconnaître une même voiture d'un site à l'autre.

    La plaque d'immatriculation est la seule vraiment fiable : renew.auto la
    publie, et AutoScout24 la glisse dans son `crossReferenceId`. LeBonCoin ne
    la publie pas du tout — sans repli, chaque voiture qu'un concessionnaire y
    republie alerterait deux fois.

    D'où l'empreinte : kilométrage exact, mois de première mise en circulation
    et département. Le kilométrage au kilomètre près suffit presque seul à
    identifier un véhicule ; les deux autres champs sont là pour écarter la
    coïncidence. Elle n'est calculée que si les trois sont connus — une
    empreinte incomplète confondrait des voitures différentes, ce qui est bien
    pire qu'un doublon.
    """
    cles = []
    plaque = plaque_de(a)
    if plaque:
        cles.append(("plaque", plaque))
    km, immat, dep = a.get("km"), a.get("date_1re_immat"), a.get("departement")
    if km and immat and dep:
        # AutoScout24 et LeBonCoin s'arretent au mois, renew.auto donne le
        # jour : on tronque au mois, la precision commune aux trois.
        cles.append(("empreinte", int(km), str(immat)[:7], str(dep)))
    return cles


def plaques_incompatibles(a: dict, b: dict) -> bool:
    """Deux plaques connues et differentes : deux voitures differentes.

    Garde-fou sur l'empreinte, qui pourrait sinon confondre deux exemplaires
    jumeaux du meme concessionnaire — meme mois, meme departement, et un
    compteur arrete au meme kilometre. La plaque, quand les deux sites la
    publient, a toujours le dernier mot.
    """
    pa, pb = plaque_de(a), plaque_de(b)
    return bool(pa and pb and pa != pb)
