"""
panopticon/pulse_track/tests/test_rules.py

Tests unitaires de RuleEngine : un déclencheur par type de règle
("known_person", "unknown_person", "object_class", "track_dwell"), le
filtre par caméra, la plage horaire, le cooldown (anti-spam), et l'absence
de corruption d'état sous accès concurrent (deux threads, même topologie
que pipeline.py::PulseTrackEngine — un thread ARGUS, un thread ROSTER).
"""

import threading
import time
import unittest

from argus.data_types import Detection, DetectionEvent
from roster.data_types import FaceMatch, RosterEvent

from pulse_track.config import PulseTrackConfig, RuleCondition, RuleConfig
from pulse_track.rules import RuleEngine


def _detection_event(camera_id: str, frame_id: int, detections: list[Detection]) -> DetectionEvent:
    now = time.time()
    return DetectionEvent(camera_id=camera_id, frame_id=frame_id, ts_capture=now, ts_detected=now,
                           width=640, height=480, detections=detections)


def _roster_event(camera_id: str, frame_id: int, matches: list[FaceMatch]) -> RosterEvent:
    now = time.time()
    return RosterEvent(camera_id=camera_id, frame_id=frame_id, ts_capture=now, ts_matched=now, matches=matches)


def _config_with_rule(condition: RuleCondition, cooldown_s: float = 60.0, rule_id: str = "r1") -> PulseTrackConfig:
    return PulseTrackConfig(rules=[RuleConfig(rule_id=rule_id, name=f"Règle {rule_id}",
                                               condition=condition, cooldown_s=cooldown_s)])


def _epoch_at_local_hour(hh: int, mm: int) -> float:
    """Construit un epoch pour AUJOURD'HUI à HH:MM en heure locale de la machine de test —
    portable quel que soit le fuseau, car _within_hours() relit ce même epoch via
    time.localtime() (donc le même fuseau que celui utilisé ici pour le construire)."""
    now = time.localtime()
    target = time.struct_time((now.tm_year, now.tm_mon, now.tm_mday, hh, mm, 0, 0, 0, -1))
    return time.mktime(target)


class TestKnownPersonTrigger(unittest.TestCase):
    def test_fires_on_matched_face(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="known_person"))
        engine = RuleEngine(config)
        event = _roster_event("CAM-0", 1, [FaceMatch(matched=True, person_id="p1", name="Alice")])
        triggered = engine.evaluate_roster_event(event)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0].person_name, "Alice")
        self.assertEqual(triggered[0].camera_id, "CAM-0")
        self.assertEqual(triggered[0].frame_id, 1)

    def test_does_not_fire_on_unmatched_face(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="known_person"))
        engine = RuleEngine(config)
        event = _roster_event("CAM-0", 1, [FaceMatch(matched=False)])
        self.assertEqual(engine.evaluate_roster_event(event), [])

    def test_person_names_filter(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="known_person", person_names=["Bob"]))
        engine = RuleEngine(config)
        event = _roster_event("CAM-0", 1, [FaceMatch(matched=True, person_id="p1", name="Alice")])
        self.assertEqual(engine.evaluate_roster_event(event), [])

        event2 = _roster_event("CAM-0", 2, [FaceMatch(matched=True, person_id="p2", name="Bob")])
        self.assertEqual(len(engine.evaluate_roster_event(event2)), 1)


class TestUnknownPersonTrigger(unittest.TestCase):
    def test_fires_on_unmatched_face(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="unknown_person"))
        engine = RuleEngine(config)
        event = _roster_event("CAM-0", 1, [FaceMatch(matched=False)])
        triggered = engine.evaluate_roster_event(event)
        self.assertEqual(len(triggered), 1)
        self.assertIsNone(triggered[0].person_name)

    def test_does_not_fire_on_matched_face(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="unknown_person"))
        engine = RuleEngine(config)
        event = _roster_event("CAM-0", 1, [FaceMatch(matched=True, person_id="p1", name="Alice")])
        self.assertEqual(engine.evaluate_roster_event(event), [])


