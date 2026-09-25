# HANDOFF — context complet pentru Claude Code (citește după CLAUDE.md)

Echipa: Vikea (coordonare + Dev 1) și Dev 2. Hackathon DeepTech GigaHack 2026, Tekwill Chișinău.
Challenge ales: **Marcaj — Vineyard AI Field Challenge** (premiu 30.000 MDL, un singur câștigător).
Obiectiv: punctaj MAXIM, strict după regulile oficiale (`../03_docs/*.pdf` au prioritate).
Limba de comunicare cu echipa: română; cod/comentarii în engleză.

## Echipă (actualizat sâmbătă 00:30): Vikea face TOT (Dev 1 + Dev 2), cu Claude Code. Dev 2 nu e inclus momentan.
Sarcinile „Dev 2” de mai jos (targets, route ×2, validate, measurements, web) le face tot Vikea, în paralel cu antrenarea YOLO
(care rulează în fundal pe GPU/MPS — nu porni o a doua antrenare/inferență grea pe GPU în același timp).

## ⚠️ SCHIMBARE DE CERINȚĂ de la organizatori (vineri noapte) — DOUĂ TRASEE
Organizatorii (Marcaj) au ajustat sarcina: aplicația trebuie să genereze și să afișeze **două trasee**:
- **Traseu ALBASTRU — inspector de stat (AIPA)**: trece pe la TOATE țintele: butuci lipsă/morți (goluri) + deșeuri.
- **Traseu ROȘU — fermier**: traseul optim DOAR pentru colectarea deșeurilor (pe baza rezultatelor inspecției).
Ambele: START → ținte → START, doar pe interrow_area + passage, fără coroane/forbidden, EPSG:32635, cu length_m.
Implementare: `pipeline/route.py --mode inspector` → `route.geojson` (albastru, include tot → acesta e cel punctat
de „coverage of hidden targets: inspection locations AND waste”) și `--mode farmer` → `route_waste.geojson` (roșu).
Web: ambele polilinii simultan (albastru + roșu), fiecare cu lungimea, timpul la 4 km/h și țintele vizitate; toggle pe fiecare.
Întrebare deschisă pentru mentori: care fișier e punctat ca `route.geojson`? (presupunere: cel al inspectorului, ambele clase de ținte).
Mentori disponibili sâmbătă de la 9:00: organizatorul + mentor Oleg (business case, viziune, feedback UI/UX, AI/ML);
@x096a pentru blocaje tehnice / cod.

## Plan v2 pe ore, până la PUBLISH (sâmbătă 26 sept; detaliat în ~/.claude/plans/lucky-sleeping-papert.md)
Reguli: o singură persoană face tot; YOLO rulează pe GPU (MPS) și NU se oprește — în paralel doar sarcini CPU;
după fiecare sarcină: rulare → rezultat → commit + push.

| # | Sarcină | Stare (00:20) |
|---|---|---|
| a | commit `blocks.py` | făcut (`2508b0f`, `6f22c82`) |
| b | blocuri < 3 rânduri eliminate (105 → 70), hartă înainte/după | făcut în cod; de arătat harta |
| c | `export_cvat.py`: 9 ZIP-uri ≤ 58 MB, validate, 311 imagini byte-identice (`out/upload/`) | făcut; rămâne ZIP test cu exemplele |
| d | `pipeline/waste.py` — detector clasic, prag strict | de făcut |
| e | `targets.py` (1855 goluri ≥ 5 m) + `route.py --mode inspector/farmer` + `validate.py` | scrise; traseul albastru în calcul; modul farmer de adăugat |
| f | `measurements.py` → `measurements.csv` în rădăcină | script scris; de rulat |
| g | web: ambele trasee + ținte + cifre (lungime, timp la 4 km/h, ținte vizitate) | pregătit pentru un traseu; de extins |
| h | YOLO gata (~03:00–03:30, ep. 9/50, mask mAP50 max 0.741) → `infer_yolo.py` → `eval.py` vs 0.817 | așteaptă |
| i | dry run Marcaj → export final → upload 9 ZIP-uri → Files = 311 → PUBLISH ≤ 14:00 | sâmbătă dimineață |

