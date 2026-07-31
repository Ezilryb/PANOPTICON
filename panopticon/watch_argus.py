"""
panopticon/watch_argus.py

Petit outil de diagnostic (ne fait pas partie d'ARGUS lui-même) : se
connecte au bus de publication et affiche en direct chaque évènement reçu
(latence, détections, track_id). Avec --snapshot-every, sauvegarde aussi
une image JPEG de temps en temps pour vérifier visuellement ce qu'ARGUS
"voit" réellement — utile pour valider le branchement d'une vraie caméra.
"""

import argparse
from pathlib import Path

import cv2

from argus.client import ArgusClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Observe en direct les évènements publiés par ARGUS")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--snapshot-every", type=int, default=0,
                         help="Sauvegarde une image JPEG toutes les N évènements reçus (0 = désactivé)")
    parser.add_argument("--snapshot-dir", default="snapshots")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = ArgusClient(args.host, args.port)
    print(f"Connexion à ARGUS sur {args.host}:{args.port}...")
    client.connect()
    print("Connecté. Ctrl+C pour arrêter.\n")

   if args.snapshot_every > 0 and count % args.snapshot_every == 0:
                frame = client.read_frame(event)
                if frame is None:
                    print(f"          -> AUCUNE frame disponible en mémoire partagée pour {event.camera_id} "
                          f"(ARGUS pas encore écrit / segment introuvable)")
                else:
                    out_path = Path(args.snapshot_dir) / f"{event.camera_id}_{event.frame_id}.jpg"
                    ok = cv2.imwrite(str(out_path), frame)
                    if ok:
                        print(f"          -> snapshot sauvegardé : {out_path.resolve()}")
                    else:
                        print(f"          -> ÉCHEC d'écriture du snapshot : {out_path.resolve()}")

    count = 0
    try:
        for event in client.events():
            count += 1
            dets = ", ".join(
                f"{d.class_name}#{d.track_id}({d.confidence:.2f})" for d in event.detections
            ) or "aucune détection"
            print(f"[{count:>5}] {event.camera_id:<12} frame={event.frame_id:<6} "
                  f"latence={event.latency_ms:6.1f}ms  {dets}")

            if args.snapshot_every > 0 and count % args.snapshot_every == 0:
                frame = client.read_frame(event)
                if frame is not None:
                    out_path = Path(args.snapshot_dir) / f"{event.camera_id}_{event.frame_id}.jpg"
                    cv2.imwrite(str(out_path), frame)
                    print(f"          -> snapshot sauvegardé : {out_path}")
    except KeyboardInterrupt:
        print("\nArrêt demandé.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
