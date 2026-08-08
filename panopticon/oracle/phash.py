"""
panopticon/oracle/phash.py

Hash perceptuel (difference hash / dHash) d'un crop d'image, utilisé par le
cache d'ORACLE pour reconnaître qu'un objet a déjà été identifié même si le
crop courant n'est pas pixel-identique au précédent (angle légèrement
différent, compression JPEG, éclairage qui a bougé...). Implémenté à la main
via OpenCV + NumPy, déjà dépendances d'ARGUS — pas besoin d'ajouter
`imagehash`/Pillow rien que pour ça, cohérent avec le reste du projet.

dHash plutôt qu'aHash (average hash) : compare des pixels voisins entre eux
plutôt qu'à une moyenne globale, ce qui le rend moins sensible aux variations
uniformes de luminosité/exposition d'une frame à l'autre — pertinent ici
puisque SPECTRA peut par ailleurs faire varier la luminosité perçue du flux.
"""

import cv2
import numpy as np


def compute_dhash(image: np.ndarray, hash_size: int = 8) -> str:
    """
    Calcule le dHash de `image` sous forme de chaîne hexadécimale.
    `hash_size=8` -> grille 8x8 -> 64 bits -> 16 caractères hex.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    # hash_size+1 colonnes pour pouvoir comparer chaque pixel à son voisin de droite.
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]

    value = 0
    for bit in diff.flatten():
        value = (value << 1) | int(bit)

    hex_digits = (hash_size * hash_size + 3) // 4  # arrondi au chiffre hex supérieur
    return format(value, f"0{hex_digits}x")


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Nombre de bits différents entre deux dHash hexadécimaux (0 = identiques)."""
    int_a, int_b = int(hash_a, 16), int(hash_b, 16)
    return bin(int_a ^ int_b).count("1")
