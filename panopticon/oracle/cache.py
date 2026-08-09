"""
panopticon/oracle/cache.py

Cache d'identifications indexé par hash perceptuel (dHash, cf. phash.py) :
évite de repayer l'API de recherche d'image pour un objet déjà identifié
(cf. section 5 du brief projet — "Cache par hash perceptuel pour éviter de
repayer l'API sur le même objet"). Persisté sur disque (JSON, écriture
atomique — même technique que `roster/store.py`) pour survivre à un
redémarrage d'ORACLE, condition nécessaire pour réellement économiser des
appels API sur la durée plutôt que seulement au sein d'un même processus.

IMPORTANT — ce que ce cache n'est PAS : un journal historique des
identifications (ce sera le rôle de VAULT/SYS-LOG, hors périmètre d'ORACLE).
C'est un mécanisme de performance/coût à capacité bornée (LRU au-delà de
`max_entries`), sans garantie de rétention long terme.
"""

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

from .data_types import ObjectIdentification
from .phash import hamming_distance

logger = logging.getLogger("oracle.cache")


class IdentificationCache:
    """
    `lookup()` renvoie l'identification associée au hash stocké le plus
    proche de `phash` (distance de Hamming <= max_hamming_distance), pas
    seulement une correspondance exacte : deux crops du même objet pris à
    quelques frames d'écart ne produisent presque jamais un dHash identique
    au bit près.
    """

    def __init__(self, data_dir: Path, hash_size: int = 8, max_hamming_distance: int = 6,
                 max_entries: int = 5000) -> None:
        self.hash_size = hash_size
        self.max_hamming_distance = max_hamming_distance
        self.max_entries = max_entries

        self._lock = threading.RLock()
        self._db_path = Path(data_dir) / "identification_cache.json"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # {phash: {"identification": dict, "last_used_ts": float}} — dict Python conserve
        # l'ordre d'insertion, mais on retrie explicitement par last_used_ts pour l'éviction
        # LRU plutôt que de se reposer sur cet ordre (qui suit l'ordre d'AJOUT, pas d'usage).
        self._entries: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # Chargement / écriture
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        if not self._db_path.is_file():
            logger.info("Aucun cache ORACLE existant (%s), démarrage à vide", self._db_path)
            return
        try:
            raw = json.loads(self._db_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Cache ORACLE illisible (%s) : %s — démarrage à vide", self._db_path, exc)
            return
        self._entries = raw.get("entries", {})
        logger.info("Cache ORACLE chargé : %d entrée(s)", len(self._entries))

    def _flush(self) -> None:
        """Écriture atomique de l'état courant (fichier temporaire + os.replace()), même principe que roster/store.py."""
        payload = {"entries": self._entries}
        fd, tmp_path = tempfile.mkstemp(dir=str(self._db_path.parent), prefix=".oracle_cache_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp_path, self._db_path)
        except OSError:
            logger.error("Échec d'écriture du cache ORACLE (%s)", self._db_path)
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------ #
    # API
    # ------------------------------------------------------------------ #

    def lookup(self, phash: str) -> ObjectIdentification | None:
        with self._lock:
            best_hash, best_distance = None, self.max_hamming_distance + 1
            for stored_hash in self._entries:
                distance = hamming_distance(phash, stored_hash)
                if distance < best_distance:
                    best_hash, best_distance = stored_hash, distance

            if best_hash is None or best_distance > self.max_hamming_distance:
                return None

            entry = self._entries[best_hash]
            entry["last_used_ts"] = time.time()
            return ObjectIdentification.from_dict(entry["identification"])

    def store(self, phash: str, identification: ObjectIdentification) -> None:
        with self._lock:
            self._entries[phash] = {
                "identification": identification.to_dict(),
                "last_used_ts": time.time(),
            }
            self._evict_if_needed()
            self._flush()

    def _evict_if_needed(self) -> None:
        if len(self._entries) <= self.max_entries:
            return
        # Purge la moitié la plus anciennement utilisée d'un coup plutôt qu'une entrée à la
        # fois : évite de réécrire le fichier de cache à chaque insertion une fois la borne
        # atteinte, ce qui serait coûteux sur un cache qui reste durablement plein.
        ordered = sorted(self._entries.items(), key=lambda kv: kv[1]["last_used_ts"])
        n_to_remove = len(self._entries) - (self.max_entries // 2)
        for stale_hash, _ in ordered[:n_to_remove]:
            del self._entries[stale_hash]
        logger.info("Cache ORACLE : %d entrée(s) purgée(s) (LRU, borne=%d)", n_to_remove, self.max_entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
