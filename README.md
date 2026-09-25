# vineyard-ai — Vineyard AI Field Challenge (DeepTech GigaHack 2026 · Marcaj)

End-to-end pipeline: Sireț3 UAV tiles → AI pre-annotations (canopies, rows, inter-row areas, waste)
→ Marcaj correction → measurements + optimised walking route → web interface.

> Work in progress — full reproduction steps, timings and model weights will be added before submission.

## Quick start
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# raw challenge package in the parent folder, or: export VINEYARD_RAW=/path/to/package
python scripts/setup_data.py        # extracts 311 tiles into data/
python -m pipeline.tiles            # sanity check: prints tile georeferencing
```

## Deliverables
- `route.geojson` — walking route (EPSG:32635, `length_m`)
- `measurements.csv` — counts, row lengths, areas by `vineyard_id` / `row_id`
- Web interface: _link TBD_
- Model weights: _link TBD_

## Processing time
_TBD (311 tiles, hardware)_

## Paid APIs / LLMs
None in the processing pipeline. Development assisted by Claude Code.

## Licences & attribution
Sireț3 imagery: CC BY 4.0 — 3DATA COLLECT / OpenAerialMap, contributors to the Open Imagery Network.
Route data contains information from OpenStreetMap © OpenStreetMap contributors, ODbL.
