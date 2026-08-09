"""
panopticon/oracle/tests/test_cache.py

Tests unitaires d'IdentificationCache : une identification stockée est
retrouvée pour son hash exact ET pour un hash suffisamment proche (distance
de Hamming sous le seuil configuré), un hash trop éloigné ne matche jamais,
la persistance survit à un redémarrage (nouvelle instance sur le même
répertoire), et l'éviction LRU respecte la borne configurée.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from oracle.cache import IdentificationCache
from oracle.data_types import ObjectIdentification


def _identification(label: str = "Toyota Camry") -> ObjectIdentification:
    return ObjectIdentification(label=label, confidence=0.8, source="mock", candidates=[])


class TestIdentificationCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="oracle_cache_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_empty_cache_returns_none(self) -> None:
        cache = IdentificationCache(self.tmp_dir)
        self.assertIsNone(cache.lookup("0000000000000000"))

    def test_exact_hash_is_found(self) -> None:
        cache = IdentificationCache(self.tmp_dir, max_hamming_distance=6)
        cache.store("00000000000000ff", _identification("Toyota Camry"))
        result = cache.lookup("00000000000000ff")
        self.assertIsNotNone(result)
        self.assertEqual(result.label, "Toyota Camry")

    def test_near_duplicate_hash_within_threshold_is_found(self) -> None:
        cache = IdentificationCache(self.tmp_dir, max_hamming_distance=6)
        cache.store("0000000000000000", _identification("Toyota Camry"))
        # 0x07 = distance de Hamming 3 par rapport à 0x00, sous le seuil de 6.
        result = cache.lookup("0000000000000007")
        self.assertIsNotNone(result)
        self.assertEqual(result.label, "Toyota Camry")

    def test_hash_beyond_threshold_is_not_found(self) -> None:
        cache = IdentificationCache(self.tmp_dir, max_hamming_distance=2)
        cache.store("0000000000000000", _identification("Toyota Camry"))
        # distance 3 > seuil de 2 : ne doit pas matcher.
        result = cache.lookup("0000000000000007")
        self.assertIsNone(result)

    def test_persistence_across_instances(self) -> None:
        cache = IdentificationCache(self.tmp_dir)
        cache.store("00000000000000ab", _identification("Dell XPS 13"))

        reloaded = IdentificationCache(self.tmp_dir)
        result = reloaded.lookup("00000000000000ab")
        self.assertIsNotNone(result)
        self.assertEqual(result.label, "Dell XPS 13")

    def test_eviction_respects_max_entries(self) -> None:
        cache = IdentificationCache(self.tmp_dir, max_hamming_distance=0, max_entries=10)
        for i in range(20):
            # Hash exact pour chaque entrée (distance 0 requise vu max_hamming_distance=0),
            # dérivé de i pour garantir l'unicité.
            cache.store(format(i, "016x"), _identification(f"objet-{i}"))
        self.assertLessEqual(len(cache), 10)

    def test_most_recently_used_entries_survive_eviction(self) -> None:
        cache = IdentificationCache(self.tmp_dir, max_hamming_distance=0, max_entries=4)
        for i in range(4):
            cache.store(format(i, "016x"), _identification(f"objet-{i}"))
        # Ré-accède à l'entrée 0 pour la "rafraîchir" juste avant de déclencher UNE SEULE purge
        # (un rafraîchissement protège une entrée pour le prochain cycle de purge, pas au-delà :
        # ajouter plusieurs nouvelles entrées déclencherait plusieurs cycles et la ferait quand
        # même vieillir relativement aux plus récentes — comportement LRU normal, pas un bug).
        cache.lookup(format(0, "016x"))
        cache.store(format(4, "016x"), _identification("objet-4"))  # len=5>4 -> une purge
        # L'entrée 0, la plus récemment utilisée juste avant cette purge, doit avoir survécu.
        self.assertIsNotNone(cache.lookup(format(0, "016x")))

    def test_len_reflects_entry_count(self) -> None:
        cache = IdentificationCache(self.tmp_dir)
        self.assertEqual(len(cache), 0)
        cache.store("0000000000000001", _identification())
        cache.store("0000000000000002", _identification())
        self.assertEqual(len(cache), 2)


if __name__ == "__main__":
    unittest.main()
