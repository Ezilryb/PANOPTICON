"""
panopticon/spectra/tests/test_screen_state.py

Tests unitaires de ScreenStateMonitor : une zone claire est jugée "allumée",
une zone sombre "éteinte" ; deux frames identiques donnent un score de
mouvement nul (zone statique), deux frames très différentes donnent un score
de mouvement élevé (zone dynamique) ; une caméra sans zone configurée ne
produit aucun résultat (et n'est même pas traitée) ; une zone hors des
limites de l'image est ignorée sans lever d'exception.
"""

import unittest

import numpy as np

from spectra.config import ScreenRegionConfig
from spectra.screen_state import ScreenStateMonitor


def _frame_with_bright_patch(shape=(200, 200, 3), patch_bbox=(50, 50, 150, 150), patch_value=200, bg_value=20):
    image = np.full(shape, bg_value, dtype=np.uint8)
    x1, y1, x2, y2 = patch_bbox
    image[y1:y2, x1:x2] = patch_value
    return image


class TestScreenStateMonitor(unittest.TestCase):
    def test_no_regions_configured_returns_empty(self) -> None:
        monitor = ScreenStateMonitor([])
        self.assertFalse(monitor.has_regions_for("CAM-0"))
        result = monitor.update("CAM-0", _frame_with_bright_patch())
        self.assertEqual(result, [])

    def test_bright_region_is_on(self) -> None:
        regions = [ScreenRegionConfig(camera_id="CAM-0", region_name="ecran", bbox=(50, 50, 150, 150),
                                       on_brightness_threshold=60.0)]
        monitor = ScreenStateMonitor(regions)
        self.assertTrue(monitor.has_regions_for("CAM-0"))

        result = monitor.update("CAM-0", _frame_with_bright_patch(patch_value=200, bg_value=20))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].region_name, "ecran")
        self.assertTrue(result[0].is_on)

    def test_dark_region_is_off(self) -> None:
        regions = [ScreenRegionConfig(camera_id="CAM-0", region_name="ecran", bbox=(50, 50, 150, 150),
                                       on_brightness_threshold=60.0)]
        monitor = ScreenStateMonitor(regions)
        result = monitor.update("CAM-0", _frame_with_bright_patch(patch_value=10, bg_value=10))
        self.assertFalse(result[0].is_on)

    def test_identical_frames_are_static(self) -> None:
        regions = [ScreenRegionConfig(camera_id="CAM-0", region_name="ecran", bbox=(50, 50, 150, 150),
                                       motion_threshold=6.0)]
        monitor = ScreenStateMonitor(regions)
        frame = _frame_with_bright_patch()
        monitor.update("CAM-0", frame)  # première observation : rien à comparer
        result = monitor.update("CAM-0", frame)  # frame strictement identique
        self.assertTrue(result[0].is_static)
        self.assertEqual(result[0].motion_score, 0.0)

    def test_changing_frames_are_dynamic(self) -> None:
        regions = [ScreenRegionConfig(camera_id="CAM-0", region_name="ecran", bbox=(50, 50, 150, 150),
                                       motion_threshold=6.0)]
        monitor = ScreenStateMonitor(regions)
        monitor.update("CAM-0", _frame_with_bright_patch(patch_value=200))
        result = monitor.update("CAM-0", _frame_with_bright_patch(patch_value=30))
        self.assertFalse(result[0].is_static)
        self.assertGreater(result[0].motion_score, regions[0].motion_threshold)

    def test_region_out_of_bounds_is_ignored_not_crashed(self) -> None:
        regions = [ScreenRegionConfig(camera_id="CAM-0", region_name="hors-champ", bbox=(500, 500, 600, 600))]
        monitor = ScreenStateMonitor(regions)
        result = monitor.update("CAM-0", _frame_with_bright_patch(shape=(200, 200, 3)))
        self.assertEqual(result, [])  # ignorée, mais pas d'exception

    def test_regions_are_isolated_per_camera(self) -> None:
        regions = [
            ScreenRegionConfig(camera_id="CAM-0", region_name="ecran-0", bbox=(50, 50, 150, 150)),
            ScreenRegionConfig(camera_id="CAM-1", region_name="ecran-1", bbox=(0, 0, 50, 50)),
        ]
        monitor = ScreenStateMonitor(regions)
        result_cam0 = monitor.update("CAM-0", _frame_with_bright_patch())
        result_cam1 = monitor.update("CAM-1", _frame_with_bright_patch())
        self.assertEqual([r.region_name for r in result_cam0], ["ecran-0"])
        self.assertEqual([r.region_name for r in result_cam1], ["ecran-1"])

    def test_unconfigured_camera_returns_empty(self) -> None:
        regions = [ScreenRegionConfig(camera_id="CAM-0", region_name="ecran", bbox=(50, 50, 150, 150))]
        monitor = ScreenStateMonitor(regions)
        self.assertFalse(monitor.has_regions_for("CAM-OTHER"))
        self.assertEqual(monitor.update("CAM-OTHER", _frame_with_bright_patch()), [])


if __name__ == "__main__":
    unittest.main()