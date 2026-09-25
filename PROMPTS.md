# Prompturi pentru Claude Code (copiază-le în ordine)

Pornire (fiecare dev, în folderul repo-ului):
```
cd ~/Desktop/"VineYard project"/vineyard-ai
source .venv/bin/activate
git pull --rebase
claude
```
Primul mesaj, la amândoi:
> Citește CLAUDE.md și HANDOFF.md și rezumă-mi în 5 puncte ce avem de făcut, ce e critic și care e rolul meu. Nu scrie cod încă.

---
## DEV 1 — model AI + pre-adnotări (termen: sâmbătă ~14:00, țintă 12:00)

**D1.1 — îmbunătățire detector clasic**
> Rulează `python -m pipeline.baseline --tiles data/examples/images --out out/baseline.xml` și `python -m pipeline.eval --pred out/baseline.xml`. Îmbunătățește `pipeline/baseline.py` pe punctele 1–6 din diagnosticul din HANDOFF.md (coroane supra-fragmentate, capetele și poziția axelor, lățimea inter-rândurilor, lungimea rândurilor, pragul pentru disrupted, pragurile de interrow_cover). După fiecare schimbare rulează eval și arată-mi scorul înainte/după. Țintă: PARTIAL SCORE > 0.65. Nu supraadapta pe cele 2 tile-uri: schimbările trebuie să aibă sens după regulile de adnotare.

**D1.2 — preview vizual**
> Creează/extinde `pipeline/preview.py` care desenează predicțiile peste tile (coroane verzi, axe roșii/magenta pentru disrupted, inter-rânduri cyan, deșeuri portocaliu) și salvează PNG în `out/preview/`. Generează preview pentru cele 2 exemple și pentru 6 tile-uri variate din data/tiles (vie tânără, livadă, sat, margine de imagine). Arată-mi-le.

**D1.3 — rulare pe toate tile-urile + filtre**
> Rulează baseline-ul pe toate cele 311 tile-uri și măsoară timpul. Verifică pe preview-uri tile-urile fără vie: nu trebuie să apară coroane pe sat, livezi, pomi (coroane 2–4 m, la 4–6 m) sau iarbă. Adaugă filtre unde e nevoie. Raportează câte tile-uri au vie și câte sunt goale.

**D1.4 — dataset + antrenare YOLO-seg**
> Scrie `train/make_dataset.py`: pseudo-etichete din baseline pe tile-urile cu vie + cele 2 exemple oficiale supra-eșantionate ×10, tăieturi 640 px cu pas 512, ~10% tăieturi negative, format YOLO-seg; un tile exemplu ținut deoparte pentru validare. Apoi `train/train_yolo.py` cu ultralytics yolov8n-seg, imgsz 640, epochs 50, patience 10, max_det 300, device mps. Pornește antrenarea în fundal și spune-mi cât estimezi că durează.

**D1.5 — inferență YOLO + comparație**
> Scrie `pipeline/infer_yolo.py` (inferență cu suprapunere pe tile, unire, tăiere la marginea tile-ului) care înlocuiește doar coroanele din baseline. Compară pe eval: clasic vs YOLO vs combinat. Păstrează varianta cu scorul cel mai mare și notează rezultatul în HANDOFF.md.

**D1.6 — ID-uri globale**
> Scrie `pipeline/blocks.py`: lucrând în UTM pe tot mozaicul, grupează plantațiile în blocuri (același bloc dacă < 5 m distanță; un drum din passages.geojson separă mereu), dă vineyard_id V01, V02…; unește segmentele coliniare ale aceluiași rând din tile-uri vecine sub un singur row_id `Vxx-Rnnn`; vineyard_id la deșeuri doar dacă ≤ 10 m de un bloc. Verifică pe cele 2 exemple că numărul de blocuri și rânduri e corect.

**D1.7 — deșeuri**
> Scrie `pipeline/waste.py` cu un detector zero-shot (OWL-ViT sau Grounding DINO: plastic bag, bottle, tire, rubbish, plastic sheet) cu prag mare; exclude tuburile albe și țărușii de lângă vițe. Arată-mi toate detecțiile pe preview ca să le validez manual.

**D1.8 — export CVAT + validare**
> Scrie `pipeline/export_cvat.py`: câte un ZIP per part original din `data/parts.json`, cu `annotations.xml` (meta din cvat_io.py) + `images/` cu tile-urile neschimbate, fiecare < 90 MB. Adaugă o validare: 311 imagini, nume label/atribute exacte, fiecare row are vineyard_id + row_id + row_structure, fiecare interrow_area are interrow_cover, valori doar din listele permise. Generează și un ZIP de test cu cele 2 exemple.

