# AUDIT — sâmbătă 26 sept, ~01:00 (starea repo-ului după prima noapte)

Verificat: README, route.geojson, route_waste.geojson, measurements.csv, out/upload/*.zip, out/route_check_*.json,
out/targets_*.geojson, pipeline/waste.py, web/data, git status, GitHub release v0.1-weights.

## Gata (nu mai atinge decât dacă e nevoie)
- Detecție clasică pe 311 tile-uri (scor local 0.817); YOLO antrenat (best epoca 7, mask mAP50 0.715), coroane 0.562 < clasic 0.587 → **clasicul e varianta predată**, YOLO = livrabilul „model neural” (release v0.1-weights).
- ID-uri globale: **70 de blocuri**, 2 583 rânduri, 3 213 segmente (blocurile < 3 rânduri eliminate).
- **9 ZIP-uri în out/upload/** (37–58 MB, toate cu annotations.xml, total exact 311 tile-uri) — gata de Marcaj.
- measurements.csv (total / bloc / rând), pipeline/run.py (o comandă, timpi în out/timing.json), README aproape complet.
- Web: hartă pe ortofoto, straturi, structură pentru două trasee.

## Probleme, în ordinea gravității

### P1 — CRITIC: traseul inspectorului pică validarea (25% din punctaj → 0)
- `route.geojson` (21:50): length 26 898 m, 1 680 / 1 855 ținte, **outside_share 0.0099 după route.py**.
- `out/route_check_inspector.json` (21:51, validate.py): **valid=False, outside_share 0.1045 (2 811 m în afara zonei)**, canopy_m 3.6.
- Limita oficială: 2 %. Cele două calcule se contrazic → de aflat care e corect (ipoteză: route.py numără pe grila de 0.5 m
  „celule walkable”, validate.py pe poligoanele reale interrow ∪ passage; linia simplificată taie colțuri / merge pe
  marginea benzii). Ținta: **< 1.5 % după validate.py**, care e arbitrul.
- route.py e modificat și necomis (lucru în curs la 21:53–21:54).

### P2 — acoperire la limită (bonus eficiență 10% se dă doar ≥ 90 %)
- 1 680 / 1 855 = 90.6 %. 175 ținte nevizitate, din care 14 „unreachable” (fără drum în graf) — de ce? (inter-rând lipsă,
  bloc izolat de passages, țintă la > 2 m de orice celulă walkable?). Ținta: ≥ 93 %.

### P3 — deșeuri: zero în pre-adnotări (decizie corectă, dar cu consecințe)
- waste.py: candidații automați = mașini, acoperișuri, pietre, flori → nu s-au scris cutii (fals pozitiv = miss).
- Consecințe: (a) deșeurile (10 %) se găsesc **manual în Marcaj**; lista `out/waste_candidates.json` e checklist pentru oameni;
  (b) `route_waste.geojson` (fermier) e gol (0.2 m) → se recalculează duminică din exportul Marcaj; pentru demo trebuie
  să existe câteva deșeuri reale.

### P4 — necomis / web incomplet
- Necomis: route.geojson, route_waste.geojson, pipeline/route.py (modificat).
- web/data nu are route.geojson (inspector) și route_check_inspector.json → site-ul nu arată traseul albastru.
- README: lipsește linkul live al site-ului (deploy GitHub Pages).

## Ce rămâne pe sâmbătă (în ordine)
1. P1 → P2 (traseu valid, acoperire ≥ 93 %), apoi commit.
2. Dry run Marcaj cu 05_examples/siret3_examples_cvat.zip → Remove all.
3. Upload cele 9 ZIP-uri → Files = 311 → PUBLISH (≤ 14:00). Apoi corectură (echipa) cu prioritate pe deșeuri.
4. Web: ambele trasee + ținte + cifre; deploy GitHub Pages; link în README.
5. Duminică: export Marcaj → blocks → run --from targets → route/route_waste/measurements noi → web → repo public → pitch.
