# Plan final — sâmbătă 26 sept 07:10 → duminică 27 sept 15:00

Surse verificate: `03_docs/Vineyard_AI_Field_Challenge_description.pdf` (reguli + punctaj),
`Vineyard_AI_annotation_rules.pdf`, `Marcaj_quick_start_for_teams.pdf`, `Marcaj_Vineyard_AI_Visual_Journey.pdf`,
`PLAYBOOK_challenge.md`, `README-2.md`.

## 1. Ce cer regulile și unde stăm

| Punctaj | Criteriu | Stare |
|---|---|---|
| 25 % | Coroane (IoU + F1 pe tile-urile de referință ascunse, penalizare pe tile-uri fără vie) | detector clasic 0,587 pe exemple; YOLO canopy 0,562 |
| 10 % | Deșeuri (F1 la IoU ≥ 0,3) | 0 cutii automate; checklist 200 pe hartă; **model multi-clasă în antrenare** |
| 15 % | Axe rânduri 8 % + atribute 5 % + grupare 2 % | 0,961 / 0,970 / 1,0 pe exemple |
| 10 % | Numărători + arii + lungime | calculate din adnotările din Marcaj (măsurători gata) |
| 25 % | Traseu (acoperire 15 % + eficiență 10 %; 0 dacă > 2 % în afara zonei) | valid: 1,46 % în afară, 38,5 km |
| 15 % | Inginerie: arhitectură 5, robustețe 4, scalabilitate 3, performanță 3 + demo reproductibil | pipeline CLI, README, Docker (netestat) |

**Condiții de admitere:** interfață web funcțională, proiect Marcaj **publicat cu toate job-urile trimise (Submit)**,
formate și georeferențiere corecte.

