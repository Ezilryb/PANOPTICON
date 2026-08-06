"""
panopticon/watch_argus.py

Petit outil de diagnostic (ne fait pas partie d'ARGUS lui-même) : se
connecte au bus de publication et affiche en direct chaque évènement reçu
(latence, détections, track_id). Peut aussi ouvrir une fenêtre caméra en
direct avec les bounding boxes dessinées dessus, activable/désactivable à
tout moment (touche Q ou commande "cam off") sans jamais interrompre ARGUS
ni la réception des évènements : fermer la fenêtre ne fait qu'arrêter
l'affichage, tout le pipeline continue de tourner en arrière-plan.

IMPORTANT : le dessin des bounding boxes (`_draw_detections`) est UNE SEULE
fonction, réutilisée à la fois par la fenêtre live et par la sauvegarde des
snapshots JPEG. Avant ce fichier, la sauvegarde écrivait la frame brute sans
jamais y dessiner les détections -- c'est pour ça qu'aucun carré vert
n'apparaissait jamais sur les images sauvegardées, même quand ARGUS
détectait correctement quelque chose.
"""

import argparse
import threading
from pathlib import Path

import cv2
import numpy as np

from argus.client import ArgusClient
from argus.data_types import DetectionEvent

_WINDOW_NAME = "ARGUS - vue live (Q: fermer | F: plein ecran)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Observe en direct les évènements publiés par ARGUS")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--snapshot-every", type=int, default=0,
                         help="Sauvegarde une image JPEG toutes les N évènements reçus (0 = désactivé)")
    parser.add_argument("--snapshot-dir", default="snapshots")
    parser.add_argument("--no-display-prompt", action="store_true",
                         help="Ne pas demander l'ouverture de la fenêtre caméra au démarrage (reste fermée)")
    return parser.parse_args()


