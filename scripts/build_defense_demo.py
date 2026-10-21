"""Package a portable source viewer and existing study without changing inputs."""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core.result_archive import ResultArchive


def build(results_zip: Path, case_audit: Path, output: Path):
    archive = ResultArchive(results_zip)
    with case_audit.open(newline='', encoding='utf-8-sig') as handle:
        rows = list(csv.DictReader(handle))
    notes = {r['case_id']: r for r in rows}
    if len(notes) != len(rows) or set(notes) != {c.case_id for c in archive.cases}:
        raise ValueError('Case audit must contain exactly the cases in the selected result archive.')
    annotations = {}
    for case in archive.cases:
        archive.series(case)
        flagged = int(notes[case.case_id]['samples_with_non_nominal_reference_bracket'])
        if not 0 <= flagged <= case.summary['sample_count']:
            raise ValueError('Invalid quality-flag sample count.')
        annotations[case.case_id] = {'summary_sha256': case.summary_sha256,
                                    'samples_with_non_nominal_reference_bracket': flagged}
    audit = {'schema_version': 1, 'results_zip_sha256': hashlib.sha256(results_zip.read_bytes()).hexdigest(),
             'case_audit_sha256': hashlib.sha256(case_audit.read_bytes()).hexdigest(), 'cases': annotations,
             'quality_screening_rule': 'Exclude whole cases with any used non-NOMINAL Sentinel reference bracket; post-inspection sensitivity analysis.',
             'limitation': 'Audit annotations match supplied case bytes; they are not producer signatures or absolute orbit-accuracy certifications.'}
    prefix = 'CarDiNel_Defense_Viewer/'
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as bundle:
        for name in ('src/__init__.py', 'src/core/__init__.py', 'src/gui/__init__.py',
                     'src/core/result_archive.py', 'src/gui/defense_window.py', 'src/gui/earth_error_view.py', 'src/defense.py',
                     'assets/earth/ne_110m_land.geojson', 'assets/earth/LICENSE.md',
                     'Launch_Defense.cmd', 'DEFENSE_VIEWER.md', 'requirements-defense.txt'):
            bundle.write(ROOT/name, prefix+name)
        bundle.write(results_zip, prefix+'defense_demo/may_results.zip', compress_type=ZIP_STORED)
        bundle.writestr(prefix+'defense_demo/study_audit.json', json.dumps(audit, indent=2)+'\n')
        bundle.write(case_audit, prefix+'defense_demo/case_verification.csv')
        hashes = {n:hashlib.sha256(bundle.read(n)).hexdigest() for n in bundle.namelist()}
        bundle.writestr(prefix+'SHA256SUMS.json', json.dumps(hashes,indent=2)+'\n')
    return {'output': str(output), 'cases':len(annotations),
            'quality_screened_cases':sum(n['samples_with_non_nominal_reference_bracket']==0 for n in annotations.values())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results-zip', type=Path, required=True)
    parser.add_argument('--case-audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.results_zip,args.case_audit,args.output),indent=2))
