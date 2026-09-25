# CLAUDE.md — vineyard-ai (DeepTech GigaHack 2026 · Marcaj Vineyard AI Field Challenge)

> Status, rezultate, plan rămas și decizii: vezi `HANDOFF.md` (actualizează-l când se schimbă ceva important).

## Mission
Build an end-to-end app that turns the Sireț3 UAV orthomosaic (311 GeoTIFF tiles) into an annotated vineyard
map + measurements + an optimised walking route. **Follow the official rules exactly and optimise every
decision for maximum score.** Official PDFs are in `../03_docs/` — they override anything else
(the "Visual Journey" PDF is only illustrative).

**Deadline: Sunday 27 Sept 2026, 15:00 (Chișinău)** — repo + all Marcaj jobs submitted.
**Pre-annotations can be imported into Marcaj ONLY ONCE, before Publish (official target: Sat ~14:00; internal 12:00).**
Dry run first: upload the example ZIP (05_examples), check the import, then Remove all.
Route graph = interrow_area ∪ passage, avoiding vineyard canopies ∪ forbidden (official onboarding).

## Scoring (85% automatic, hidden subset of the 311 tiles)
| Weight | What | Metric |
|---|---|---|
| 25% | canopies `vineyard` | 0.6·class IoU + 0.4·F1 (1-to-1 at IoU≥0.5); canopies on no-vine tiles penalised 0.5×covered share |
| 10% | waste boxes | F1, 1-to-1 at IoU≥0.3; false boxes cost like misses |
| 8% | row axes | match if each ≥80% within 0.4 m of the other |
| 5% | attributes | mean(accuracy, macro-F1) of row_structure & interrow_cover; missing objects = errors |
| 2% | vineyard_id grouping | consistency only (ID values irrelevant) |
| 10% | counts/measures | blocks, rows, canopy area, inter-row area (tol 15%), total row length (tol 10%) |
| 15%+10% | route | coverage of hidden targets (≤2 m) + efficiency L_ref/L (only if coverage ≥90%) |
| 15% | engineering | architecture, robustness, scalability, measured performance (jury) |
Route = **0** if >2% of its length is outside passable inter-row areas ∪ passages, or it doesn't return to START (≤5 m).

## Annotation rules (must be respected by model output)
- Labels / attributes EXACT, lower-case: `vineyard`(polygon: vineyard_id), `waste`(box: vineyard_id),
  `row`(polyline: vineyard_id,row_id,row_structure), `interrow_area`(polygon: vineyard_id,interrow_cover).
- row_structure: `regular` | `disrupted` (gap ≥5 m in THIS tile, or tree/obstacle in row) | `unassessable`.
- interrow_cover: `bare_soil` (<25% veg) | `mixed` (25–75%) | `vegetation` (>75%) | `unassessable`.
- One canopy polygon per plant (never per row). Grapevines only — no trees/orchards/shrubs/grass.
  Weeds <0.2 m² not annotated. Touching canopies split at narrowing, else at in-row spacing 1.0–1.5 m.
- Row = one straight-ish polyline per physical row per tile, first→last vine, straight through gaps,
  within 0.2 m of vine centres. Same row_id across tiles (recommend `V03-R017`).
- interrow_area: canopy edge to canopy edge of two neighbouring rows of same block, ends at the shorter row,
  never overlaps canopies, none outside outermost rows, holes for trees/buildings, cut at tile edge.
- Block = connected planting; same block if <5 m apart; a road/track ALWAYS separates blocks.
  Waste gets vineyard_id if ≤10 m from a block, else empty.
- Waste: bags, plastic film, bottles, cans, tyres, rubble, rubbish heaps. NOT waste: white vine tubes,
  stakes, posts, wires, hoses, stones, pale soil, pruning residue, vehicles. When in doubt, leave it out.
- Garden vineyards count only with ≥3 rows. Nothing in black no-data areas.
- Tiles with nothing → exported with no objects (humans tick "No objects in this frame" in Marcaj).

## Data facts
- Tiles: 2048×2048 px, 0.025 m/px (51.2 m), EPSG:32635, JPEG-compressed GeoTIFF, name `siret3_rRRR_cCCC.tif`.
  Georef from TIFF tags (see `pipeline/tiles.py`). Pixel→UTM: X = X0 + px·0.025, Y = Y0 − py·0.025.
- Route inputs (EPSG:32635): START (629504.70, 5220250.75); passages.geojson; forbidden.geojson; study_area.geojson.
- Reference examples (`data/examples`): siret3_r021_c012 (V01, 25 rows regular, 399 canopies, 24 bare_soil),
  siret3_r006_c004 (V02, 26 rows / 5 disrupted, 251 canopies, 21 bare_soil + 4 mixed). Both are among the 311 tiles.
- Measured on examples: row spacing axis-axis 2.5–2.8 m; canopy median ~0.5 m² (p90 1.1–1.9 m²);
  canopy centre ~5 cm from row axis; young vines, many gaps.

## Deliverables (repo root)
- `route.geojson` — ONE LineString, EPSG:32635, start=end=START (≤5 m), property `length_m`.
- `measurements.csv` — block & row counts, row lengths (per row + total, m), canopy area (union) and
  inter-row area (m² and ha), by vineyard_id / row_id.
- `README.md` — install/run from tiles → route + measurements, pinned deps, model weights link,
  processing time for 311 tiles + hardware, paid APIs (none), web app link. Dockerfile = bonus.
- Code + model weights (link). Annotations are taken by organisers from Marcaj.

## Upload ZIP (CVAT for images 1.1) — see `pipeline/cvat_io.py`
`annotations.xml` + `images/<original tile names, unchanged>`; ≤90 MB per ZIP; one ZIP per original part
(`data/parts.json`). All 5 parts uploaded, check 311 files, THEN publish.

## Repo layout & ownership (avoid merge conflicts)
- `pipeline/` data, model inference, post-processing, CVAT export — **Dev 1**
- `train/` dataset building + YOLO training notebook — **Dev 1**
- `pipeline/route.py`, `pipeline/measurements.py`, `pipeline/validate.py`, `web/` — **Dev 2**
- `config.py`, `CLAUDE.md`, `README.md` — shared, small edits only, pull before editing
- Raw challenge package lives in the parent folder (or `VINEYARD_RAW`); `data/` and `out/` are git-ignored.

## Conventions
- Python 3.11, numpy/opencv/shapely/pyproj; all geometry processing in UTM metres.
- Every stage is a CLI: `python -m pipeline.<stage>`; writes to `out/`.
- `pipeline/eval.py` reproduces the scoring on the 2 example tiles — every model change must improve it.
- Commit small and often; `git pull --rebase` before push. Never commit tiles, zips or .pt files.
