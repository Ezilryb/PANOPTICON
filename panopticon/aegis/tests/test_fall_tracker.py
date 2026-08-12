"""
panopticon/aegis/tests/test_fall_tracker.py

Tests unitaires de FallStateTracker : aucune confirmation avant
confirm_seconds ; confirmation après confirm_seconds de posture "lying" +
immobilité soutenues ; confirmation plus rapide (fast_confirm_seconds) si
une chute verticale rapide a été observée ; un retour à "upright" avant
confirmation réinitialise l'accumulation ; une fois confirmée, un bref
retour à "upright" plus court que recovery_confirm_seconds NE clôture PAS
l'alerte ; un retour à "upright" soutenu clôture avec la bonne durée et
raison ; "uncertain" ne modifie aucun chronomètre ; un mouvement soutenu
retarde la confirmation jusqu'à l'immobilité ; une piste perdue pendant une
alerte active la clôture en "track_lost" ; une piste perdue jamais
confirmée ne produit aucun évènement ; accès concurrent sans corruption
d'état (même topologie que pulse_track/tests/test_rules.py::TestConcurrentEvaluation).
"""

import threading
import time
import unittest

from aegis.config import FallDetectionConfig
from aegis.data_types import PostureResult
from aegis.fall_tracker import FallStateTracker

_LYING = PostureResult(posture="lying", confidence=0.9, aspect_ratio=1.5)
_UPRIGHT = PostureResult(posture="upright", confidence=0.9, aspect_ratio=0.4)
_UNCERTAIN = PostureResult(posture="uncertain", confidence=0.4, aspect_ratio=1.0)


def _config(**overrides) -> FallDetectionConfig:
    defaults = dict(
        confirm_seconds=10.0, fast_confirm_seconds=4.0, recovery_confirm_seconds=5.0,
        track_lost_after_s=20.0, min_detection_confidence=0.4,
        motion_window_s=30.0, fall_detection_window_s=2.0, fall_min_vertical_px=60.0,
        fall_trigger_grace_s=6.0, max_movement_px=15.0,
    )
    defaults.update(overrides)
    return FallDetectionConfig(**defaults)


class TestNoFastFallConfirmation(unittest.TestCase):
    """Cas par défaut : aucune chute rapide observée, la confirmation suit `confirm_seconds`."""

    def setUp(self) -> None:
        self.tracker = FallStateTracker(_config())
        self.t0 = 1_000_000.0

    def test_no_event_before_confirm_seconds(self) -> None:
        ev = self.tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=self.t0)
        self.assertIsNone(ev)
        ev = self.tracker.update("CAM-0", 1, 2, _LYING, cx=101, cy=101, now=self.t0 + 5.0)
        self.assertIsNone(ev)  # 5s < confirm_seconds=10s

    def test_confirms_after_confirm_seconds_when_still(self) -> None:
        self.tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=self.t0)
        self.tracker.update("CAM-0", 1, 2, _LYING, cx=101, cy=101, now=self.t0 + 5.0)
        ev = self.tracker.update("CAM-0", 1, 3, _LYING, cx=101, cy=102, now=self.t0 + 10.5)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, "fall_confirmed")
        self.assertEqual(ev.track_id, 1)
        self.assertFalse(ev.fast_fall_observed)
        self.assertAlmostEqual(ev.fall_started_at, self.t0, delta=0.01)

    def test_never_confirms_twice_for_same_episode(self) -> None:
        self.tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=self.t0)
        first = self.tracker.update("CAM-0", 1, 2, _LYING, cx=100, cy=100, now=self.t0 + 10.5)
        self.assertIsNotNone(first)
        second = self.tracker.update("CAM-0", 1, 3, _LYING, cx=100, cy=100, now=self.t0 + 15.0)
        self.assertIsNone(second)