| Ora | Ce |
|---|---|
| 00:20–00:45 | (b) hartă blocuri · (c) ZIP test · traseu albastru → `validate.py` → pe site · commit |
| 00:45–01:45 | (d) `waste.py` (CPU): pete luminoase compacte 0.05–4 m², nu pe axa rândului, nu tuburi albe · commit |
| 01:45–02:30 | (e) `route_waste.geojson`, `validate.py` pe amândouă · (f) `measurements.csv` · commit |
| 02:30–03:15 | (g) site cu 2 trasee simultan · commit · republicare link |
| 03:15–03:45 | (h) YOLO terminat: inferență pe exemple → `eval.py`; păstrăm ce e mai bun; rulare pe 311 tile-uri |
| 03:45–04:15 | `blocks.py` → `export_cvat.py` pe versiunea finală; regenerare web; commit. Somn. |
| 09:00–10:00 | (i) dry run: upload `part5of5.zip` (10 MB) → raport → **Remove all** |
| 10:00–11:00 | verificare vizuală a pre-adnotărilor finale; ultimele corecții |
| 11:00–12:30 | export final → upload 9 ZIP-uri, unul câte unul, raport citit la fiecare → **Files = 311** |
| 12:30–13:30 | rezervă |
| **≤ 14:00** | **PUBLISH** (o singură dată) → corectură în Marcaj |

## Stare sâmbătă 02:10 (după AUDIT_night1 P1–P4 și lista A–F)
- **Traseu inspector VALID** (`route.geojson`, `out/route_check_inspector.json`): 38,48 km, 1,46 % în afară (limita 2 %),
  START 0,0 m, 0 m prin coroane/forbidden, 2 421 / 3 513 ținte (69 %), 78 % din golurile ≥ 5 m. Bugetul intern e 1,7 %
  (măsurat exact ca validate.py). Acoperire ≥ 92 % **nu e posibilă** sub regula de 2 %: fiecare trecere între inter-rânduri
  costă ~0,5 m „în afară”; turul complet (3 345 opriri) iese la 2,8 %.
- Cauzele erorii inițiale (10 % în afară): rasterul cv2 umplea o celulă în plus pe două laturi + segmentele dintre celule
  tăiau colțurile poligonului → acum test exact Shapely pe centrele celulelor și mers doar la ≥ 0,35 m în interior
  (`grid-v5`), celule la < 0,36 m de coroane blocate.
- Timp traseu: ~5 min (matrice de distanțe paralelă 2 min, TSP 60 s, buclă de buget cu cache pe segmente); re-rulări
  pe aceleași detecții: ~3 min (cache `out/route_cache_inspector.npz`).
- Ținte: prag 3 m (recall 100 % pe exemple; la 5 m doar 62 %); 97 goluri de la capete de rând marcate `reachable=false`.
- Traseu fermier valid, gol (0 deșeuri automate; `out/waste_checklist.csv` = listă de verificat manual în Marcaj).
- Web: ambele trasee, ținte (de la zoom 0.5), validare, cifre; local `python -m http.server -d web 8000`.
- README, Dockerfile, `export_cvat.py --use-examples` (oprit până confirmă mentorii), release weights v0.1.
- **De făcut pentru Marcaj:** 1) dry run cu `out/upload_test/test_r021_c012_r006_c004.zip` → raport import → Remove all;
  2) upload cele 9 ZIP-uri din `out/upload/` unul câte unul (raport la fiecare) → Files = 311; 3) PUBLISH ≤ 14:00.
- **Repo PUBLIC + GitHub Pages LIVE: https://viteokk.github.io/vineyard-ai/** (branch `gh-pages` = conținutul `web/`;
  după orice schimbare: `git subtree push --prefix web origin gh-pages`). Linkul e în README.
- Web (03:00): export GPX per traseu (track + waypoints în ordinea turului, WGS84 prin proj4js, zona UTM din CRS-ul
  GeoJSON) și navigare GPS pe telefon (poziție, distanță și direcție până la următoarea țintă, „Verificat” în localStorage).
- SCORE_PLAN: S1 ✓ valid (acoperire ≥ 92 % imposibilă sub 2 %; varianta `--no-row-crossing` în test), S2 ✓ (prag 3 m,
  recall 100 % pe exemple), S3 ✓ planșa `out/preview/vine_filter_emptied.jpg` (de confirmat uman), S4 sweep dilatare/closing
  în curs (`out/s4_sweep.log`), S6 ✓ (0 rânduri duplicate, 0 blocuri tăiate de drumuri, 12 row_id sar peste un tile fără
  detecție — corect fizic), S7 ✓ `out/waste_checklist.csv` + `out/waste_checklist/README.md` (200 candidați cu miniaturi),
  S8 Dockerfile scris dar **docker nu e instalat pe acest Mac** (de testat pe alt calculator), README cu diagramă.
