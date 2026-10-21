"""Read-only access to saved historical results; no orbital or network imports."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import statistics
from zipfile import ZipFile

SATELLITES = {
    'sentinel1a': ('Sentinel-1A', 39634, 'POE'),
    'sentinel1c': ('Sentinel-1C', 62261, 'POE'),
    'swarma': ('Swarm-A', 39452, 'PSO'),
    'swarmb': ('Swarm-B', 39451, 'PSO'),
    'swarmc': ('Swarm-C', 39453, 'PSO'),
}


class ResultError(ValueError):
    """An archive cannot safely or consistently be displayed."""


def require(condition, message):
    if not condition:
        raise ResultError(message)


def finite(value):
    result = float(value)
    require(math.isfinite(result), 'Non-finite measurement in saved results.')
    return result


@dataclass(frozen=True)
class Case:
    case_id: str
    prefix: str
    summary: dict
    summary_sha256: str
    flagged_samples: int | None

    @property
    def satellite(self):
        return self.summary['satellite_key']

    @property
    def date(self):
        return self.summary['tle_epoch_utc'][:10]

    @property
    def quality_label(self):
        if self.flagged_samples is None:
            return 'Quality annotation unavailable'
        if self.flagged_samples:
            return f'{self.flagged_samples} sample brackets use non-NOMINAL reference states'
        return 'No non-NOMINAL brackets in supplied audit'


@dataclass(frozen=True)
class Series:
    hours: tuple[float, ...]
    errors: tuple[float, ...]
    tle: str


class ResultArchive:
    """Accept an extracted result root or ZIP without extracting ZIP members.

    An optional study_audit.json beside the archive associates reference-quality
    annotations with exact summary bytes. It is an audit annotation, not a
    producer signature or an external certification of orbital accuracy.
    """

    def __init__(self, path: str | Path, audit_path: str | Path | None = None):
        self.path = Path(path).resolve()
        require(self.path.exists(), f'Result path does not exist: {self.path}')
        self.zipped = self.path.is_file()
        if self.zipped:
            require(self.path.suffix.lower() == '.zip', 'Select a results ZIP or an extracted results folder.')
            with ZipFile(self.path) as archive:
                names = [n for n in archive.namelist() if not n.endswith('/')]
                require(len(names) == len(set(names)), 'Duplicate ZIP member names.')
                require(all(not PurePosixPath(n).is_absolute() and '..' not in PurePosixPath(n).parts and '\\' not in n for n in names), 'Unsafe ZIP member path.')
                require(sum(i.file_size for i in archive.infolist()) <= 2_000_000_000, 'Results ZIP is too large for this viewer.')
                self.names = set(names)
        else:
            self.names = {p.relative_to(self.path).as_posix() for p in self.path.rglob('summary.json')}
        annotation = Path(audit_path) if audit_path else (self.path.parent if self.zipped else self.path) / 'study_audit.json'
        self.audit = json.loads(annotation.read_text(encoding='utf-8')) if annotation.exists() else {}
        audit_cases = self.audit.get('cases', {})
        cases = []
        for name in sorted(self.names):
            if PurePosixPath(name).name != 'summary.json':
                continue
            prefix = str(PurePosixPath(name).parent)
            case_id = PurePosixPath(prefix).name
            require(bool(re.fullmatch(r'[A-Za-z0-9_.-]+', case_id)), 'Invalid case directory name.')
            raw = self.read(name)
            summary = json.loads(raw)
            key = summary.get('satellite_key')
            # This viewer deliberately presents the five-satellite historical study.
            if key not in SATELLITES or summary.get('evaluation_mode') != 'retrospective':
                continue
            require(summary.get('norad_id') == SATELLITES[key][1], 'Saved satellite identity disagrees with registry.')
            require(summary.get('reference_grade') == SATELLITES[key][2], 'Saved reference grade disagrees with satellite.')
            require(summary.get('sample_count', 0) >= 2, 'A result case needs at least two samples.')
            for metric in ('rms_error_km', 'mean_error_km', 'maximum_error_km'):
                require(finite(summary[metric]) >= 0, 'Negative error metric.')
            digest = hashlib.sha256(raw).hexdigest()
            note = audit_cases.get(case_id)
            flagged = None
            if note:
                require(note.get('summary_sha256') == digest, f'Audit annotation does not match saved case: {case_id}')
                flagged = note['samples_with_non_nominal_reference_bracket']
                require(type(flagged) is int and 0 <= flagged <= summary['sample_count'], 'Invalid reference-quality annotation.')
            cases.append(Case(case_id, prefix, summary, digest, flagged))
        require(bool(cases), 'No supported historical result cases found. Select the folder containing the case directories, or their ZIP.')
        identities = [(c.satellite, c.summary['tle_epoch_utc']) for c in cases]
        require(len(set(identities)) == len(identities), 'Duplicate satellite/epoch cases; select one study batch.')
        self.cases = tuple(sorted(cases, key=lambda c: (list(SATELLITES).index(c.satellite), c.summary['tle_epoch_utc'])))

    def read(self, name: str) -> bytes:
        require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts, 'Unsafe result path.')
        if self.zipped:
            with ZipFile(self.path) as archive:
                info = archive.getinfo(name)
                require(info.file_size <= 25_000_000, 'Result member exceeds viewer size limit.')
                return archive.read(name)
        path = (self.path / name).resolve()
        require(path.is_relative_to(self.path), 'Result symlink points outside selected folder.')
        require(path.stat().st_size <= 25_000_000, 'Result file exceeds viewer size limit.')
        return path.read_bytes()

    def checked_file(self, case: Case, name: str) -> bytes:
        raw = self.read(case.prefix + '/' + name)
        expected = case.summary.get('output_sha256', {}).get(name)
        require(expected and hashlib.sha256(raw).hexdigest() == expected, f'Saved file hash mismatch: {case.case_id}/{name}')
        return raw

    def series(self, case: Case) -> Series:
        raw = self.checked_file(case, 'errors.csv')
        self.checked_file(case, 'horizons.csv')
        tle = self.checked_file(case, 'evaluated_tle.tle').decode('utf-8-sig')
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        require(len(rows) == case.summary['sample_count'], 'Saved sample count disagrees with summary.')
        hours = tuple(finite(r['elapsed_hours_from_tle_epoch']) for r in rows)
        errors = tuple(finite(r['total_error_km']) for r in rows)
        require(hours[0] == 0 and all(b > a for a, b in zip(hours, hours[1:])), 'Invalid elapsed-time grid.')
        require(abs(hours[-1] - finite(case.summary['duration_hours'])) < 1e-8, 'Saved time window disagrees with summary.')
        origin = datetime.fromisoformat(case.summary['tle_epoch_utc'])
        require(origin.tzinfo is not None, 'TLE epoch must contain its UTC offset.')
        for hour, error, row in zip(hours, errors, rows):
            stamp = datetime.fromisoformat(row['time_utc'])
            require(stamp.tzinfo is not None and abs((stamp-origin).total_seconds()/3600-hour) < 1e-8, 'Saved UTC/elapsed timestamps disagree.')
            norm = math.sqrt(sum((finite(row[f'pred_{a}_itrs_km'])-finite(row[f'ref_{a}_itrs_km']))**2 for a in 'xyz'))
            require(error >= 0 and abs(norm-error) < 1e-7, 'Saved error disagrees with position vectors.')
        rms = math.sqrt(statistics.fmean(e*e for e in errors))
        require(abs(rms-case.summary['rms_error_km']) < 1e-7, 'Saved RMS disagrees with summary.')
        for horizon in case.summary['horizons']:
            threshold = finite(horizon['threshold_km'])
            first = next((h for h, e in zip(hours, errors) if e > threshold), None)
            recorded = horizon['first_exceedance_hours']
            require((first is None) == (recorded is None), 'Saved crossing/censoring disagrees with error series.')
            if first is not None:
                require(abs(first-finite(recorded)) < 1e-8, 'Saved threshold crossing disagrees with error series.')
            require(bool(horizon['right_censored']) == (first is None), 'Saved censoring flag disagrees with error series.')
        return Series(hours, errors, tle)

    def filtered(self, satellite=None, screened=False):
        return tuple(c for c in self.cases if (not satellite or c.satellite == satellite) and (not screened or c.flagged_samples == 0))

    def overview(self, screened=False):
        rows = []
        for key, (name, _, _) in SATELLITES.items():
            cases = self.filtered(key, screened)
            if not cases:
                continue
            first = [next(h for h in c.summary['horizons'] if h['threshold_km'] == 1)['first_exceedance_hours'] for c in cases]
            crossed = [h for h in first if h is not None]
            rows.append((name, len(cases), statistics.median(c.summary['rms_error_km'] for c in cases),
                         statistics.median(crossed) if len(crossed) == len(cases) else None,
                         sum(c.flagged_samples is not None and c.flagged_samples > 0 for c in cases)))
        return rows

    def export_case(self, case: Case, destination: str | Path) -> Path:
        # Validate before publishing an export, and never overwrite source cases.
        self.series(case)
        root = Path(destination).resolve()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        target = root / f'{case.case_id}_export_{stamp}'
        target.mkdir()
        (target/'summary.json').write_bytes(self.read(case.prefix+'/summary.json'))
        for name in ('errors.csv', 'horizons.csv', 'evaluated_tle.tle'):
            (target/name).write_bytes(self.checked_file(case, name))
        (target/'viewer_note.json').write_text(json.dumps({
            'case_id': case.case_id, 'summary_sha256': case.summary_sha256,
            'samples_with_non_nominal_reference_bracket': case.flagged_samples,
            'interpretation': 'Historical reconstruction; first sampled exceedance is not permanent expiry; quality annotation is not an accuracy certification.',
        }, indent=2)+'\n', encoding='utf-8')
        return target
