# vineyard-ai — Vineyard AI Field Challenge (DeepTech GigaHack 2026 · Marcaj)

End-to-end pipeline for the Sireț3 UAV orthomosaic (311 GeoTIFF tiles, 2.5 cm/px, EPSG:32635):
AI pre-annotations (canopies, row axes, inter-row areas, attributes) → manual correction in Marcaj →
global block / row IDs → measurements → two walking routes → interactive web map.

| Deliverable | Where |
|---|---|
| Walking route, inspector (row gaps + waste) | [`route.geojson`](route.geojson) — one LineString, EPSG:32635, `length_m` |
| Walking route, farmer (waste only) | [`route_waste.geojson`](route_waste.geojson) |
| Measurements by `vineyard_id` / `row_id` | [`measurements.csv`](measurements.csv) |
| Web interface | **https://viteokk.github.io/vineyard-ai/** (GitHub Pages from `web/`, branch `gh-pages`) · local: `python -m http.server -d web 8000` |
| Model weights | [yolov8n-seg-vineyard-canopy.pt (GitHub release v0.1-weights)](https://github.com/Viteokk/vineyard-ai/releases/tag/v0.1-weights) |
| Pre-annotations uploaded to Marcaj | `out/upload/*.zip` (CVAT for images 1.1, built by `pipeline/export_cvat.py`) |

## Architecture

```
 311 GeoTIFF tiles ──► detect (pipeline.baseline: ExG → row grid → canopies / inter-rows / attributes,
                       vine / non-vine + forbidden filters; optional pipeline.infer_yolo)
                   ──► blocks (global vineyard_id / row_id across tiles)
                   ──► export_cvat (Marcaj ZIPs) ──► human correction in Marcaj ──► export ──┐
                   ──► targets (row gaps ≥ 3 m, waste) ──► route ×2 (grid graph + TSP) ──► validate  │
                   ──► measurements.csv                                                              │
                   ──► web/data (mosaic + GeoJSON) ──► web/index.html (Leaflet, static)  ◄───────────┘ (Sunday recompute)
```
Every arrow is a CLI stage (`python -m pipeline.<stage>`), chained by `pipeline/run.py`; all geometry in EPSG:32635 metres.

## Install

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock.txt          # exact versions (numpy, opencv, shapely, scipy, ortools, ultralytics, torch)
# raw challenge package (01_tiles … 05_examples) in the parent folder, or: export VINEYARD_RAW=/path/to/package
python scripts/setup_data.py                  # extracts the 311 tiles into data/tiles + route inputs + examples
```

## Run: from the supplied tiles to the routes and the measurements

```bash
python -m pipeline.run --all                  # classical detector (the configuration we submitted)
python -m pipeline.run --all --weights yolov8n-seg-vineyard-canopy.pt   # YOLO canopies + classical rows
```

Stages (each is its own CLI, `python -m pipeline.<stage> --help`):

| Stage | Module | What it does |
|---|---|---|
| detect | `pipeline.baseline` | ExG vegetation → dominant row orientation → periodic row grid → per-row line fit → canopies inside the ±0.3 m band → inter-row strips → `row_structure`, `interrow_cover`. Vine / non-vine filter (crown spill + size) and forbidden-zone filter. |
| (optional) | `pipeline.infer_yolo` | YOLOv8n-seg canopies on 640 px crops, merged with the classical rows |
| blocks | `pipeline.blocks` | global `vineyard_id` (connected plantings < 5 m apart, roads always separate) and `row_id` shared by the segments of one physical row across tiles |
| targets | `pipeline.targets` | inspection targets = row gaps ≥ 5 m (ID, X, Y, `vineyard_id`, `row_id`) + waste centres |
| route | `pipeline.route` | 0.5 m grid graph on inter-rows + passages (canopies / forbidden blocked), OR-Tools TSP from START, outside-share budget < 1.5 % (official limit 2 %); `--mode inspector` / `--mode farmer` |
| validate | `pipeline.validate` | one LineString, EPSG:32635, `length_m`, start = end ≤ 5 m, share outside inter-rows + passages, targets visited ≤ 2 m |
| measure | `pipeline.measurements` | `measurements.csv`: totals, per block, per row (m, m², ha; canopy area = union of polygons) |
| export | `pipeline.export_cvat` | Marcaj upload ZIPs (`annotations.xml` + unchanged tiles), split < 60 MB, validated |
| web | `scripts/make_web_tiles.py`, `scripts/build_web_map.py` | orthophoto mosaic (10 cm/px + full-res reference tiles) and GeoJSON layers for `web/index.html` |

Sunday recompute from the corrected Marcaj export: `python -m pipeline.blocks --inp out/marcaj_export.xml --out out/marcaj_global.xml`
then `python -m pipeline.run --from targets --inp out/marcaj_global.xml`.

Local scoring on the two official example tiles (same formulas as the challenge): `python -m pipeline.eval --pred out/baseline.xml`
→ partial score 0.817 for the classical detector (canopy 0.587, axes 0.961, attributes 0.970, grouping 1.0, counts 0.98).

## Run on your own survey

The pipeline is not tied to Sireț3. For another vineyard flight:

1. Tiles: georeferenced RGB GeoTIFFs (any size, ~2–4 cm/px works best), EPSG:32635 or any metric CRS with the
   georeference in the TIFF tags (`ModelTiepoint` + `ModelPixelScale`, read by `pipeline/tiles.py`). Put them in
   `data/tiles/` named `siret3_rRRR_cCCC.tif` (row / column of a regular grid) or adapt `NAME_RE` in `pipeline/tiles.py`.
2. Route inputs in `data/route/`: `start.geojson` (Point), `passages.geojson` (walkable roads / paths, MultiPolygon),
   `forbidden.geojson` (no-go areas), `study_area.geojson` — same CRS as the tiles.
3. `python -m pipeline.run --all` → `route.geojson`, `route_waste.geojson`, `measurements.csv`, `out/upload/*.zip`
   (CVAT 1.1 for a Marcaj / CVAT correction pass), `web/` ready to serve. Detector parameters (row spacing band, canopy
   thresholds) are in `pipeline/baseline.py` (`class P`); check them on 1–2 annotated tiles with `pipeline/eval.py`.
4. Docker: `docker build -t vineyard-ai .` then
   `docker run --rm -v /path/to/package:/raw -v $(pwd)/out:/app/out -v $(pwd)/web:/app/web vineyard-ai sh -c "python scripts/setup_data.py && python -m pipeline.run --all"`.

## Processing time and hardware

Measured on a MacBook Pro (Apple M4 Pro, 24 GB), macOS 27, Python 3.12, no GPU used for the submitted pipeline:

| Stage | 311 tiles |
|---|---|
| detect (classical) | 1 min 53 s |
| blocks (global IDs) | 3 s |
| targets | 7 s |
| route inspector (1 855 targets) | ~12 min (10 min distance matrix, cached; 60 s TSP) |
| route farmer | seconds (no waste in the pre-annotations) |
| measurements + export ZIPs | 5 s |
| web data (mosaic + layers) | 3 min |

YOLOv8n-seg training (optional): 53 min on the M4 Pro GPU (MPS), 17 epochs, early stop, best at epoch 7.
Full per-stage timings of the last run: `out/timing.json`.

## Model

How the whole solution works, the training data and the open questions: [`docs/MODEL.md`](docs/MODEL.md).
One YOLOv8n-seg with two classes (`vineyard`, `waste`): `train/make_multi_dataset.py` (Sireț3 crops with the official
reference + pseudo-labels, DroneWaste v1.0 and UAVVaste, both CC BY 4.0, non-waste categories dropped) →
`train/train_yolo.py --name multi` → `pipeline/infer_multi.py` (waste boxes + optional model canopies on classical rows).

Submitted detections come from the classical pipeline (it scored higher than the fine-tuned YOLO on the example
tiles: canopy 0.587 vs 0.562, because its outlines match the loosely traced reference better). The neural model
is delivered anyway: `train/make_dataset.py` (pseudo-labels + official example, 640 px crops) → `train/train_yolo.py`
(yolov8n-seg, MPS) → weights in the release above; `pipeline/infer_yolo.py` plugs it into the same pipeline.

## Web interface

`web/index.html` — Leaflet on the real orthophoto in UTM (CRS.Simple, no reprojection): layers (canopies, rows with
`row_id`, inter-rows with cover, passages, forbidden, blocks), both routes with length, walking time and targets
visited, measurements per block / row, pipeline status. Data contract: `web/data/*.geojson`, `web/data/pred/*`,
`web/data/route*.geojson`, `web/data/route_check_*.json`, `web/data/variants/`.

- **Roles** (first screen, or `#inspector` / `#fermier` / `#agronom` in the link): state inspector (blue route,
  gaps, blocks, areas), farmer (red route, waste, missing vines ≈ gap length / 1.2 m, replanting cost, yearly
  maintenance at MDL 52 000–80 000 / ha from the brief), agronomist (all layers).
- **Parameters:** walking speed and hours per day (times and field days update at once), minimum gap to inspect
  (precomputed routes for ≥ 5 / 8 / 10 m on the static site, `scripts/build_route_variants.py`).
- **Field use:** GPX export of each route, GPS navigation on the phone with a chosen start and checked targets.

### Live mode on the laptop
```bash
python -m pipeline.serve            # http://127.0.0.1:8000 — the same site plus the local API
```
The site detects the API and switches to live computation:
- **Recompute a route** with any gap threshold (`POST /api/route`): `pipeline.route` on the filtered targets with
  the cached distance matrix, 1–3 min on the M4 Pro, drawn as a third route with its own GPX.
- **Analyse a new tile** (`POST /api/analyze`): upload any georeferenced GeoTIFF at ~2.5 cm/px; the classical
  detector and the AI model return canopies, rows, inter-rows and waste on the map with counts and areas
  (~4 s per 2048 px tile on CPU).

## Paid APIs / LLMs

None in the processing pipeline. Development assisted by Claude Code.

## Licences & attribution

Sireț3 imagery: CC BY 4.0 — 3DATA COLLECT / OpenAerialMap, contributors to the Open Imagery Network.
Route inputs contain information from OpenStreetMap © OpenStreetMap contributors, ODbL.