- PRODUCT_PLAN (P1 → P2 → P6 → P4 → P5 → P3) începe după upload-ul în Marcaj; P5 (GPX + GPS) e deja gata.

## Termene (ora Chișinăului)
| Când | Ce |
|---|---|
| Vineri seara/noaptea | detector clasic bun + pseudo-etichete + antrenare YOLO-seg |
| Sâmbătă dimineața | **dry run**: urcă ZIP-ul din `05_examples` în proiect, verifică importul, apoi Remove all |
| **Sâmbătă ≤ 14:00** (oficial „~14:00”; țintă internă 12:00) | inferență 311 tile-uri → ID-uri globale → export CVAT (5 ZIP) → upload → **PUBLISH (o singură dată!)** |
| Sâmbătă 12:00 – Duminică 11:00 | corectură manuală în Marcaj de toată echipa + Submit la toate joburile |
| Duminică 11:00–13:00 | export Marcaj → `measurements.csv` + `route.geojson` final |
| **Duminică 15:00** | predare: link repo (public) + joburi Marcaj trimise |

## Starea curentă a repo-ului (github.com/Viteokk/vineyard-ai, privat)
- `config.py` — căi (RAW = folderul părinte „VineYard project”), constante (0.025 m/px, labels, atribute).
- `scripts/setup_data.py` — dezarhivează 311 tile-uri în `data/tiles`, rute în `data/route`, exemple în `data/examples`, `data/parts.json` (ce tile e în ce part 1..5 → 74/71/78/76/12). **Rulat, OK.**
- `pipeline/tiles.py` — `open_tile()`, `Tile.px_to_utm()/utm_to_px()`, georef din tag-urile TIFF 33922/33550 (fără GDAL). Testat.
- `pipeline/cvat_io.py` — `read_cvat()` / `write_cvat()` CVAT 1.1 cu blocul `<meta>` copiat exact din exemplu. Round-trip testat (650 coroane, 51 rânduri, 49 inter-rânduri).
- `pipeline/eval.py` — reproduce scorul oficial pe cele 2 tile-uri exemplu (coroane 0.6·IoU+0.4·F1@0.5, deșeuri F1@0.3, axe 80%/0.4 m, atribute acc+macroF1, grupare, counts cu toleranțe). Ref vs ref = 1.000. `python -m pipeline.eval --pred out/X.xml`
- `pipeline/baseline.py` — detector clasic v0 (ExG > 0.10 → orientare dominantă prin FFT → rânduri = vârfuri de profil → coroane doar în benzi ±0.45 m de axă, tăiate la ~1.2 m → inter-rânduri între marginile coroanelor → row_structure (gol ≥ 5 m) → interrow_cover (% ExG > 0.05)). ~1 s/tile.
  `python -m pipeline.baseline --tiles data/examples/images --out out/baseline.xml`
- `requirements.txt` (intervale) + `requirements.lock.txt` (versiuni exacte, Python 3.12, Mac arm64: torch 2.14, ultralytics 8.4.123, numpy 2.2.6, opencv 5.0).

## Rezultatul baseline v0 pe exemple (PARTIAL SCORE 0.481)
```
canopy   IoU=0.412  F1=0.221  (pred 1064 / ref 650, TP 189)  -> 0.336
axes     F1=0.404  (pred 48 / ref 51, TP 20)
attrs    0.408
grouping 1.000
counts   blocks=1.00 rows=0.61 canopy_area=0.00 interrow_area=0.00 row_length=0.00
  pred: rows 48, canopy 401.9 m2, interrow 2772.4 m2, rowlen 1723.5 m
  ref : rows 51, canopy 536.2 m2, interrow 4064.4 m2, rowlen 1941.6 m
```
Diagnostic → ce trebuie îmbunătățit (în ordinea impactului):
1. **Coroane supra-fragmentate** (1064 vs 650): prea multe bucăți mici / tăieri. Crește `min_canopy_m2` (~0.12), unește fragmente apropiate (<0.3 m) pe același rând, taie doar blob-uri > ~1.8 m. Conturul: dilatare ușoară (referința e trasată larg, ±10 cm) → crește IoU.
2. **Axe** (TP 20/48): capetele rândului și poziția (toleranță 0.4 m, 80% mutual). Verifică: capetele să fie de la primul la ultimul butuc sau marginea tile-ului (referința merge adesea până la marginea tile-ului); axa refinată prin centroizii coroanelor, nu prin toți pixelii verzi. 3 rânduri lipsă.
3. **Aria inter-rânduri** subestimată (2772 vs 4064 m²): marginile coroanelor (percentila 85) sunt prea largi; referința merge de la marginea coroanei la marginea coroanei, lățime ~2.0–2.4 m, pe toată lungimea până la marginea tile-ului. Nu scădea toată reuniunea de coroane dacă creează crestături mari.
4. **Lungime rânduri** subestimată → aceeași cauză ca 2 (capete).
5. **row_structure**: calibrează pragul de gol (referința: r006_c004 are 5 `disrupted` din 26).
6. interrow_cover: r006_c004 are 4 `mixed` (benzi de iarbă) — calibrează pragurile.