class TestFastFallConfirmation(unittest.TestCase):
    """Une chute verticale rapide raccourcit le délai de confirmation à `fast_confirm_seconds`."""

    def setUp(self) -> None:
        self.tracker = FallStateTracker(_config())
        self.t0 = 2_000_000.0

    def test_rapid_vertical_drop_shortens_confirmation(self) -> None:
        # cy passe de 100 à 200 (drop=100 >= fall_min_vertical_px=60) en 0.3s, dans
        # fall_detection_window_s=2.0s : qualifie une "chute rapide".
        self.tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=self.t0)
        self.tracker.update("CAM-0", 1, 2, _LYING, cx=100, cy=200, now=self.t0 + 0.3)
        # Toujours avant confirm_seconds=10s mais après fast_confirm_seconds=4s, et immobile depuis.
        ev = self.tracker.update("CAM-0", 1, 3, _LYING, cx=101, cy=201, now=self.t0 + 4.5)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, "fall_confirmed")
        self.assertTrue(ev.fast_fall_observed)

    def test_slow_descent_does_not_trigger_fast_path(self) -> None:
        # Même déplacement total, mais étalé sur une durée qui dépasse fall_detection_window_s :
        # ne doit jamais qualifier de "chute rapide" (mouvement volontaire lent, ex: s'asseoir).
        self.tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=self.t0)
        self.tracker.update("CAM-0", 1, 2, _LYING, cx=100, cy=200, now=self.t0 + 5.0)
        ev = self.tracker.update("CAM-0", 1, 3, _LYING, cx=100, cy=200, now=self.t0 + 9.0)
        self.assertIsNone(ev)  # 9s < confirm_seconds=10s, pas de chemin rapide disponible

    def test_fast_fall_trigger_expires_after_grace_period(self) -> None:
        self.tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=self.t0)
        self.tracker.update("CAM-0", 1, 2, _LYING, cx=100, cy=200, now=self.t0 + 0.3)
        # fall_trigger_grace_s=6s : au-delà, le chemin rapide n'est plus disponible.
        ev = self.tracker.update("CAM-0", 1, 3, _LYING, cx=100, cy=200, now=self.t0 + 8.0)
        self.assertIsNone(ev)  # 8s < confirm_seconds=10s, et la chute rapide n'est plus "récente"


class TestUprightResetsUnconfirmedStreak(unittest.TestCase):
    def test_brief_upright_before_confirmation_cancels_streak(self) -> None:
        tracker = FallStateTracker(_config())
        t0 = 3_000_000.0
        tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=t0)
        tracker.update("CAM-0", 1, 2, _LYING, cx=100, cy=100, now=t0 + 8.0)
        tracker.update("CAM-0", 1, 3, _UPRIGHT, cx=100, cy=100, now=t0 + 8.5)  # se relève avant confirmation
        tracker.update("CAM-0", 1, 4, _LYING, cx=100, cy=100, now=t0 + 9.0)    # rechute : chronomètre reparti de zéro
        ev = tracker.update("CAM-0", 1, 5, _LYING, cx=100, cy=100, now=t0 + 15.0)  # 6s depuis la rechute < 10s
        self.assertIsNone(ev)


class TestUncertainPostureIsInert(unittest.TestCase):
    def test_uncertain_does_not_reset_lying_streak(self) -> None:
        tracker = FallStateTracker(_config())
        t0 = 4_000_000.0
        tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=t0)
        tracker.update("CAM-0", 1, 2, _UNCERTAIN, cx=100, cy=101, now=t0 + 5.0)  # ne doit rien réinitialiser
        ev = tracker.update("CAM-0", 1, 3, _LYING, cx=100, cy=101, now=t0 + 10.5)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, "fall_confirmed")
        # fall_started_at doit toujours remonter au tout premier "lying" (t0), pas à la reprise après "uncertain".
        self.assertAlmostEqual(ev.fall_started_at, t0, delta=0.01)

    def test_uncertain_does_not_end_confirmed_alert(self) -> None:
        tracker = FallStateTracker(_config())
        t0 = 4_500_000.0
        tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=t0)
        confirmed = tracker.update("CAM-0", 1, 2, _LYING, cx=100, cy=100, now=t0 + 10.5)
        self.assertIsNotNone(confirmed)
        ev = tracker.update("CAM-0", 1, 3, _UNCERTAIN, cx=100, cy=100, now=t0 + 20.0)
        self.assertIsNone(ev)
        self.assertEqual(tracker.active_alert_count(), 1)


