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
| Model weights | [yolo11n-seg-vineyard-waste.pt (release v0.2-weights)](https://github.com/Viteokk/vineyard-ai/releases/tag/v0.2-weights) · earlier canopy-only [v0.1-weights](https://github.com/Viteokk/vineyard-ai/releases/tag/v0.1-weights) |
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
One **YOLO11n-seg** with two classes (`vineyard`, `waste`) —
[weights: GitHub release v0.2-weights](https://github.com/Viteokk/vineyard-ai/releases/tag/v0.2-weights):
`train/make_multi_dataset.py` (Sireț3 crops with the official reference + pseudo-labels, DroneWaste v1.0 and UAVVaste,
both CC BY 4.0, non-waste categories dropped) → `train/train_yolo.py --model yolo11n-seg.pt --name multi11` (15 epochs,
2 h 51 min on the M4 Pro GPU) → `pipeline/infer_multi.py`. Held-out validation: mask mAP50 vineyard 0.717, box mAP50
waste 0.731 (YOLOv8n-seg: 0.713 / 0.654). On the held-out example tile the classical canopies still score higher
(0.683 vs 0.529 model-only), so the submitted canopies are classical; the model supplies waste candidates and the
live "analyse a tile" mode.

Submitted detections come from the classical pipeline (it scored higher than the fine-tuned YOLO on the example
tiles: canopy 0.587 vs 0.562, because its outlines match the loosely traced reference better). The neural model
is delivered anyway: `train/make_dataset.py` (pseudo-labels + official example, 640 px crops) → `train/train_yolo.py`
(yolov8n-seg, MPS) → weights in the release above; `pipeline/infer_yolo.py` plugs it into the same pipeline.

## Web interface

Pages: `web/index.html` (landing: interactive map of Moldova, current figures and the challenge requirements, read from `web/data`) → `web/login.html` (role + demo account `inspector@fieldplanner.demo` / `administrator@fieldplanner.demo`, password `demo2026`, prefilled for the chosen role and checked in the browser only; or MPass) → `web/app.html`, the map. The role is fixed by the account; logging out is the way to switch.

`web/app.html` — Leaflet on the real orthophoto in UTM (CRS.Simple, no reprojection): layers (canopies, rows with
`row_id`, inter-rows with cover, passages, forbidden, blocks), both routes with length, walking time and targets
visited, measurements per block / row, pipeline status. Data contract: `web/data/*.geojson`, `web/data/pred/*`,
`web/data/route*.geojson`, `web/data/route_check_*.json`, `web/data/variants/`.

- **Roles** (login page, or `#inspector` / `#fermier` in the link): state inspector (compliance, blue route, gaps,
  blocks, areas) and vineyard administrator (red route, waste, missing vines ≈ gap length / 1.2 m, replanting cost,
  yearly maintenance at MDL 52 000–80 000 / ha from the brief, Marcaj corrections). Each role sees only its tabs.
- **MPass:** „Intră cu MPass” on the login page redirects to the real `https://mpass.gov.md/login/saml`. For the demo, a
  simulated flow is linked under it: `mpass.html`, an authorization page clearly labelled as a demo, with test identities
  and no credential fields, then `auth.html`, the callback that opens the session with the role. A real integration needs Field Planner registered as a SAML 2.0 service provider with the
  Agenția de Guvernare Electronică and a server-side assertion consumer endpoint that checks the signature and maps
  the IDNP to a role; a static site cannot do this.
- **Planifică (both roles):** choose a work zone (cadastral number anywhere in Moldova via ASP, a drawn rectangle / polygon,
  blocks or a parcel clicked on the map), a START (official, clicked on the map, or the phone's GPS) and the targets (row gaps
  from a minimum length, annotated waste, AI waste candidates). The walking route is computed **in the browser**
  (`web/router.js`, a Web Worker) with the same rules as `pipeline/route.py`: 0.5 m grid, only inter-rows and authorised
  passages as cheap cells, canopies (+0.35 m) and forbidden zones blocked, targets visited within 2 m, TSP with 2-opt,
  START → targets → START; GPX / GeoJSON export. Typical block: 1–40 s. The share outside inter-rows is shown; the
  optional *Strict* mode drops the costliest stops to stay under the competition's 2 % (checked with `pipeline.validate`:
  0 m through canopies and forbidden zones).
- **Parameters:** walking speed and hours per day (times and field days update at once), minimum gap to inspect
  (precomputed routes for ≥ 5 / 8 / 10 m on the static site, `scripts/build_route_variants.py`).
- **Field use:** GPX export of each route, GPS navigation on the phone with a chosen start and checked targets.

### Vineyard register (DEMO, EU-compatible)
`python -m pipeline.register [--make-demo]` → `web/data/register.geojson`, `out/register.csv` (≈ 2 s).
Model in [`registry/schema.json`](registry/schema.json), aligned with Reg. (EU) 2018/273 art. 7 and annexes III–IV:
**grower** (`DEMO-G-xx`, pseudonymised) → **parcel** (`cad_nr` `DEMO-xxxx`, geometry, RVV code, declared area, variety,
planting year and scheme, authorisation, status planted / grubbed_up / abandoned, IGP) → **events** (planting,
replanting, grubbing-up, inspection). **Everything declared is synthetic and marked `demo: true`**; the parcels are
generated from the detected blocks (whole blocks, halves split parallel to the rows, one half without authorisation,
two parcels with no vines, one declared abandoned) and linked to `registry/rvv_demo.json` by `cad_nr`.
Measured fields per parcel come from the pipeline: rows clipped to the parcel × the block's median row spacing
(`measured_area_ha`, a few % below the block figure because rows are cut exactly at the parcel edge), rows, density
nominal / effective (1.2 m vine spacing), gap share, missing vines, inter-row cover shares and `status_detected`
(`no_vines`: measured < 10 % of the parcel; `abandoned`, heuristic: vegetation inter-rows > 80 % and gaps > 40 %).
In the web map (Conformitate → Registru viticol, layer “Registru viticol DEMO”, ⌘K search `DEMO-0005`, link
`#parcel=DEMO-0005`) a click opens the **parcel sheet**: declared vs measured side by side (differences above the
tolerances in `criteria.json` highlighted), missing authorisation in red, event history, DEMO label always visible;
**register extract** as CSV or GeoJSON for one parcel or all, with generation date, source and DEMO notice.
Tests: `python -m unittest tests.test_register` (halves sum to the block within 1 %, every parcel has id / geometry /
status, scenarios present, registry linked). Real integration (ONVV RVV, ASP cadastre, AIPA) would be a data exchange
through MConnect; not implemented.

### Possible unauthorised plantings / register to update (DEMO)
`python -m pipeline.register_mismatch` → `web/data/register_mismatch.geojson`, `out/register_mismatch.csv` (< 1 s).
Compares where vines are (detected blocks) with where they are registered (DEMO register), Reg. (EU) 1308/2013
art. 62–72 and Reg. (EU) 2018/273 art. 7, 37. Only real vineyards count (≥ 3 rows and ≥ 0.15 ha, the register
threshold); smaller ones are listed as below threshold, never flagged. **Type A** “possible unauthorised planting”:
part of a vineyard not covered by a planted, authorised parcel (≥ 0.05 ha or ≥ 10 % of the block). **Type B**
“possible grubbed-up / abandoned”: parcel registered as planted with < 10 % covered by detected vines. Thresholds,
wording and legal texts live in `registry/criteria.json` → `mismatch`; every item is a signal, not a verdict, with a
visit point snapped to the nearest inter-row / passage. In the web map (Conformitate): red / orange layer, list sorted
by area, “+ vizită”; on the laptop (`python -m pipeline.serve`) the route through the chosen places is computed and
checked with `pipeline.validate` (example: 2 places, 1.24 km, 0.14 % outside, valid); on GitHub Pages the visit list
downloads as GPX. `route.geojson` is never changed. Tests: `python -m unittest tests.test_register_mismatch`
(all deliberate DEMO mismatches found, nothing below 0.15 ha flagged).

### Compliance: vineyard register (ONVV), cadastre, AIPA subsidies
Tab „Conformitate” (role *Inspector*): per block, what the drone measured (planted area, density, gaps) against the
Registrul vitivinicol entry and the AIPA request (demo records, clearly labelled), plus the **real public cadastral parcels**
(ASP, WFS on geodata.gov.md): status per block, the eligible amount recomputed, a 9 km control route through the 28 blocks
to visit only, and a printable inspection report. A real registry extract (CSV) can be loaded in live mode.
Sources, legal basis and what is real vs demo: [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md).
`python -m pipeline.cadastre` · `python -m pipeline.compliance --visit-route [--registry-csv extras.csv]`

### Zones, cadastral search, Moldova context
- **Zonă** tab (`#zona`): one zone polygon from three inputs — draw (rectangle / polygon), select (blocks, tiles, the
  cadastral parcel under a click; Shift+click adds), or a cadastral number. The same zone gives measurements (rows,
  canopies, inter-rows, gaps, missing vines, waste), the blocks with their compliance status, the cadastral parcels,
  GeoJSON / CSV export, a printable report and, in live mode, a route through the zone only. Shareable links:
  `#zona=bloc:V63,V32`, `#zona=cad:80371140153`, `#zona=tile:r021_c012`, `#zona=poly:x,y;x,y;...`.
- **Cadastral numbers**: search anywhere in Moldova (⌘K or the Conformitate tab) — the page queries the public ASP
  cadastre on geodata.gov.md (WFS with CORS; MOLDREF99 → UTM 35N with proj4) and shows land use, area and the vines
  detected on the parcel when it is inside the flight. No owner data is used.
- **Moldova**: country outline (Natural Earth), 35 raions and the Sireți commune (ASP), study area highlighted
  (`scripts/build_moldova.py`).
- Production step not in this repo: the vineyard register (RVV, ONVV) and AIPA files have no public API; the real
  integration is a data exchange through MConnect (the government interoperability platform). The CSV import in live
  mode is the stand-in.

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