## Baseline v1 (25 sept) — PARTIAL SCORE 0.817 pe exemple
```
canopy   IoU=0.613  F1=0.549  (pred 648 / ref 650, TP 356)  -> 0.587
axes     F1=0.961  (pred 51 / ref 51, TP 49)
attrs    0.970     grouping 1.000
counts   blocks=1.00 rows=1.00 canopy_area=0.93 interrow_area=1.00 row_length=0.99
```
Ce s-a schimbat (punctele 1–6): unghi fin = std maximă a profilului + perioadă din FFT cu zero-padding;
axe pe grilă periodică cu derivă (nu mai cad pe benzile de iarbă); rânduri prelungite până la marginea
imaginii (ca referința); mască `valid` fără găuri în umbre (era bug major); coroane = pixeli ExG>0.12 în banda
±0.3 m, opening 0.175 m, merge 0.3 m, dilatare 0.05 m, fără tăiere; inter-rând = bandă axă+0.3 → axă−0.3
tăiată de marginea tile-ului (referința e exact asta: 4064.4 vs 4062.0 m²); disrupted = cel mai lung gol
(inclusiv capetele) > 5.5 m; cover cu ExG>0.08. Preview: `python -m pipeline.preview --pred ... --tiles ... [--ref ...]`.
**Atenție:** pe 311 tile-uri (113 s, ~0.36 s/tile) detectorul pune rânduri pe 292/311 tile-uri, inclusiv
livezi, sat, câmpuri arate → trebuie filtru vie / non-vie înainte de export (penalizare coroane false).

## Update vineri noapte (după planul aprobat)
- **Filtru vie / non-vie** în `baseline.py` (`vine_rows`): un rând e pom/livadă/pășune dacă vegetația iese mult în afara
  benzii ±0,3 m (`spill` ≥ 0,7) ȘI coroanele sunt mari (mediana ≥ 0,8 m²). Tile-urile fără ≥ 3 rânduri de vie consecutive
  se golesc; în tile-urile cu vie se scot doar rândurile clar de pom (spill ≥ 0,85 și ≥ 1,5 m²) — mai ușor de șters în
  Marcaj decât de desenat. Rezultat: 292 → 194 tile-uri cu detecții, 5161 → 3262 rânduri; scor exemple neschimbat 0,817.
  De reverificat în Marcaj (posibil vie tânără pe iarbă): r031_c018, r037_c024, r026_c019, r029_c019.
- **Filtru forbidden**: obiectele > 50% în `forbidden.geojson` se scot (`--no-forbidden` îl dezactivează).
- **YOLO**: `dataset/` construit (3037 crop-uri train, 16 val); antrenare `train/train_yolo.py --name canopy` pornită în
  fundal pe M4 Pro (MPS), log `out/train_canopy.log`, weights în `runs/vineyard/canopy/weights/best.pt`.
- **Web**: `web/index.html` + `web/data` (mozaic 10 cm/px, hi-res pe exemple, pred/ + referință) sunt în repo;
  local: `python -m http.server -d web 8000`. Regenerare: `scripts/make_web_tiles.py` → `scripts/build_web_map.py`.
- Planul complet de acum până duminică: vezi `/Users/victoristrati/.claude/plans/lucky-sleeping-papert.md` (rezumat:
  blocks.py → export_cvat.py cu ZIP-uri împărțite < 85 MiB → dry run → PUBLISH ≤ 12:00; Dev 2: targets/route/validate/
  measurements). **Atenție: ZIP-urile părților 1–4 au deja 93–94 MB fără adnotări → trebuie împărțite.**