class TestObjectClassTrigger(unittest.TestCase):
    def test_fires_on_matching_class(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="object_class", object_classes=["car"], min_confidence=0.5))
        engine = RuleEngine(config)
        event = _detection_event("CAM-0", 1, [Detection(0, "car", 0.9, (0, 0, 10, 10), track_id=7)])
        triggered = engine.evaluate_detection_event(event)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0].object_class, "car")
        self.assertEqual(triggered[0].track_id, 7)

    def test_ignores_other_classes(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="object_class", object_classes=["car"]))
        engine = RuleEngine(config)
        event = _detection_event("CAM-0", 1, [Detection(0, "dog", 0.9, (0, 0, 10, 10))])
        self.assertEqual(engine.evaluate_detection_event(event), [])

    def test_confidence_below_threshold_is_ignored(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="object_class", object_classes=["car"], min_confidence=0.8))
        engine = RuleEngine(config)
        event = _detection_event("CAM-0", 1, [Detection(0, "car", 0.5, (0, 0, 10, 10))])
        self.assertEqual(engine.evaluate_detection_event(event), [])


class TestTrackDwellTrigger(unittest.TestCase):
    def test_does_not_fire_before_dwell_seconds(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="track_dwell", object_classes=["person"], dwell_seconds=10.0))
        engine = RuleEngine(config)
        event = _detection_event("CAM-0", 1, [Detection(0, "person", 0.9, (0, 0, 10, 10), track_id=5)])
        self.assertEqual(engine.evaluate_detection_event(event), [])

    def test_fires_after_dwell_seconds(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="track_dwell", object_classes=["person"], dwell_seconds=0.05),
                                    cooldown_s=0.0)
        engine = RuleEngine(config)

        first = _detection_event("CAM-0", 1, [Detection(0, "person", 0.9, (0, 0, 10, 10), track_id=5)])
        self.assertEqual(engine.evaluate_detection_event(first), [])

        time.sleep(0.08)
        second = _detection_event("CAM-0", 2, [Detection(0, "person", 0.9, (0, 0, 10, 10), track_id=5)])
        triggered = engine.evaluate_detection_event(second)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0].track_id, 5)

    def test_different_track_id_resets_dwell(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="track_dwell", object_classes=["person"], dwell_seconds=0.05),
                                    cooldown_s=0.0)
        engine = RuleEngine(config)

        engine.evaluate_detection_event(
            _detection_event("CAM-0", 1, [Detection(0, "person", 0.9, (0, 0, 10, 10), track_id=5)]))
        time.sleep(0.08)
        # Piste 6, PAS 5 : ne doit pas hériter de l'ancienneté de la piste 5.
        triggered = engine.evaluate_detection_event(
            _detection_event("CAM-0", 2, [Detection(0, "person", 0.9, (0, 0, 10, 10), track_id=6)]))
        self.assertEqual(triggered, [])


class TestCameraFilter(unittest.TestCase):
    def test_only_listed_cameras_trigger(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="object_class", object_classes=["car"], camera_ids=["ENTREE"]))
        engine = RuleEngine(config)

        elsewhere = _detection_event("PARKING", 1, [Detection(0, "car", 0.9, (0, 0, 10, 10))])
        self.assertEqual(engine.evaluate_detection_event(elsewhere), [])

        allowed = _detection_event("ENTREE", 2, [Detection(0, "car", 0.9, (0, 0, 10, 10))])
        self.assertEqual(len(engine.evaluate_detection_event(allowed)), 1)

    def test_empty_camera_ids_allows_all(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="object_class", object_classes=["car"], camera_ids=[]))
        engine = RuleEngine(config)
        event = _detection_event("N-IMPORTE-QUELLE-CAM", 1, [Detection(0, "car", 0.9, (0, 0, 10, 10))])
        self.assertEqual(len(engine.evaluate_detection_event(event)), 1)


class TestWithinHours(unittest.TestCase):
    def test_no_hours_means_always_active(self) -> None:
        cond = RuleCondition(trigger="object_class")
        self.assertTrue(RuleEngine._within_hours(cond, time.time()))

    def test_simple_range_inside(self) -> None:
        cond = RuleCondition(trigger="object_class", hours_start="08:00", hours_end="18:00")
        self.assertTrue(RuleEngine._within_hours(cond, _epoch_at_local_hour(12, 0)))

    def test_simple_range_outside(self) -> None:
        cond = RuleCondition(trigger="object_class", hours_start="08:00", hours_end="18:00")
        self.assertFalse(RuleEngine._within_hours(cond, _epoch_at_local_hour(20, 0)))

    def test_midnight_wraparound_inside(self) -> None:
        cond = RuleCondition(trigger="object_class", hours_start="22:00", hours_end="06:00")
        self.assertTrue(RuleEngine._within_hours(cond, _epoch_at_local_hour(23, 30)))
        self.assertTrue(RuleEngine._within_hours(cond, _epoch_at_local_hour(2, 0)))

    def test_midnight_wraparound_outside(self) -> None:
        cond = RuleCondition(trigger="object_class", hours_start="22:00", hours_end="06:00")
        self.assertFalse(RuleEngine._within_hours(cond, _epoch_at_local_hour(12, 0)))


