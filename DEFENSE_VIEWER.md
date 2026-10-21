# CarDiNel defense viewer

Extract the entire download first. On Windows, double-click **Launch_Defense.cmd**.
The launcher checks the existing ORBIT-TRUST environment on your Desktop as well
as a local `.venv`, then the usual `py` and `python` commands. It needs Python,
PySide6 and matplotlib. It is a source distribution, not a standalone `.exe`.

The prepared defense download includes the 155 saved May 2026 daily cases and
their matching source-audit quality annotations. It opens them automatically.
The source repository does not contain that research dataset.

If your environment needs the two GUI dependencies, install them once while
online, before the defense:

```powershell
& "C:\Users\USER\OneDrive\Desktop\orbit-trust\.venv\Scripts\python.exe" -m pip install -r requirements-defense.txt
```

To launch explicitly from the extracted folder:

```powershell
& "C:\Users\USER\OneDrive\Desktop\orbit-trust\.venv\Scripts\python.exe" -m src.defense
```

For a different Python location, set `CARDINEL_PYTHON` to its executable before
running the launcher. To open another saved historical batch, use the viewer's
ZIP/folder buttons or `python -m src.defense --results PATH`. A result ZIP is
read without extraction. A `study_audit.json` beside it supplies matching
quality annotations; absence of this file means quality is **unknown**, not
unflagged. A changed summary cannot inherit a previous audit annotation.

## Demonstration

1. Start with Sentinel-1C and a daily TLE epoch. Explain that the graph is a
   saved historical reconstruction, with elapsed time measured from TLE epoch.
2. Show the error curve, threshold lines and first saved exceedance. The table
   separates the saved crossing, neighboring-sample bracket, linear estimate
   and right censoring. Errors can later return below a threshold.
3. Switch to Swarm-B to show the same workflow with PSO references.
4. Select Sentinel-1A, May 19, to show a reference-quality flag. Open
   **Inputs & provenance** for the TLE, reference filenames and hashes.
5. Enable **Exclude flagged / unknown reference quality**. The bundled study
   changes from 155 to 139 cases; the overview recomputes medians of the
   included daily summaries. This is a post-inspection sensitivity analysis.
6. Export the selected case or save its plot. Exports use new case directories.

The viewer performs no network requests and does not import the propagator,
Astropy or the legacy research pipeline. It checks each displayed case's saved
hashes, time grid, error norms, RMS and sampled threshold outcomes. Opening an
archive does not revalidate the absolute accuracy of producer reference files.
The separate prospective capture remains pending until reference validation.

For the existing application, `python -m src.main` retains its legacy tabs and
adds a **Defense Results** button that opens this viewer in its own window.
Use `python -m src.defense` for the focused offline demonstration.

## Build the prepared source download

```powershell
python scripts/build_defense_demo.py --results-zip PATH_TO_ANALYSIS_ZIP --case-audit PATH_TO_CASE_VERIFICATION_CSV --output CarDiNel_Defense_Viewer.zip
```

The builder checks every included case before packaging and binds the supplied
quality counts to each exact summary hash. Keep the independent audit evidence
with your research records. This packaging step does not independently derive
the reference-quality counts from raw orbit products.