## Parametri măsurați pe referință (folosește-i)
- distanță axă–axă: 2.53–2.78 m (mediană); coroană: mediană ~0.5 m² (p10 0.25, p90 1.1–1.9 m²)
- centrul coroanei la ~5 cm de axă; distanța dintre coroane pe rând: mediană 2–3 m (lipsesc butuci)
- ExG (=(2G−R−B)/(R+G+B)): coroane mediană 0.18–0.20 (p5 0.065); sol inter-rând 0.00–0.03
- ExG>0.10 pe pixeli: IoU ~0.45–0.49 (fără filtrul de rânduri)
- r021_c012: V01, 25 rânduri regular, 399 coroane, 24 bare_soil; r006_c004: V02, 26 rânduri (5 disrupted), 251 coroane, 21 bare_soil + 4 mixed.

## Plan tehnic rămas (ordinea de lucru)
### Dev 1 — model & pre-adnotări (termen: sâmbătă ~14:00, țintă 12:00)
1. Îmbunătățește `baseline.py` pe `eval.py` (punctele 1–6 de mai sus). Țintă: partial > 0.65.
2. Rulează pe toate 311 tile-urile; verifică vizual câteva (overview + preview-uri) — ATENȚIE la tile-uri fără vie (sat, livezi): coroanele false sunt penalizate. Pomi: coroană 2–4 m, la 4–6 m → filtrați.
3. `train/make_dataset.py`: pseudo-etichete baseline + cele 2 exemple (supra-eșantionate) → YOLO-seg, tăieturi 640 px pas 512, ~10% negative. Validare pe un tile exemplu ținut deoparte.
4. Antrenare `yolov8n-seg` / `yolo11n-seg`, imgsz 640, epochs 50, patience 10, max_det 300+. Pe Mac: `device="mps"` (~2–4 h) sau Kaggle T4 (~1 h). Păstrează ce iese mai bine pe `eval.py` (clasic / YOLO / combinat). Weights → link în README (fără .pt în git).
5. `pipeline/blocks.py`: ID-uri GLOBALE în UTM peste tot mozaicul — blocuri (plantații la < 5 m = același bloc; un drum din `passages.geojson` separă mereu), `row_id` comun pentru segmentele aceluiași rând din tile-uri vecine (`V03-R017`). Vineyard_id la deșeuri dacă ≤ 10 m de bloc.
6. `pipeline/waste.py`: detector zero-shot (OWL-ViT / Grounding DINO: „plastic bag, bottle, tire, rubbish”), prag mare; NU tuburi albe/țăruși. Fals pozitiv costă cât o ratare.
7. `pipeline/export_cvat.py`: câte un ZIP per part original (`data/parts.json`): `annotations.xml` + `images/` cu tile-urile neschimbate; < 90 MB; validare nume label/atribute; 311 imagini. (Întrebare deschisă: putem înlocui cele 2 tile-uri exemplu cu adnotările oficiale? — de confirmat pe Slack.)
8. Upload în Marcaj part cu part, citește raportul de import, Files = 311 → PUBLISH.

### Dev 2 — traseu, măsurători, web
1. `pipeline/route.py`: zona permisă = (passages ∪ interrow_area) − forbidden − coroane; grilă 0.25 m → graf 8-vecini; ținte = centrele golurilor ≥ 5 m din rânduri (puncte de inspecție: ID, X, Y, vineyard_id, row_id) + centrele deșeurilor; aliniere la cel mai apropiat punct permis (≤ 2 m); matrice distanțe → TSP OR-Tools START→...→START; lipire + simplificare fără ieșire din zonă. Mers pe mijlocul inter-rândului (~1.35 m de axă) acoperă golurile din rândurile vecine.
2. `pipeline/validate.py`: % lungime în afara zonei (țintă < 0.5%, limita oficială 2%), start/finish ≤ 5 m de START, ținte neatinse.
3. `pipeline/measurements.py` → `measurements.csv`: nr. blocuri, nr. rânduri, lungime per rând și totală (m), arie coroane (reuniune) și inter-rânduri (m² și ha), pe vineyard_id / row_id.
4. `pipeline/import_marcaj.py`: export Marcaj (CVAT XML) → UTM (pentru recalcul final duminică).
5. `web/`: Leaflet + proj4js (EPSG:32635 → WGS84); fundal ortofoto; straturi coroane / rânduri / inter-rânduri / deșeuri / ținte / traseu; panou cu lungime traseu, ținte vizitate, arii (m² + ha), nr. blocuri și rânduri, tabel rânduri (row_id, lungime, vineyard_id), lungime totală; selector Inspecție / Deșeuri / Combinat. Mockup de urmat: slide 8 din `../Marcaj_Vineyard_AI_Visual_Journey.pdf`. Deploy: GitHub Pages / Vercel.
6. Poate începe ACUM pe adnotările din `data/examples/annotations.xml`.