class TestCooldown(unittest.TestCase):
    def test_second_trigger_within_cooldown_is_suppressed(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="object_class", object_classes=["car"]), cooldown_s=60.0)
        engine = RuleEngine(config)

        event = _detection_event("CAM-0", 1, [Detection(0, "car", 0.9, (0, 0, 10, 10))])
        self.assertEqual(len(engine.evaluate_detection_event(event)), 1)
        self.assertEqual(engine.evaluate_detection_event(event), [])  # même contexte, cooldown actif

    def test_trigger_after_cooldown_elapses(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="object_class", object_classes=["car"]), cooldown_s=0.05)
        engine = RuleEngine(config)

        event = _detection_event("CAM-0", 1, [Detection(0, "car", 0.9, (0, 0, 10, 10))])
        self.assertEqual(len(engine.evaluate_detection_event(event)), 1)

        time.sleep(0.08)
        self.assertEqual(len(engine.evaluate_detection_event(event)), 1)

    def test_different_cameras_have_independent_cooldowns(self) -> None:
        config = _config_with_rule(RuleCondition(trigger="object_class", object_classes=["car"]), cooldown_s=60.0)
        engine = RuleEngine(config)

        event_a = _detection_event("CAM-A", 1, [Detection(0, "car", 0.9, (0, 0, 10, 10))])
        event_b = _detection_event("CAM-B", 1, [Detection(0, "car", 0.9, (0, 0, 10, 10))])
        self.assertEqual(len(engine.evaluate_detection_event(event_a)), 1)
        self.assertEqual(len(engine.evaluate_detection_event(event_b)), 1)


class TestDisabledRule(unittest.TestCase):
    def test_disabled_rule_never_fires(self) -> None:
        condition = RuleCondition(trigger="object_class", object_classes=["car"])
        config = PulseTrackConfig(rules=[RuleConfig(rule_id="r1", name="R1", condition=condition, enabled=False)])
        engine = RuleEngine(config)
        event = _detection_event("CAM-0", 1, [Detection(0, "car", 0.9, (0, 0, 10, 10))])
        self.assertEqual(engine.evaluate_detection_event(event), [])


class TestConcurrentEvaluation(unittest.TestCase):
    def test_two_threads_evaluating_concurrently_do_not_crash(self) -> None:
        """Reproduit la topologie de pipeline.py::PulseTrackEngine (thread ARGUS + thread
        ROSTER partageant la même instance de RuleEngine) : garde-fou contre une régression
        du verrou interne de RuleEngine (cf. note THREAD-SAFETY dans rules.py)."""
        config = PulseTrackConfig(rules=[
            RuleConfig(rule_id="r-argus", name="R-ARGUS",
                       condition=RuleCondition(trigger="object_class", object_classes=["car"]), cooldown_s=0.0),
            RuleConfig(rule_id="r-roster", name="R-ROSTER",
                       condition=RuleCondition(trigger="known_person"), cooldown_s=0.0),
        ])
        engine = RuleEngine(config)
        errors: list[Exception] = []

        def hammer_argus() -> None:
            try:
                for i in range(200):
                    engine.evaluate_detection_event(
                        _detection_event("CAM-0", i, [Detection(0, "car", 0.9, (0, 0, 10, 10), track_id=i)]))
            except Exception as exc:  # noqa: BLE001 — capturé pour assertion, pas pour masquer
                errors.append(exc)

        def hammer_roster() -> None:
            try:
                for i in range(200):
                    engine.evaluate_roster_event(
                        _roster_event("CAM-0", i, [FaceMatch(matched=True, person_id="p1", name="Alice")]))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=hammer_argus)
        t2 = threading.Thread(target=hammer_roster)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()