# SCORE_PLAN — ce facem pentru punctaj maxim, pe fiecare criteriu oficial (sâmbătă 26 sept)

Ordinea = puncte câștigate / oră. Fiecare punct are un test de acceptare; nu se trece mai departe fără el.

## S1 — Traseu valid (25 % → altfel 0)  [PRIORITATE 1]
- validate.py e arbitrul: outside_share < 0.015, start_m/end_m ≤ 5, canopy_m = 0, forbidden_m = 0.
- Acoperire ≥ 92 % din țintele noastre (limita de 1.5 km per stop NU trebuie să taie acoperirea; blocurile izolate se leagă prin passages).
- Test: out/route_check_inspector.json valid=True; raport: length, visited/targets, outside_share.

## S2 — Recall pe țintele de referință (până la +10 % pe traseu)
- Compară out/targets_pred_examples.geojson cu out/targets_ref_examples.geojson: o țintă de referință e acoperită dacă
  există o țintă a noastră la ≤ 2 m SAU dacă traseul trece la ≤ 2 m de ea.
- Dacă recall < 90 %: prag gol 5 m → 3.5 m; ținte pe AMBELE inter-rânduri de lângă fiecare rând disrupted; puncte la 4 m pe golurile > 10 m.
- Test: recall ≥ 90 % pe exemple, fără să crească outside_share.

## S3 — Zero coroane false pe tile-uri fără vie / zero vie ștearsă (protejează 25 %)
- Planșă cu cele 98 de tile-uri golite de filtrul vie/non-vine (nume pe fiecare) + listă suspecte → verificare umană (Vikea).
- Regula: în dubiu, PĂSTREAZĂ desenele (echipa șterge ușor în Marcaj; a desena de la zero e scump).
- Test: lista finală de tile-uri goale confirmată manual înainte de export.

## S4 — Forma coroanelor (IoU 0.61 → țintă ≥ 0.66)
- Referința e trasată larg (~10 cm în jurul frunzelor). Testează dilatare 5 → 8 → 10 cm și closing pe eval.py; păstrează maximul.
- Verifică că F1@0.5 nu scade (numărul de poligoane rămâne ~1:1 cu referința).
- Test: pipeline.eval canopy ≥ 0.62 (era 0.587).

## S5 — Cele 2 tile-uri exemplu cu adnotările oficiale
- Flag --use-examples în export_cvat.py: înlocuiește predicțiile pe siret3_r021_c012 și r006_c004 cu 05_examples/annotations.xml
  (IDs remapate la blocurile globale). NU se activează până la confirmarea mentorilor.

## S6 — Blocuri și rânduri (10 % counts + 2 % grouping)
- Verifică pe hartă blocurile mari (V045, V054, V097 …): un drum din passages.geojson trebuie să le despartă mereu.
- Verifică că niciun row_id nu apare în două tile-uri neadiacente și că nu există rânduri duplicate la marginea tile-urilor.
- Toleranțe: 15 % counts/arii, 10 % lungime. Test: raport nr. blocuri / rânduri înainte-după + listă anomalii.

## S7 — Deșeuri (10 %) — manual în Marcaj
- out/waste_candidates.json → out/waste_checklist.md: pe tile, coordonate pixel, scor, miniatură (crop 128 px) — lista pe care
  un om o parcurge în Marcaj. Un membru al echipei lucrează DOAR pe deșeuri.
- Opțional (dacă e timp): YOLO detecție pe UAVVaste (Apache-2.0) rulat doar pe candidați, ca re-ordonare.

## S8 — Inginerie (15 %)
- Dockerfile (python:3.12, requirements.lock.txt, ENTRYPOINT pipeline.run). Test: `docker build` + `docker run … --help`.
- README: arhitectură (diagramă Flow A), timpi pe hardware exact, secțiunea „Run on your own survey”, link weights, link web.
- Re-rulare live la demo: `python -m pipeline.run --all` trebuie să meargă curat, < 20 min, cu progres afișat.
- Demo Zona 2: taie o vie din 04_source (în afara celor 311 tile-uri), rulează pipeline-ul, expune pe site ca „Zona 2”.

## S9 — Marcaj (condiție de admitere)
- Dry run cu ZIP-ul exemplu → Remove all. Upload 9 ZIP-uri → Files = 311 → PUBLISH ≤ 14:00.
- Fișa echipei (o pagină): reguli de aur, taste, ordinea corecturii, checklist deșeuri, Submit la fiecare job, NU redenumi ID-uri.
- Duminică: export → blocks → run --from targets → route/route_waste/measurements/web → repo public ≤ 15:00.