## Strategia pentru ținte de inspecție (lista organizatorilor e ascunsă și poate conține și alte situații)
Punctaj traseu: acoperire max 15% (țintă atinsă dacă traseul trece la ≤ 2 m) + eficiență max 10%, acordată DOAR de la 90% acoperire.
→ **Recall-ul țintelor e mai important decât lungimea.** O țintă în plus costă câțiva metri; o țintă ratată costă acoperire (și poate tot bonusul de eficiență).
Candidați (fiecare cu ID, X, Y, vineyard_id, row_id, tip):
1. `gap` — gol în rând ≥ 5 m (sigur); punct la mijlocul golului, pe axa rândului.
2. `short_gap` — gol 3–5 m (câțiva butuci lipsă) — „potentially missing planting”.
3. `obstacle` — pom/obstacol în rând (componentă verde mare pe axa rândului).
4. `missing_row_part` — rând mult mai scurt decât vecinii / porțiune de bloc fără plantă.
5. `unassessable` — porțiuni de rând nevizibile (umbră, buruieni înalte).
6. `waste` — centrul fiecărei cutii de deșeu.
Pentru golurile lungi (> 10 m) pune mai multe puncte (la ~4 m), ca să fie acoperită orice poziție aleasă de organizatori.
Traseul trece pe mijlocul inter-rândului vecin (~1.35 m de axă) → acoperă țintele de pe ambele rânduri vecine.
Calibrare: verifică pe `data/examples` câte ținte generează fiecare tip și cât crește traseul; decide pragurile finale după răspunsul de pe Slack.

## Predare (rădăcina repo)
`route.geojson` (un LineString, EPSG:32635, `length_m`, start=finish ≤ 5 m) · `measurements.csv` · `README.md` (instalare, rulare, dependențe fixate, link weights, timp de procesare pe 311 tile-uri + hardware (MacBook Pro Apple Silicon), API-uri plătite: niciunul, link web) · cod · Dockerfile (bonus).

## Instrucțiuni oficiale de onboarding (primite vineri seara) — confirmări
- Login: marcaj.com/login (credențiale din e-mail); proiectul echipei și label-urile sunt deja configurate.
- **Dry run obligatoriu**: urcă ZIP-ul exemplu, verifică importul, apoi șterge-l — înainte de upload-ul real.
- Import + Publish țintă: **sâmbătă ~14:00**; după Publish nu adăugăm/ștergem tile-uri și nu schimbăm label-urile.
- Traseu: **graf peste interrow_area + passage, evitând vineyard (coroane) + forbidden**; vizitează țintele de inspecție și deșeurile; întoarcere la start ±5 m; EPSG:32635. Se dezvoltă pe adnotările exemplu. (Neclar: la punctare se verifică pe inter-rândurile noastre sau pe referință → mergem pe mijlocul inter-rândului.)
- Web: hartă cu traseul și lungimea lui, coroane, inter-rânduri, ID-uri, numărători și lungimi de rânduri.
- Sâmbătă: toată echipa adnotează; tile-uri vecine la aceeași persoană; Submit la toate joburile (nesubmis = 0).
- Duminică dimineața: export din Marcaj → recalcul `route.geojson` + `measurements.csv`; README; pitch 5 min cu demo live. 15:00 repo + Marcaj înghețate.

## Întrebări deschise pentru Slack (canalul challenge-ului)
1. Verificarea „max 2% în afara zonei” la traseu se face pe inter-rândurile NOASTRE din Marcaj sau pe cele de REFERINȚĂ? (onboarding-ul spune să construim graful peste interrow_area + passage, dar nu spune ce se folosește la punctare). Până la răspuns: traseul merge pe MIJLOCUL inter-rândurilor și pe passages — sigur în ambele cazuri.
2. Putem folosi/importa adnotările din `05_examples` (antrenare / pre-adnotare)?
3. Ce conține exact lista de ținte de inspecție ascunsă? (pe lângă golurile ≥ 5 m și deșeuri — ce alte situații: goluri mai scurte, pomi în rând, rânduri lipsă, zone neevaluabile? unde e plasat punctul: mijlocul golului pe axa rândului?)

