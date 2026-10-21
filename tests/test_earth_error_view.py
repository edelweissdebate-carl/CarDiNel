"""Scientific display invariants and playback behavior for the 3D view."""
from pathlib import Path
import tempfile
import unittest

import numpy as np

from src.core.result_archive import ResultArchive
from test_result_viewer import fixture

try:
    from PySide6.QtWidgets import QApplication
    from src.gui.earth_error_view import EarthErrorView, displayed_prediction, visible_above_globe
except ImportError:
    QApplication = EarthErrorView = None


@unittest.skipIf(EarthErrorView is None,'PySide6 and matplotlib required')
class EarthErrorViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name); fixture(root)
        archive = ResultArchive(root); self.case = archive.cases[0]
        self.series = archive.series(self.case)
        self.view = EarthErrorView(); self.view.set_series(self.case,self.series)

    def tearDown(self):
        self.view.close(); self.temp.cleanup()

    def test_magnification_applies_only_to_error_and_preserves_archived_vectors(self):
        reference = np.asarray(self.series.reference_itrs_km)
        prediction = np.asarray(self.series.predicted_itrs_km)
        before = prediction.copy()
        np.testing.assert_allclose(displayed_prediction(reference,prediction,1),prediction,rtol=0,atol=1e-12)
        np.testing.assert_allclose(displayed_prediction(reference,prediction,500)-reference,500*(prediction-reference))
        np.testing.assert_array_equal(prediction,before)
        for i in range(len(self.series.hours)):
            self.view.slider.setValue(i)
            self.assertAlmostEqual(np.linalg.norm(self.view.local_offset),self.series.errors[i],places=9)
        self.view.scale.setCurrentIndex(0)
        np.testing.assert_allclose(self.view.displayed_position,prediction[-1])

    def test_scrubbing_uses_saved_time_and_real_error_even_when_magnified(self):
        self.view.slider.setValue(1)
        self.assertEqual(self.view.frame_index,1)
        self.assertIn(self.series.times_utc[1],self.view.readout.text())
        self.assertIn('1.500000 km',self.view.readout.text())
        self.assertIn('×500',self.view.scale_note.text())
        self.view.jump_to_crossing();self.assertEqual(self.view.slider.value(),1)

    def test_playback_stops_at_last_saved_sample_and_on_clear(self):
        self.view.toggle_play();self.assertTrue(self.view.timer.isActive())
        self.view.speed.setCurrentIndex(2);self.view.advance()
        self.assertEqual(self.view.slider.value(),2);self.assertFalse(self.view.timer.isActive())
        self.view.toggle_play();self.assertEqual(self.view.slider.value(),0)
        self.view.set_series(None,None)
        self.assertFalse(self.view.timer.isActive());self.assertFalse(self.view.play.isEnabled())
        self.assertIsNone(self.view.displayed_position)
        self.assertEqual(len(self.view.ref_trail.get_data_3d()[0]),0)

    def test_camera_rotation_does_not_change_local_error(self):
        self.view.slider.setValue(1);offset=self.view.local_offset.copy()
        self.view.axes.view_init(elev=-30,azim=150);self.view.update_frame()
        np.testing.assert_array_equal(self.view.local_offset,offset)

    def test_globe_hides_far_side_positions_but_keeps_visible_limb(self):
        visible=visible_above_globe(np.array([[7000,0,0],[-7000,0,0],[-1000,7000,0]]),0,0)
        np.testing.assert_array_equal(visible,[True,False,True])

    def test_switching_case_resets_time_and_pauses_playback(self):
        self.view.slider.setValue(1);self.view.toggle_play()
        self.view.set_series(self.case,self.series)
        self.assertEqual(self.view.slider.value(),0);self.assertFalse(self.view.timer.isActive())


if __name__=='__main__':unittest.main()