class TestMovementDelaysConfirmation(unittest.TestCase):
    def test_sustained_movement_blocks_confirmation_until_still(self) -> None:
        tracker = FallStateTracker(_config(max_movement_px=15.0))
        t0 = 5_000_000.0
        tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=t0)
        # Continue de se déplacer nettement (ex: rampe) bien au-delà de confirm_seconds=10s.
        for i in range(1, 20):
            ev = tracker.update("CAM-0", 1, 1 + i, _LYING, cx=100 + i * 20, cy=100, now=t0 + i * 0.6)
        self.assertIsNone(ev, "un mouvement soutenu ne doit jamais confirmer, même après confirm_seconds")

    def test_confirms_once_movement_stops(self) -> None:
        tracker = FallStateTracker(_config(max_movement_px=15.0))
        t0 = 5_500_000.0
        tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=t0)
        tracker.update("CAM-0", 1, 2, _LYING, cx=300, cy=100, now=t0 + 1.0)  # bouge encore juste après
        # S'immobilise ensuite pour le reste de la fenêtre de confirmation.
        tracker.update("CAM-0", 1, 3, _LYING, cx=301, cy=101, now=t0 + 6.0)
        ev = tracker.update("CAM-0", 1, 4, _LYING, cx=301, cy=101, now=t0 + 11.5)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, "fall_confirmed")


class TestRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = FallStateTracker(_config())
        self.t0 = 6_000_000.0
        self.tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=self.t0)
        confirmed = self.tracker.update("CAM-0", 1, 2, _LYING, cx=100, cy=100, now=self.t0 + 10.5)
        self.assertIsNotNone(confirmed)

    def test_brief_upright_shorter_than_recovery_does_not_end_alert(self) -> None:
        ev = self.tracker.update("CAM-0", 1, 3, _UPRIGHT, cx=100, cy=100, now=self.t0 + 12.0)  # 1.5s < recovery=5s
        self.assertIsNone(ev)
        self.assertEqual(self.tracker.active_alert_count(), 1)

    def test_relapse_into_lying_resets_recovery_streak(self) -> None:
        self.tracker.update("CAM-0", 1, 3, _UPRIGHT, cx=100, cy=100, now=self.t0 + 12.0)
        self.tracker.update("CAM-0", 1, 4, _LYING, cx=100, cy=100, now=self.t0 + 14.0)  # rechute avant recovery
        ev = self.tracker.update("CAM-0", 1, 5, _UPRIGHT, cx=100, cy=100, now=self.t0 + 17.0)  # 3s depuis la reprise < 5s
        self.assertIsNone(ev)
        self.assertEqual(self.tracker.active_alert_count(), 1)

    def test_sustained_upright_ends_alert_with_correct_reason_and_duration(self) -> None:
        ev = self.tracker.update("CAM-0", 1, 3, _UPRIGHT, cx=100, cy=100, now=self.t0 + 12.0)
        self.assertIsNone(ev)
        ev = self.tracker.update("CAM-0", 1, 4, _UPRIGHT, cx=100, cy=100, now=self.t0 + 17.5)  # 5.5s >= recovery=5s
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, "fall_ended")
        self.assertEqual(ev.end_reason, "posture_recovered")
        self.assertAlmostEqual(ev.duration_s, 17.5, delta=0.05)
        self.assertEqual(self.tracker.active_alert_count(), 0)