## Precizări verificate în documente
- Modelul neural: cerut în brief și la predare („neural-network model weights or a reproducible way to obtain them”); NU e în condițiile de admitere, dar îl livrăm obligatoriu.
- Condiții de admitere: interfață web funcțională + proiect Marcaj publicat cu joburi trimise + formate și georeferențiere corecte.
- `measurements.csv` NU se punctează direct: valorile punctate se calculează de organizatori din adnotările noastre din Marcaj; CSV-ul e arătat juriului.
- Punctele de inspecție (ID, coordonate, vineyard_id, row_id) NU intră în Marcaj; sunt output al aplicației (ex. `targets.geojson` + afișate în web).
- Pitch-ul nu are punctaj separat; juriul dă 15% pe inginerie (arhitectură 5, robustețe 4, scalabilitate 3, performanță măsurată 3) pe baza demo-ului și a README-ului.
- Departajare la egalitate: traseu, apoi coroane, apoi votul juriului.

## Riscuri de evitat
- Publish înainte de toate 5 part-urile / fără pre-adnotări → nu se mai pot importa.
- Joburi nesubmitate la 15:00 duminică → neadnotate.
- Rânduri renumerotate per tile → număr de rânduri greșit. Blocuri noi per tile → grupare greșită.
- Coroane pe tile-uri fără vie → penalizare. Traseu > 2% în afara zonei → 0 pe 25%.
- `git push --force` după ce lucrează amândoi — interzis. `git pull --rebase` înainte de push.

## Contract de date pentru site (Dev 2 construiește web-ul în paralel cu modelul)
Site-ul citește DOAR fișiere din `web/data/` — nu depinde de model. Când modelul/Marcaj produc date noi, se regenerează fișierele, site-ul rămâne neschimbat.
- Generare: `python -m pipeline.to_geojson --cvat <annotations.xml>` (acum pe `data/examples/annotations.xml`; sâmbătă pe predicții; duminică pe exportul Marcaj).
- Fundal ortofoto: `python scripts/make_web_tiles.py` → `web/data/tiles/*.jpg` (512 px = 0.1 m/px) + `web/data/tiles_index.json` [{name, img, bounds:[minx,miny,maxx,maxy]}]. Extent: 628992,5219174 → 630733,5220966.
- Toate coordonatele sunt în **EPSG:32635 (metri)**. Hartă: **Leaflet cu `L.CRS.Simple` în metri UTM** (lat = Y nord, lng = X est), cu o transformare cu offset ca să evităm numere mari, ex.:
  `const crs = L.extend({}, L.CRS.Simple, {transformation: new L.Transformation(1, -628990, -1, 5220970)});`
  → imaginile (`L.imageOverlay(img, [[miny,minx],[maxy,maxx]])`) și GeoJSON-urile (`coordsToLatLng: c => L.latLng(c[1], c[0])`) se aliniază exact, fără reproiectare și fără basemap OSM.
- Fișiere:
  - `canopies.geojson` (Polygon): vineyard_id, tile, area_m2
  - `rows.geojson` (LineString): vineyard_id, row_id, row_structure, tile, length_m (segment din tile)
  - `interrows.geojson` (Polygon): vineyard_id, interrow_cover, tile, area_m2
  - `waste.geojson` (Polygon): waste_id, vineyard_id, tile, cx, cy
  - `start.geojson`, `passages.geojson`, `forbidden.geojson`, `study_area.geojson` (de la organizatori)
  - `summary.json`: blocks, block_ids, rows, total_row_length_m, canopy_area_m2/ha, interrow_area_m2/ha, canopy_count, waste_count, row_lengths[{row_id, vineyard_id, length_m}] (lungimea unui rând = suma segmentelor din toate tile-urile)
  - DE ADĂUGAT de Dev 2: `route.geojson` (LineString, length_m, visited_targets) și `targets.geojson` (Point: target_id, type, vineyard_id, row_id, visited)
- Canopies pe toată zona vor fi zeci de mii de poligoane → afișează-le doar la zoom mare (sau `preferCanvas: true`).

## Machete site + business flows (Figma / FigJam)
https://www.figma.com/board/OUUTJ6fjQehASrDNjiVvgX/Case-management?node-id=3622-356
- Flow A: pipeline end-to-end (dronă → model → ID-uri → CVAT → dry run/upload → PUBLISH → corectură Marcaj → export → măsurători/traseu → validare → predare)
- Flow B: utilizatorul în Field Planner (hartă → bloc → cifre → mod Inspecție/Deșeuri/Combinat → traseu → teren → raport)
- Ecran 1 Hartă + măsurători · Ecran 2 Traseu (moduri, validare, listă ținte) · Ecran 3 Măsurători pe blocuri · Ecran 4 Pipeline & reproducere
- Checklist „Ce cere juriul” (ce TREBUIE să arate site-ul, admitere, predare, punctaj)