**D1.9 — un singur cmd + timp**
> Fă `python -m pipeline.run --all` care rulează tot de la tile-uri la cele 5 ZIP-uri și scrie timpul total în out/timing.json. Actualizează README.md cu pașii și timpul măsurat.

---
## DEV 2 — traseu, măsurători, site (începe ACUM, pe exemple)

**D2.1 — date pentru site**
> Rulează `python -m pipeline.to_geojson --cvat data/examples/annotations.xml` și `python scripts/make_web_tiles.py`. Explică-mi ce fișiere au apărut în web/data și ce conțin (contractul din HANDOFF.md).

**D2.2 — site (Ecran 1: Hartă)**
> Construiește `web/index.html` + `web/app.js` (fără build, Leaflet din CDN) după Ecranul 1 din Figma (link în HANDOFF.md) și contractul de date: Leaflet cu L.CRS.Simple în metri UTM, fundal din tiles_index.json, straturi coroane / rânduri (cu popup row_id, lungime, structură) / inter-rânduri / deșeuri / passages / forbidden / START, panou dreapta cu cifrele din summary.json și tabelul de rânduri. Pornește-l cu `python -m http.server -d web 8000` și verifică în browser.

**D2.3 — ținte de inspecție**
> Scrie `pipeline/targets.py`: din rânduri și coroane (UTM) generează ținte cu ID, tip, X, Y, vineyard_id, row_id: goluri ≥ 5 m (mai multe puncte la ~4 m pe golurile > 10 m), goluri 3–5 m, obstacole în rând, porțiuni neevaluabile, plus centrele deșeurilor. Scrie `targets.geojson`. Testează pe exemple.

**D2.4 — traseu**
> Scrie `pipeline/route.py`: zona permisă = (interrow_area ∪ passage) − coroane − forbidden; grilă 0.25 m → graf 8-vecini (preferă centrul inter-rândurilor); aliniază fiecare țintă la cel mai apropiat punct permis la ≤ 2 m; matrice de distanțe; TSP cu OR-Tools START → ținte → START; lipește drumurile și simplifică fără să ieși din zonă. Scrie `route.geojson` (LineString, EPSG:32635, length_m, visited_targets).

**D2.5 — validare traseu**
> Scrie `pipeline/validate.py`: % din lungime în afara zonei permise (țintă < 0.5%, limita oficială 2%), distanța start/finish față de START (≤ 5 m), ținte neatinse la 2 m. Oprește exportul dacă o condiție pică.

**D2.6 — măsurători**
> Scrie `pipeline/measurements.py` → `measurements.csv` pe vineyard_id / row_id: nr. blocuri, nr. rânduri, lungime per rând și totală (m), arie coroane (reuniune) și inter-rânduri (m² și ha). Compară cu summary.json.

**D2.7 — site (Ecranele 2–4)**
> Adaugă în site tab-urile Traseu (moduri Inspecție/Deșeuri/Combinat, lungime, timp la 4 km/h, ținte atinse, rezultatele validării, lista țintelor), Măsurători (tabel pe blocuri + export CSV) și Pipeline (timpi, model, link weights, comenzi de reproducere), după Figma.

**D2.8 — deploy**
> Publică `web/` pe GitHub Pages (sau Vercel) și pune linkul în README.md. Verifică că merge pe alt calculator/telefon.

---
## DUMINICĂ — recalcul final (oricare dev)
> Am exportat adnotările din Marcaj în `out/marcaj_export.xml`. Rulează to_geojson, targets, route, validate, measurements pe acest export, copiază `route.geojson` și `measurements.csv` în rădăcina repo, actualizează web/data și README (timp, hardware, link weights, link site), apoi arată-mi checklist-ul final din HANDOFF.md bifat.

## Reguli de lucru cu Claude Code
- Un prompt = o sarcină. Cere mereu „rulează și arată-mi rezultatul”.
- După fiecare sarcină reușită: `git add -A && git commit -m "..." && git pull --rebase && git push`.
- Dev 1 lucrează în `pipeline/` (model) și `train/`; Dev 2 în `route.py`, `targets.py`, `validate.py`, `measurements.py`, `web/`.
- Dacă ceva important se schimbă (scor, decizie), cere-i: „actualizează HANDOFF.md”.
