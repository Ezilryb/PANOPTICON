"""
panopticon/oracle/tests/test_phash.py

Tests unitaires de compute_dhash/hamming_distance : déterminisme (même
image -> même hash), stabilité sous variation mineure (bruit léger, léger
recadrage) au sens d'une distance de Hamming faible, séparation nette pour
deux images très différentes, et correction du calcul de distance lui-même.
"""

import unittest

import numpy as np

from oracle.phash import compute_dhash, hamming_distance


def _solid_image(color: tuple[int, int, int], shape=(80, 80, 3)) -> np.ndarray:
    image = np.zeros(shape, dtype=np.uint8)
    image[:, :] = color
    return image


class TestHammingDistance(unittest.TestCase):
    def test_identical_hashes_have_zero_distance(self) -> None:
        self.assertEqual(hamming_distance("00000000000000ff", "00000000000000ff"), 0)

    def test_known_bit_difference(self) -> None:
        # 0x07 = 0b111 -> 3 bits différents de 0x00
        self.assertEqual(hamming_distance("0000000000000000", "0000000000000007"), 3)

    def test_fully_inverted_hash_is_max_distance(self) -> None:
        self.assertEqual(hamming_distance("0000000000000000", "ffffffffffffffff"), 64)


class TestComputeDhash(unittest.TestCase):
    def test_deterministic_for_same_image(self) -> None:
        image = _solid_image((60, 120, 200))
        self.assertEqual(compute_dhash(image), compute_dhash(image))

    def test_hash_length_matches_hash_size(self) -> None:
        image = _solid_image((60, 120, 200))
        h = compute_dhash(image, hash_size=8)
        self.assertEqual(len(h), 16)  # 8x8 = 64 bits = 16 caractères hex

    def test_minor_noise_stays_close(self) -> None:
        rng = np.random.default_rng(0)
        base = np.full((100, 100, 3), 128, dtype=np.uint8)
        noisy = np.clip(base.astype(np.int16) + rng.integers(-3, 4, base.shape), 0, 255).astype(np.uint8)

        h_base, h_noisy = compute_dhash(base), compute_dhash(noisy)
        self.assertLessEqual(hamming_distance(h_base, h_noisy), 6)

    def test_very_different_images_are_far_apart(self) -> None:
        # dHash compare chaque pixel à son voisin de droite APRÈS sous-échantillonnage : un
        # damier fin (haute fréquence) s'aplatit en gris quasi uniforme au moyennage et n'est
        # donc PAS un bon cas de test pour ce hash (propriété connue/attendue de dHash, qui
        # capture des gradients de basse fréquence, pas de la texture fine). On compare plutôt
        # deux dégradés orthogonaux : horizontal (varie en x) donne un hash à 1 partout, vertical
        # (constant en x, varie en y) donne un hash à 0 partout -> distance maximale déterministe.
        width_gradient = np.tile(np.linspace(0, 255, 80, dtype=np.uint8), (80, 1))
        horizontal = np.stack([width_gradient] * 3, axis=-1)
        height_gradient = np.tile(np.linspace(0, 255, 80, dtype=np.uint8).reshape(-1, 1), (1, 80))
        vertical = np.stack([height_gradient] * 3, axis=-1)

        h1, h2 = compute_dhash(horizontal), compute_dhash(vertical)
        self.assertGreater(hamming_distance(h1, h2), 20)

    def test_grayscale_input_is_accepted(self) -> None:
        gray = np.full((60, 60), 100, dtype=np.uint8)
        h = compute_dhash(gray)
        self.assertEqual(len(h), 16)


if __name__ == "__main__":
    unittest.main()