**Ce cere interfața web:** trasee (cu lungime), `vineyard_id` / `row_id`, arii de coroane și de inter-rânduri,
numărul de blocuri și de rânduri, lungimea fiecărui rând și lungimea totală. Rezultatele precalculate sunt corecte:
procesarea e pipeline-ul, iar juriul poate cere o rulare din nou la demo („A demo from the team's laptop is accepted”).
Calculul live pe laptop (secțiunea 4) e un plus la inginerie și la produs, nu o condiție.

**Atenție la reguli:**
- „Manual annotation of Sireț3 is done only in Marcaj”. Bifele „E deșeu / Nu e” de pe hartă sunt doar un ajutor
  de căutare. **Nu le punem în ZIP-uri și nu antrenăm pe ele.** În pre-adnotări intră doar ce produce modelul.
- `--use-examples` rămâne oprit până confirmă mentorii pe Slack.
- Pe Marcaj corecturile se fac numai manual după Publish, iar la final **fiecare job trebuie trimis (Submit)**.

## 2. Modelul AI: unul singur, două clase

**Un singur YOLOv8n-seg cu clasele `0 vineyard` și `1 waste`.** Pornește de la greutățile noastre de coroane, ca
să nu piardă ce știa. Rânduri, inter-rânduri, atribute, ID-uri, măsurători și traseu rămân algoritmi care
folosesc ieșirea modelului: exact arhitectura cerută („AI/ML + classical computer vision + post-processing”).

| Date | Licență | Ce aduc |
|---|---|---|
| Crop-urile Sireț3 (`dataset/`: referința oficială + pseudo-etichete clasice), 3 037 | CC BY 4.0 | coroane; și fundal pentru deșeuri (tuburi albe, țăruși, sol deschis = NU deșeu) |
| DroneWaste v1.0 (numit în descrierea concursului), 893 cu deșeuri + 600 fundal | CC BY 4.0 | gropi de gunoi din dronă; scoase categoriile care NU sunt deșeu după reguli (vehicule, pământ, excavații, asfalt, zgură) |
| UAVVaste, 772 poze → 3 494 crop-uri | CC BY 4.0 | gunoi mărunt; micșorat ×0,25 / ×0,5 ca să aibă mărimea în pixeli de pe tile-uri (2,5 cm/px) |

Validare pe date nevăzute: siturile DroneWaste 13 + 17, setul oficial val + test UAVVaste, tile-ul canopy de validare.
Rulare: `train/make_multi_dataset.py` → `train/train_yolo.py --name multi` (15 epoci, oprire devreme). Gata ~09:20.

**Decizia după antrenare (09:20–09:50):**
1. `pipeline.eval` pe exemple: coroane multi vs clasic (0,817). Se păstrează ce dă scor mai mare.
2. Deșeuri: inferență pe 311 tile-uri (crop-uri 640 cu suprapunere), prag ales pe setul de validare (precizie ≥ 0,8).
3. Detecțiile se văd pe hartă; peste prag intră ca pre-adnotări `waste` în ZIP-uri; corectura se face în Marcaj.
   Dacă pe Sireț3 modelul dă mai ales fals pozitive: 0 cutii în ZIP, deșeurile se desenează manual în Marcaj.

**Alte moduri de antrenare, de păstrat pentru prezentare / după concurs:**
- **Duminică, runda 2:** re-antrenare pe exportul corectat din Marcaj (adnotări Sireț3 făcute legal în Marcaj):
  cea mai bună potrivire de domeniu, pentru greutățile finale.
- Zero-shot (Grounding DINO / OWL-ViT / YOLO-World cu „plastic bag, bottle, tyre”): fără antrenare, dar slab pe
  obiecte de 10–20 px și lent; bun doar ca propunător de candidați.
- SAM 2 + clasificator mic: contururi foarte bune la coroane, dar lent pe 311 tile-uri.
- Model mai mare (yolov8s-seg): ~2× mai lent, câștig incert la 2,5 cm/px.

## 3. Platforma pe roluri (cine intră și de ce are nevoie)

Ecran de intrare „Cine ești?” (se reține în browser; link direct `#inspector`, `#fermier`, `#agronom`):

| Rol | Ce vede implicit | Parametri |
|---|---|---|
| **Inspector de stat** (audit subvenții) | traseul albastru (goluri + deșeuri), blocuri, arii, rânduri disrupted, raport pe bloc | viteză de mers, ore pe zi → ture, prag gol (3 / 5 m) |
| **Fermier / administrator** | traseul roșu (deșeuri), starea blocurilor, butuci lipsă, costuri de replantare și întreținere (52–80 mii MDL/ha) | preț butaș, viteză, ore pe zi |
| **Agronom / analist** | toate straturile, măsurători, pipeline, sursa AI vs referință | toate |

- **Pe GitHub Pages (static):** variante precalculate (prag 3 / 5 m, ture pe zi pentru 4 / 6 / 8 h), alese din
  parametri. Nimic nu e hardcodat pentru Sireț3: totul se citește din `web/data/`.
- **Pe laptop (`python -m pipeline.serve`):** același site plus API local:
  - `POST /api/route {mode, min_gap, speed, hours, start}` → traseu recalculat live, în secunde, din matricea de distanțe din cache;
  - `POST /api/analyze` cu un GeoTIFF → detector + model → coroane, rânduri, deșeuri pe hartă („Analizează un tile”);
  - `GET /api/status` → site-ul afișează „calcul live” sau „rezultate precalculate”.

## 4. Orarul

### Sâmbătă, până la PUBLISH (termen 14:00, țintă 13:30)
| Ora | GPU | Claude (CPU) | Victor |
|---|---|---|---|
| 07:10–09:20 | antrenare multi | `pipeline/serve.py` (status, route, analyze) + ecranul de roluri + parametri pe site | **dry run Marcaj**: `out/upload_test/test_r021_c012_r006_c004.zip` → raport de import → Remove all |
| 09:20–09:50 | inferență 311 tile-uri | eval coroane multi vs clasic; prag deșeuri; `waste` în `pre_global.xml` | se uită pe hartă la detecțiile modelului (doar ca verificare vizuală) |
| 09:50–10:30 | liber | export final: `blocks` → `export_cvat` (9 ZIP-uri, validate) → commit | — |
| 10:30–12:30 | — | ture pe zi (P1) + raport pe bloc (P2) | upload 9 ZIP-uri unul câte unul, raportul fiecăruia citit, **Files = 311** |
| **12:30–13:30** | — | — | **PUBLISH** (o singură dată) |
| 13:30–23:00 | — | README, Docker, P6 (căutare, link partajabil, legendă RO/EN, riglă), pregătire pitch | corectură în Marcaj: tile-uri fără vie → coroane false → rânduri / atribute → deșeuri; **Submit la fiecare job** |

### Duminică
| Ora | Ce |
|---|---|
| până la 11:00 | corectura în Marcaj terminată; toate job-urile trimise |
| 11:00–12:00 | export Marcaj → `blocks` → `run.py --from targets` (ținte, ambele trasee, validate, measurements) → web → gh-pages |
| 12:00–13:00 | opțional runda 2 de antrenare pe exportul Marcaj (greutăți finale) |
| 13:00–14:30 | README final (timpi, hardware, greutăți, link web), `python -m pipeline.run --all` rulat curat, repetiție pitch |
| **≤ 15:00** | predare: linkul repo-ului |

## 5. Riscuri
- Antrenarea întârzie sau modelul nu ajută: PUBLISH nu așteaptă; ZIP-urile actuale (clasic, fără deșeuri) sunt gata.
- Corectura solo pe 311 tile-uri nu se termină: prioritate tile-urile fără vie (penalizare) și rândurile; Submit pe toate job-urile oricum.
- Calculul live e doar pe laptop: la demo pornim `pipeline.serve`; linkul public rămâne varianta statică.