## Antrenare YOLO-seg (scripturi gata)
- `python train/make_dataset.py` → `dataset/` (gold: r021_c012 ×10 în train, r006_c004 = val; pseudo-etichete din baseline pe restul, cache în `out/baseline_all.xml` — ȘTERGE-L după ce îmbunătățești baseline-ul, ca să se regenereze). Testat: etichetele se aliniază corect (out/label_check.jpg).
- `python train/train_yolo.py --epochs 1 --fraction 0.1` = test rapid; `python train/train_yolo.py` = antrenare completă (mps, patience 10, augmentări rotație/flip).
- `python train/watch.py` într-un al doilea terminal = tabel live (loss ↓, mask mAP50 ↑). Grafice: `runs/vineyard/canopy/results.png`; predicții pe validare: `val_batch*_pred.jpg`.

## Audit complet folder (vineri ~23:00) — ce lipsește, în ordinea priorității
1. **`pipeline/blocks.py` nu e în git** (untracked) → commit imediat.
2. **Blocuri: 105, din care 38 < 500 m²** (fragmente de 1–2 rânduri, ex. V005–V009, V019–V025, V049–V066). Referința cere vie cu ≥ 3 rânduri; fragmentele umflă numărul de blocuri (2%) și pot fi coroane false (25%). De făcut: elimină blocurile cu < 3 rânduri (sau le lipește de blocul vecin dacă sunt la < 5 m pe aceeași direcție) și verifică vizual pe web. Harta blocurilor: out/blocks.geojson.
3. **Export CVAT lipsește** (`pipeline/export_cvat.py`) + **ZIP-urile trebuie împărțite** (părțile 1–4 au deja 93–94 MB fără adnotări; limita e 90 MB) → ~10 ZIP-uri a câte ~30–35 tile-uri, fiecare cu annotations.xml propriu. Apoi dry run în Marcaj.
4. **Deșeuri (10%)** — nimic încă. Minim: detector simplu (obiecte albe/foarte luminoase, compacte, nu pe axa rândului) cu prag strict, restul la corectura manuală.
5. **Traseu (25%) + ținte + validare + measurements** (Dev 2) — nu există încă în repo. CRITIC: traseul valorează cât coroanele.
6. YOLO: antrenare în curs (epoca 1: mask mAP50 = 0.665, ~3.2 min/epocă → ~2.5 h). Când termină: `infer_yolo.py` + comparație pe eval.py cu baseline 0.817.
7. README final, link weights, timp procesare, deploy web (GitHub Pages).

## Extra pentru site (valoare pentru utilizatorul din vie) — după ce cerințele obligatorii sunt gata
Ordinea = raport valoare / timp. Toate sunt front-end static (fără server, fără AI), se pot face în paralel cu corectura din Marcaj.
1. **Export GPX** (30 min): buton care descarcă route.geojson / route_waste.geojson ca GPX (track + waypoints = ținte), pentru Gaia/OsmAnd/Garmin.
2. **Navigare GPS pe telefon** (1 h): `navigator.geolocation.watchPosition` → punctul utilizatorului pe hartă (UTM → din WGS84 cu proj4js), distanța până la următoarea țintă nevizitată din ordinea traseului, buton „am verificat” (stare în localStorage).
3. **Starea blocurilor** (1 h): fiecare bloc colorat după % rânduri disrupted (verde < 10 %, galben 10–30 %, roșu > 30 %); panou „blocuri care au nevoie de atenție”.
4. **Costuri** (1 h): butuci lipsă estimați (din goluri / distanța de plantare ~1.2 m) × preț butaș (parametru editabil, implicit 15 MDL) = cost replantare pe bloc; ha × 52 000–80 000 MDL = cost întreținere anual (cifrele din brief-ul Marcaj).
5. **Căutare după ID** (30 min): input „V02-R017” → zoom pe rând/bloc + fișa lui (lungime, structură, bloc).
6. **Link partajabil** (30 min): starea (bloc selectat, straturi, zoom) în URL hash.
7. **Legendă + explicații RO/EN** (30 min): ce înseamnă disrupted / bare_soil / mixed / țintă / passage; cine folosește fiecare traseu.
8. **Rigla de măsurat** (15 min): distanță între două click-uri pe hartă.
9. **Raport PDF de audit** (1–2 h, doar dacă rămâne timp duminică): hartă bloc + cifre + lista țintelor cu coordonate + data zborului, generat în browser (window.print cu CSS de print).
