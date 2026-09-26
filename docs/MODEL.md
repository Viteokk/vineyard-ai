# Cum funcționează soluția AI (pentru mentori și juriu)

## Pe scurt
Un model neuronal găsește **obiectele** (coroane de vie și deșeuri). Algoritmi geometrici transformă obiectele în
**structură** (rânduri, inter-rânduri, blocuri, ID-uri), **măsurători** și **trasee**. Omul corectează în Marcaj,
iar corecturile se întorc în același pipeline și în antrenarea următoare.

```
GeoTIFF 2048×2048 px, 2,5 cm/px, EPSG:32635
   │
   ├─► MODEL (YOLOv8n-seg, 2 clase: vineyard, waste)  ─┐  „ce obiecte sunt în imagine”
   │     crop-uri 640 px cu suprapunere → măști → poligoane / cutii → deduplicare între crop-uri
   │
   ├─► GEOMETRIE CLASICĂ                               ─┤  „cum sunt organizate”
   │     vegetație (ExG) → orientarea rândurilor (FFT) → grilă periodică de rânduri → axa fiecărui rând
   │     → benzi inter-rând → atribute (regular / disrupted, bare_soil / vegetation / mixed)
   │     → filtre: vie vs livadă / pădure, zone forbidden
   │
   ├─► BLOCURI + ID-uri globale: rânduri legate peste marginile tile-urilor → vineyard_id, row_id
   ├─► EXPORT CVAT 1.1 → Marcaj (pre-adnotări) → corectură umană → export
   ├─► ȚINTE: goluri în rânduri ≥ 3 m + centrele deșeurilor
   ├─► TRASEE: graf pe inter-rânduri + drumuri (0,5 m), Dijkstra + TSP (OR-Tools), < 2 % în afara zonei
   └─► MĂSURĂTORI (measurements.csv) + HARTĂ WEB
```

## Modelul
- **Arhitectură:** YOLO11n-seg (Ultralytics 8.4), ~2,9 M parametri: detecție + segmentare de instanțe într-o singură
  trecere. (Prima versiune a fost YOLOv8n-seg; YOLO11 a ieșit mai bun: deșeuri mAP50 0,731 vs 0,654, coroane 0,717 vs 0,713.) „n” = varianta cea mai mică: rulează pe laptop, fără GPU dedicat.
- **Clase:** `0 vineyard` (coroana unui butuc, poligon) și `1 waste` (deșeu; din mască se ia cutia).
- **Intrare:** crop-uri de 640×640 px din tile-urile de 2048 px, la rezoluția nativă (o coroană ≈ 30 px,
  o sticlă ≈ 10–15 px), suprapunere de 128 px. Pe tile: 16 crop-uri; obiectele duble de la suprapuneri se elimină.
- **Ieșire:** pentru fiecare obiect: clasă, scor de încredere, contur. Pragul de încredere e ales pe validare.

## Pe ce date îl antrenăm
| Date | Câte | Licență | Ce învață din ele |
|---|---|---|---|
| Sireț3: tile-ul de referință oficial (×10) + pseudo-etichete de la detectorul clasic | 3 037 crop-uri | CC BY 4.0 | forma coroanelor la 2,5 cm/px; tuburile albe, țărușii, solul deschis NU sunt deșeu |
| DroneWaste v1.0 (recomandat în descrierea concursului) | 893 cu deșeuri + 600 fără | CC BY 4.0 | plastic, ambalaje, anvelope, moloz, butoaie, textile; excluse: vehicule, pământ, excavații, asfalt, zgură |
| UAVVaste | 772 poze → 3 494 crop-uri | CC BY 4.0 | gunoi mărunt împrăștiat; poze micșorate ×0,25 / ×0,5 ca gunoiul să aibă mărimea din tile-uri |

- **Pornire:** greutățile noastre de coroane (fine-tuning), nu de la zero.
- **Augmentări:** rotiri cu 90°, oglindiri, culoare, mozaic (din aer orice orientare e validă).
- **Validare** pe date nevăzute: siturile DroneWaste 13 + 17, setul oficial val + test UAVVaste, tile-ul r006_c004.
- **Hardware:** MacBook Pro M4 Pro (MPS), ~8 min pe epocă, 15 epoci cu oprire devreme.

**Pseudo-etichete:** pe Sireț3 nu există adnotări în afară de 2 tile-uri exemplu, așa că detectorul clasic
etichetează automat restul. Modelul învață din ele și din referința oficială. E o metodă standard (self-training).

## De ce model + algoritmi, nu doar model
- **Rândurile** sunt structuri lungi, periodice, care trec peste marginile tile-urilor: o grilă geometrică le
  găsește mai stabil (8 % axe: 0,961 pe exemple) decât o rețea antrenată pe 2 tile-uri.
- **ID-urile, măsurătorile și traseul** sunt calcule exacte, nu predicții: trebuie să fie reproductibile și verificabile.
- **Coroanele:** la final se păstrează sursa cu scor mai mare pe exemple (clasic 0,587 vs model), măsurat cu
  aceleași formule ca juriul (`pipeline/eval.py`).

## Omul în buclă (Marcaj)
1. Modelul + algoritmii produc pre-adnotările → ZIP-uri CVAT 1.1 → import în Marcaj.
2. Echipa corectează geometria și atributele **doar în Marcaj**, apoi trimite fiecare job.
3. Exportul corectat → blocuri / ID-uri → ținte → trasee → măsurători → site (o singură comandă).
4. Opțional: exportul corectat devine set de antrenare pentru runda 2 a modelului.

## Performanță (MacBook Pro M4 Pro)
Detector clasic pe 311 tile-uri: ~2 min. Traseu inspector: ~12 min (matrice de distanțe, cu cache).
Model: se completează după inferența pe 311 tile-uri.

## Întrebări pentru mentori (Slack / la masă)
1. Putem pune în pre-adnotări adnotările oficiale ale celor 2 tile-uri exemplu (`--use-examples`)?
2. Pre-adnotările de deșeuri generate de model sunt OK, chiar dacă unele sunt false și le ștergem în Marcaj?
3. Tile-urile din subsetul ascuns includ și zone de lângă sat / drumuri? (unde e cel mai probabil gunoiul)
4. Ce contează ca „zonă trecabilă” pentru traseu: doar `interrow_area` din adnotările noastre sau și capetele de rând?
5. Lista ascunsă de ținte: goluri de ce lungime minimă? (noi folosim ≥ 3 m)
6. La inginerie: e suficient demo-ul de pe laptop cu recalcularea live, sau vor să vadă și rularea completă?
7. Greutățile modelului pot fi în GitHub Release (link în README)?
