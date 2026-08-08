"""
panopticon/roster/enroll_cli.py

Petit outil en ligne de commande pour l'opérateur : enrôler une personne,
lister les personnes enrôlées, ou en supprimer une (droit à l'effacement).
Ne fait pas partie du pipeline ROSTER lui-même (pas lancé par DAEMON) —
un outil d'administration, au même titre que `watch_argus.py` pour ARGUS.

Le consentement (`consent_given`) n'est JAMAIS déduit automatiquement d'un
flag de ligne de commande seul : par défaut, l'outil demande une
confirmation interactive explicite (taper "OUI" en toutes lettres) avant
tout traitement. Le flag `--yes` permet de sauter cette confirmation
interactive UNIQUEMENT pour un usage scripté/automatisé — c'est alors à
l'appelant du script de s'assurer que le consentement a bien été recueilli
en amont (ex: écran de consentement signé dans une appli tierce).

Usage :
    python3 roster/enroll_cli.py add "Alice Dupont" photo1.jpg photo2.jpg photo3.jpg --notes "famille"
    python3 roster/enroll_cli.py list
    python3 roster/enroll_cli.py delete <person_id>
"""

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from roster.config import load_config  # noqa: E402
from roster.embedder import build_embedder  # noqa: E402
from roster.enrollment import ConsentNotGivenError, EnrollmentService, NoFaceDetectedError  # noqa: E402
from roster.store import PersonStore  # noqa: E402

_DEFAULT_CONFIG_PATH = _REPO_ROOT / "roster.json"


def _load_default_config():
    config_path = str(_DEFAULT_CONFIG_PATH) if _DEFAULT_CONFIG_PATH.is_file() else None
    return load_config(config_path)


def _confirm_consent(name: str, skip_interactive: bool) -> bool:
    if skip_interactive:
        return True
    print(f"\nEnrôlement de « {name} » dans ROSTER.")
    print("Cette personne (ou son représentant légal) doit avoir donné son accord explicite")
    print("pour être reconnue par ce système de vidéosurveillance.")
    answer = input("Confirmez-vous que ce consentement a été obtenu ? Tapez OUI en toutes lettres : ").strip()
    return answer == "OUI"


def cmd_add(args: argparse.Namespace) -> int:
    config = _load_default_config()
    store = PersonStore(config.persons_db_path, config.reference_photos_dir)
    embedder = build_embedder(config.embedder)
    embedder.warmup()
    service = EnrollmentService(store, embedder, config.reference_photos_dir)

    consent_given = _confirm_consent(args.name, skip_interactive=args.yes)
    if not consent_given:
        print("Consentement non confirmé : enrôlement annulé.")
        return 1

    try:
        person = service.enroll_person(
            args.name, args.photos, consent_given=True, notes=args.notes or "",
        )
    except ConsentNotGivenError as exc:
        print(f"Erreur : {exc}")
        return 1
    except NoFaceDetectedError as exc:
        print(f"Erreur : {exc}")
        return 1

    print(f"\nPersonne enrôlée avec succès :")
    print(f"  Nom          : {person.name}")
    print(f"  Identifiant  : {person.person_id}")
    print(f"  Photos       : {len(person.embeddings)}/{len(args.photos)} exploitée(s)")
    print(f"  Consentement : horodaté à {person.consent_confirmed_at}")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    config = _load_default_config()
    store = PersonStore(config.persons_db_path, config.reference_photos_dir)
    persons = store.all()

    if not persons:
        print("Aucune personne enrôlée.")
        return 0

    print(f"{len(persons)} personne(s) enrôlée(s) :\n")
    print(f"{'Identifiant':<34}{'Nom':<24}{'Photos':<8}{'Notes'}")
    for person in persons:
        print(f"{person.person_id:<34}{person.name:<24}{len(person.embeddings):<8}{person.notes}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    config = _load_default_config()
    store = PersonStore(config.persons_db_path, config.reference_photos_dir)

    person = store.get(args.person_id)
    if person is None:
        print(f"Aucune personne trouvée avec l'identifiant : {args.person_id}")
        return 1

    if not args.yes:
        answer = input(f"Supprimer définitivement « {person.name} » (id={person.person_id}) "
                        f"et ses {len(person.reference_photo_paths)} photo(s) de référence ? "
                        f"Tapez OUI en toutes lettres : ").strip()
        if answer != "OUI":
            print("Suppression annulée.")
            return 1

    deleted = store.delete_person(args.person_id)
    print("Personne supprimée." if deleted else "Échec de la suppression (identifiant introuvable).")
    return 0 if deleted else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Administration des personnes enrôlées dans ROSTER")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Enrôler une nouvelle personne")
    add_parser.add_argument("name", help="Nom de la personne")
    add_parser.add_argument("photos", nargs="+", help="Chemins des photos de référence (3-5 recommandées)")
    add_parser.add_argument("--notes", default="", help="Note libre (ex: 'famille', 'voisin autorisé')")
    add_parser.add_argument("--yes", action="store_true",
                             help="Ignore la confirmation interactive (usage scripté uniquement — "
                                  "le consentement doit avoir été recueilli en amont)")
    add_parser.set_defaults(func=cmd_add)

    list_parser = subparsers.add_parser("list", help="Lister les personnes enrôlées")
    list_parser.set_defaults(func=cmd_list)

    delete_parser = subparsers.add_parser("delete", help="Supprimer une personne (droit à l'effacement)")
    delete_parser.add_argument("person_id", help="Identifiant de la personne à supprimer")
    delete_parser.add_argument("--yes", action="store_true", help="Ignore la confirmation interactive")
    delete_parser.set_defaults(func=cmd_delete)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
