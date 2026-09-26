# PRODUCT_PLAN — de la „hartă cu cifre” la „planificator de teren” (după ce SCORE_PLAN S1–S3 și Publish sunt gata)

Problema reală (brief Marcaj): mai puțin timp de pregătire și de mers pe jos, cu aceeași acoperire, pentru audit
(inspector) și pentru întreținere (fermier). Traseul complet de 38 km / 9.6 h nu e un plan de lucru → aplicația
trebuie să răspundă la „ce fac mâine, în 3 ore?” și „ce trebuie făcut în fiecare bloc și cât costă?”.
Toate punctele sunt front-end static + scripturi Python care scriu în web/data. Nu se atinge nimic din livrabilele
obligatorii (route.geojson = traseul complet al inspectorului rămâne cel predat).

## P1 — Ture pe zi, pe bloc, cu buget de timp  [impact maxim, 2–3 h]
- `pipeline/tours.py`: din targets_inspector + graful de traseu, împarte țintele în ture:
  a) pe bloc (o tură = blocurile vecine până la bugetul de timp), b) după prioritate (gol mai mare = mai important;
  deșeuri = prioritate mare), c) buget de timp configurabil (1 h / 2 h / 4 h / zi întreagă la 4 km/h).
- Fiecare tură = un LineString START → ținte → START, validat cu validate.py (≤ 2 % în afara zonei).
- Output: web/data/tours.geojson (FeatureCollection, properties: tour_id, day, minutes, length_m, blocks, targets, priority_score).
- Web: selector „Buget: 2 h” → lista turelor (Ziua 1 / 2 / 3…), click = evidențiază tura pe hartă + țintele ei; export GPX per tură.
- Test: suma turelor acoperă ≥ 95 % din ținte; fiecare tură ≤ buget; fiecare tură validă.

## P2 — Lista de sarcini pe bloc  [2 h]
- `pipeline/block_report.py` → web/data/blocks_report.json: pentru fiecare vineyard_id:
  rânduri, rânduri disrupted (%), stare (verde < 10 %, galben 10–30 %, roșu > 30 %), lungime goluri (m),
  butuci lipsă estimați (= lungime goluri / 1.2 m), cost replantare (= butuci × preț butaș, implicit 15 MDL, editabil),
  deșeuri în bloc, ha coroane / inter-rânduri, cost întreținere anual (ha × 52 000–80 000 MDL, sursă: brief Marcaj).
- Web: panou „Sarcini” = blocurile sortate roșu → verde, fiecare cu: „Replantează ~N butuci (~X MDL)”, „Strânge M deșeuri”,
  „Verifică rândurile R03, R07…”; click = zoom pe bloc; buton „Export CSV sarcini”.
- Test: totalurile din blocks_report.json = cele din measurements.csv.

## P3 — Deșeuri reale → traseul fermierului  [după corectura din Marcaj, duminică]
- Export Marcaj → run --from targets → route_waste.geojson cu deșeuri reale; traseul roșu vizibil pe hartă cu lungime și minute.
- Până atunci, pentru demo: NU inventa deșeuri. Arată în UI mesajul „0 deșeuri în ciornă; se completează după verificarea umană”.

## P4 — Încredere: ciornă AI vs verificat de om  [1 h]
- Fiecare obiect din web/data primește `source`: "ai" (pre-adnotare) sau "human" (din exportul Marcaj, duminică).
- Web: toggle „Arată doar verificate”; în fișa blocului „x % din rânduri verificate”. Legendă cu explicația.

## P5 — Navigare pe teren  [1.5 h]
- Export GPX per traseu / per tură (track + waypoints cu ID și tip).
- Pe telefon: navigator.geolocation → poziția pe hartă, distanța la următoarea țintă din tură, buton „Verificat” (localStorage, try/catch).

## P6 — Finisaje demo  [1 h total]
- Căutare după ID (V02 / V02-R017) → zoom + fișă. Link partajabil (hash). Legendă RO/EN. Riglă de măsurat.

## Ordine de execuție și reguli
P1 → P2 → P6 (sâmbătă seara, în timpul corecturii din Marcaj) → P4 → P5 → P3 (duminică, după export).
După fiecare: rulează local, screenshot/descriere, commit + push, HANDOFF.md actualizat.
Nu hardcoda nimic specific Sireț3 în JS; totul vine din web/data.