class TestPruneStale(unittest.TestCase):
    def test_track_lost_while_confirmed_ends_alert(self) -> None:
        tracker = FallStateTracker(_config(track_lost_after_s=6.0))
        t0 = 7_000_000.0
        tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=t0)
        confirmed = tracker.update("CAM-0", 1, 2, _LYING, cx=100, cy=100, now=t0 + 10.5)
        self.assertIsNotNone(confirmed)

        events = tracker.prune_stale("CAM-0", seen_track_ids=set(), now=t0 + 10.5)
        self.assertEqual(events, [])  # pas encore assez longtemps disparue

        events = tracker.prune_stale("CAM-0", seen_track_ids=set(), now=t0 + 17.0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "fall_ended")
        self.assertEqual(events[0].end_reason, "track_lost")
        self.assertEqual(tracker.active_alert_count(), 0)

    def test_track_lost_never_confirmed_produces_no_event(self) -> None:
        tracker = FallStateTracker(_config(track_lost_after_s=6.0))
        t0 = 7_500_000.0
        tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=t0)  # jamais confirmée (durée trop courte)
        events = tracker.prune_stale("CAM-0", seen_track_ids=set(), now=t0 + 7.0)
        self.assertEqual(events, [])

    def test_seen_track_is_never_pruned(self) -> None:
        tracker = FallStateTracker(_config(track_lost_after_s=6.0))
        t0 = 7_800_000.0
        tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100, now=t0)
        confirmed = tracker.update("CAM-0", 1, 2, _LYING, cx=100, cy=100, now=t0 + 10.5)
        self.assertIsNotNone(confirmed)
        events = tracker.prune_stale("CAM-0", seen_track_ids={1}, now=t0 + 100.0)
        self.assertEqual(events, [])
        self.assertEqual(tracker.active_alert_count(), 1)

    def test_different_camera_is_not_affected(self) -> None:
        tracker = FallStateTracker(_config(track_lost_after_s=6.0))
        t0 = 7_900_000.0
        tracker.update("CAM-A", 1, 1, _LYING, cx=100, cy=100, now=t0)
        tracker.update("CAM-A", 1, 2, _LYING, cx=100, cy=100, now=t0 + 10.5)
        events = tracker.prune_stale("CAM-B", seen_track_ids=set(), now=t0 + 100.0)
        self.assertEqual(events, [])
        self.assertEqual(tracker.active_alert_count(), 1)


class TestDefaultNowUsesRealTime(unittest.TestCase):
    def test_update_without_explicit_now_uses_current_time(self) -> None:
        tracker = FallStateTracker(_config(confirm_seconds=0.05, fast_confirm_seconds=0.02,
                                            motion_window_s=1.0, fall_detection_window_s=0.2, max_movement_px=5.0))
        tracker.update("CAM-0", 1, 1, _LYING, cx=100, cy=100)  # now=None -> time.time()
        time.sleep(0.1)
        ev = tracker.update("CAM-0", 1, 2, _LYING, cx=100, cy=100)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, "fall_confirmed")


class TestConcurrentAccess(unittest.TestCase):
    def test_two_threads_updating_different_tracks_do_not_crash(self) -> None:
        """Garde-fou contre une régression du verrou interne — même esprit que
        pulse_track/tests/test_rules.py::TestConcurrentEvaluation."""
        tracker = FallStateTracker(_config(confirm_seconds=0.05, fast_confirm_seconds=0.02,
                                            motion_window_s=2.0, fall_detection_window_s=0.2, max_movement_px=5.0))
        errors: list[Exception] = []

        def hammer(track_id: int) -> None:
            try:
                base = time.time()
                for i in range(100):
                    tracker.update("CAM-0", track_id, i, _LYING, cx=100, cy=100, now=base + i * 0.001)
                    tracker.prune_stale("CAM-0", seen_track_ids={track_id}, now=base + i * 0.001)
            except Exception as exc:  # noqa: BLE001 — capturé pour assertion, pas pour masquer
                errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(tid,)) for tid in (1, 2, 3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