def _draw_detections(image: np.ndarray, event: DetectionEvent) -> np.ndarray:
    """
    Dessine les bounding boxes + labels + track_id sur une COPIE de `image`.
    Fonction unique utilisée à la fois pour l'affichage live et pour les
    snapshots sauvegardés sur disque, pour être sûr que les deux montrent
    exactement les mêmes détections.
    """
    canvas = image.copy()
    for det in event.detections:
        x1, y1, x2, y2 = (int(v) for v in det.bbox)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (60, 220, 60), 2)
        label = f"{det.class_name}#{det.track_id} {det.confidence:.2f}"
        cv2.putText(canvas, label, (x1, max(0, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (60, 220, 60), 1, cv2.LINE_AA)

    cv2.putText(canvas, f"{event.camera_id}  latence={event.latency_ms:.0f}ms  "
                         f"({len(event.detections)} detection(s))",
                (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return canvas


class CameraDisplay:
    """
    Gère la fenêtre OpenCV d'affichage caméra en direct, indépendamment de la
    boucle de lecture des évènements. Peut être ouverte/fermée à volonté
    (touche Q dans la fenêtre, ou commande console "cam on"/"cam off") ;
    fermer la fenêtre n'arrête jamais ARGUS ni la réception des évènements.
    """

    def __init__(self) -> None:
        self._open = False
        self._fullscreen = False

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        if self._open:
            return
        cv2.namedWindow(_WINDOW_NAME, cv2.WINDOW_NORMAL)
        self._open = True
        self._fullscreen = False
        print("[CAMERA] Fenêtre ouverte (Q pour fermer, F pour plein écran).")

    def close(self) -> None:
        if not self._open:
            return
        cv2.destroyWindow(_WINDOW_NAME)
        self._open = False
        print("[CAMERA] Fenêtre fermée. ARGUS continue de tourner en arrière-plan.")

    def toggle_fullscreen(self) -> None:
        if not self._open:
            return
        self._fullscreen = not self._fullscreen
        cv2.setWindowProperty(
            _WINDOW_NAME, cv2.WND_PROP_FULLSCREEN,
            cv2.WINDOW_FULLSCREEN if self._fullscreen else cv2.WINDOW_NORMAL,
        )

    def render(self, image: np.ndarray, event: DetectionEvent) -> None:
        """Affiche `image` annotée des détections, et traite les touches Q/F."""
        if not self._open:
            return

        canvas = _draw_detections(image, event)
        cv2.imshow(_WINDOW_NAME, canvas)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), ord("Q"), 27):  # 27 = ESC
            self.close()
        elif key in (ord("f"), ord("F")):
            self.toggle_fullscreen()


def _console_command_loop(display: CameraDisplay, stop_event: threading.Event) -> None:
    """
    Tourne dans un thread séparé : lit des commandes tapées au clavier dans
    la console pour ouvrir/fermer la caméra à tout moment, y compris après
    une fermeture précédente (ré-ouverture possible autant de fois que voulu).
    """
    print("Commandes disponibles : 'cam on' (afficher la caméra), 'cam off' (la masquer), 'quit' (arrêter watch_argus).")
    while not stop_event.is_set():
        try:
            line = input().strip().lower()
        except EOFError:
            return
        if line in ("cam on", "on", "cam"):
            display.open()
        elif line in ("cam off", "off"):
            display.close()
        elif line in ("quit", "exit"):
            stop_event.set()
            return


def _ask_yes_no(question: str, default: bool = False) -> bool:
    suffix = " [O/n] " if default else " [o/N] "
    try:
        answer = input(question + suffix).strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("o", "oui", "y", "yes")


def main() -> None:
    args = parse_args()
    client = ArgusClient(args.host, args.port)
    print(f"Connexion à ARGUS sur {args.host}:{args.port}...")
    client.connect()
    print("Connecté.\n")

    display = CameraDisplay()
    if not args.no_display_prompt:
        if _ask_yes_no("Afficher la caméra en direct dans une fenêtre ?"):
            display.open()

    stop_event = threading.Event()
    input_thread = threading.Thread(
        target=_console_command_loop, args=(display, stop_event), daemon=True,
    )
    input_thread.start()

    Path(args.snapshot_dir).mkdir(parents=True, exist_ok=True)

    count = 0
    try:
        for event in client.events():
            if stop_event.is_set():
                break
            count += 1
            dets = ", ".join(
                f"{d.class_name}#{d.track_id}({d.confidence:.2f})" for d in event.detections
            ) or "aucune détection"
            print(f"[{count:>5}] {event.camera_id:<12} frame={event.frame_id:<6} "
                  f"latence={event.latency_ms:6.1f}ms  {dets}")

            want_snapshot = args.snapshot_every > 0 and count % args.snapshot_every == 0
            need_frame = display.is_open or want_snapshot
            frame = client.read_frame(event) if need_frame else None

            if display.is_open:
                if frame is not None:
                    display.render(frame, event)
                else:
                    cv2.waitKey(1)  # garde la fenêtre réactive (Q/F) même sans frame disponible

            if want_snapshot:
                if frame is None:
                    print(f"          -> AUCUNE frame disponible pour {event.camera_id} "
                          f"(ARGUS pas encore écrit / fichier introuvable)")
                else:
                    annotated = _draw_detections(frame, event)
                    out_path = Path(args.snapshot_dir) / f"{event.camera_id}_{event.frame_id}.jpg"
                    ok = cv2.imwrite(str(out_path), annotated)
                    if ok:
                        print(f"          -> snapshot sauvegardé ({len(event.detections)} detection(s)) : {out_path.resolve()}")
                    else:
                        print(f"          -> ÉCHEC d'écriture du snapshot : {out_path.resolve()}")
    except KeyboardInterrupt:
        print("\nArrêt demandé.")
    finally:
        stop_event.set()
        display.close()
        client.close()


if __name__ == "__main__":
    main()
