"""Read-only viewer contracts, with small explicit trajectories."""
from __future__ import annotations
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from src.core.result_archive import ResultArchive, ResultError


def fixture(root, values=(0.5, 1.5, 0.7), flagged=1):
    name='sentinel1a_20260501T000000000000Z_POE_fixture';folder=root/name;folder.mkdir()
    rows=[]
    for i,error in enumerate(values):
        rows.append(dict(time_utc=f'2026-05-01T00:0{i}:00+00:00',elapsed_hours_from_tle_epoch=i/60,
            pred_x_itrs_km=7000+error,pred_y_itrs_km=0,pred_z_itrs_km=0,
            ref_x_itrs_km=7000,ref_y_itrs_km=0,ref_z_itrs_km=0,total_error_km=error))
    with (folder/'errors.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (folder/'horizons.csv').write_text('archived,horizons\n')
    (folder/'evaluated_tle.tle').write_text('saved TLE fixture\n')
    horizons=[]
    for threshold in (1,2,5,10):
        index=next((i for i,e in enumerate(values) if e>threshold),None)
        first=None if index is None else index/60
        left=None if index is None else max(0,index-1)/60
        estimate=None if index is None else 0 if index==0 else left+(threshold-values[index-1])/(values[index]-values[index-1])/60
        horizons.append(dict(threshold_km=threshold,right_censored=index is None,crossed_threshold=index is not None,
            first_exceedance_hours=first,bracket_start_hours=left,bracket_end_hours=first,
            horizon_hours_estimate=estimate,observed_duration_hours=(len(values)-1)/60))
    summary=dict(satellite_key='sentinel1a',satellite_name='Sentinel-1A',norad_id=39634,reference_grade='POE',reference_product='AUX_POEORB',
        evaluation_mode='retrospective',tle_epoch_utc='2026-05-01T00:00:00+00:00',evaluation_end_utc='2026-05-01T00:02:00+00:00',
        sample_count=len(values),duration_hours=(len(values)-1)/60,step_seconds=60,
        rms_error_km=math.sqrt(sum(e*e for e in values)/len(values)),mean_error_km=sum(values)/len(values),maximum_error_km=max(values),
        horizons=horizons,references=[],reference_interpolation='Cubic Hermite',output_sha256={
            n:hashlib.sha256((folder/n).read_bytes()).hexdigest() for n in ('errors.csv','horizons.csv','evaluated_tle.tle')})
    (folder/'summary.json').write_text(json.dumps(summary))
    if flagged is not None:
        (root/'study_audit.json').write_text(json.dumps({'cases':{name:{'summary_sha256':hashlib.sha256((folder/'summary.json').read_bytes()).hexdigest(),
            'samples_with_non_nominal_reference_bracket':flagged}}}))
    return folder


class ResultViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_recovery_does_not_erase_first_crossing_and_censoring_is_none(self):
        fixture(self.root)
        a=ResultArchive(self.root);c=a.cases[0];s=a.series(c)
        self.assertEqual(s.errors,(0.5,1.5,0.7))
        self.assertEqual(c.summary['horizons'][0]['first_exceedance_hours'],1/60)
        self.assertIsNone(c.summary['horizons'][1]['first_exceedance_hours'])
        self.assertEqual(len(a.filtered(screened=True)),0)

    def test_initial_exceedance_is_zero(self):
        fixture(self.root,values=(1.5,0.5,0.7),flagged=0)
        a=ResultArchive(self.root);a.series(a.cases[0]);self.assertEqual(a.cases[0].summary['horizons'][0]['first_exceedance_hours'],0)

    def test_unknown_quality_is_not_silently_treated_as_clean(self):
        fixture(self.root,flagged=None);a=ResultArchive(self.root)
        self.assertIsNone(a.cases[0].flagged_samples);self.assertFalse(a.filtered(screened=True))

    def test_changed_summary_cannot_inherit_quality_annotation(self):
        f=fixture(self.root);p=f/'summary.json';p.write_text(p.read_text()+'\n')
        with self.assertRaisesRegex(ResultError,'Audit annotation'):ResultArchive(self.root)

    def test_changed_measurements_and_rehashed_inconsistent_norm_are_rejected(self):
        f=fixture(self.root,flagged=None);p=f/'errors.csv';p.write_text(p.read_text().replace('7000.5','7009.5'))
        a=ResultArchive(self.root)
        with self.assertRaisesRegex(ResultError,'hash mismatch'):a.series(a.cases[0])
        summary=json.loads((f/'summary.json').read_text());summary['output_sha256']['errors.csv']=hashlib.sha256(p.read_bytes()).hexdigest()
        (f/'summary.json').write_text(json.dumps(summary));a=ResultArchive(self.root)
        with self.assertRaisesRegex(ResultError,'position vectors'):a.series(a.cases[0])

    def test_zip_read_and_export_preserve_source_bytes(self):
        folder=fixture(self.root,flagged=None);before={p.name:p.read_bytes() for p in folder.iterdir()}
        path=self.root/'cases.zip'
        with ZipFile(path,'w') as z:
            for p in folder.iterdir():z.write(p,folder.name+'/'+p.name)
        a=ResultArchive(path);target=a.export_case(a.cases[0],self.root/'exports')
        for n,data in before.items():self.assertEqual((target/n).read_bytes(),data);self.assertEqual((folder/n).read_bytes(),data)
        second=a.export_case(a.cases[0],self.root/'exports');self.assertNotEqual(target,second)

    def test_zip_traversal_is_rejected_without_extraction(self):
        path=self.root/'bad.zip'
        with ZipFile(path,'w') as z:z.writestr('../escape/summary.json','{}')
        with self.assertRaisesRegex(ResultError,'Unsafe ZIP'):ResultArchive(path)

    def test_wrong_censored_flag_is_rejected_even_with_consistent_file_hashes(self):
        f=fixture(self.root,flagged=None);p=f/'summary.json';s=json.loads(p.read_text());s['horizons'][0]['right_censored']=True;p.write_text(json.dumps(s))
        a=ResultArchive(self.root)
        with self.assertRaisesRegex(ResultError,'censoring flag'):a.series(a.cases[0])


try:
    from PySide6.QtWidgets import QApplication
    from src.gui.defense_window import DefenseWindow
except ImportError:
    QApplication=DefenseWindow=None


@unittest.skipIf(QApplication is None,'PySide6 and matplotlib required for GUI checks')
class QtViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_filter_clears_stale_plot_and_export_after_excluding_only_case(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root)
            w=DefenseWindow(root);w.show();self.app.processEvents()
            self.assertIsNotNone(w.current_case);self.assertTrue(w.export_button.isEnabled())
            self.assertEqual(w.horizon_table.item(1,1).text(),'Not reached')
            w.screened.setChecked(True);self.app.processEvents()
            self.assertIsNone(w.current_case);self.assertEqual(w.horizon_table.rowCount(),0)
            self.assertFalse(w.export_button.isEnabled());self.assertFalse(w.save_plot.isEnabled())
            self.assertFalse(w.axes.lines);w.close()

    def test_invalid_archive_does_not_replace_current_dataset(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root);w=DefenseWindow(root)
            original=w.archive
            self.assertFalse(w.load_results(root/'missing.zip',show_errors=False))
            self.assertIs(w.archive,original);w.close()


if __name__=='__main__':unittest.main()
