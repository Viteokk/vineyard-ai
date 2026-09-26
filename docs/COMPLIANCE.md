# Conformitate & compensații: Registrul vitivinicol (ONVV) + cadastru + AIPA

Idee de la mentori: inspectorul de stat verifică, pe imaginea din dronă și pe cadastru, dacă o plantație corespunde
cu ce e înregistrat și cu ce s-a cerut la subvenții. Platforma face comparația automat pe fiecare bloc și trimite
inspectorul doar unde e nevoie.

## Ce face platforma
| Pas | Modul | Date |
|---|---|---|
| Măsoară fiecare bloc din dronă: suprafață plantată (lungime rânduri × distanța mediană dintre rânduri), densitate nominală și efectivă (butuci/ha), goluri și butuci lipsă | `pipeline/compliance.py` | pipeline-ul nostru (aceleași cifre ca `measurements.csv`) |
| Suprapune parcelele cadastrale: nr. cadastral, mod de folosință, suprafață; vie detectată pe fiecare parcelă | `pipeline/cadastre.py` | **reale, publice**: ASP, WFS `cadastru_data:terenuri` pe geodata.gov.md (date la 01.01.2026) |
| Compară cu Registrul vitivinicol și cu cererea AIPA: înregistrare, IGP, soi, suprafață, densitate, integritate, sumă eligibilă | `pipeline/compliance.py` + `registry/criteria.json` | **demonstrative** (`registry/rvv_demo.json`, `demo: true`) sau un extras CSV real (`--registry-csv`, șablon `registry/rvv_template.csv`) |
| Status pe bloc: conform / de verificat / neconform / sub prag; traseu de control doar prin blocurile de vizitat; proces-verbal tipăribil | site, tab „Conformitate” | — |

Rezultat pe Sireț3 (registru demo): 70 de blocuri, 28 de vizitat, traseu de control de 9,1 km (față de 38,5 km turul complet
al golurilor). Cadastru (real): 676 de parcele în zona de studiu, 469 cu vie detectată, 43 cu peste 0,15 ha (înregistrare
RVV obligatorie), 21 cu vie pe teren înregistrat pentru construcții / locuințe (de verificat; unele sunt efect de margine
al conturului de bloc).

## Baza legală și surse (verificate 26.09.2026)
- **Registrul vitivinicol (SIA RVV)**, gestionat de ONVV din 2019: https://www.maia.gov.md/ro/content/1611
  - Înregistrare obligatorie pentru parcelele viticole > 0,15 ha (Legea 57/2006 a viei și vinului, art. 21(1)).
  - Regulament: HG 292/2017; formulare: https://rvv.gov.md/documents/overview.jsf
  - Căutare publică după IDNO/IDNP sau număr cadastral: https://rvv.gov.md/publicpart/check.jsf. Fișa publică arată
    soiul, suprafața utilă / totală, raionul și localitatea, fără geometrie. **Nu am găsit API sau set de date deschis.**
- **IGP**: Codru, Ștefan Vodă, Valul lui Traian și Divin; ariile au fost delimitate pe raioane prin Ordinul MAIA
  nr. 12/2016. Sireți (r. Strășeni) intră în IGP Codru. Contururi GIS oficiale nepublicate.
  https://wineofmoldova.com/ro/regiuni-vitivinicole/
- **Subvenții**: agenția plătitoare e AIPA; cadrul actual este Legea 71/2023 și HG 491/2023.
  - Măsura **SP_2.5 „Investiții în exploatații din sectorul viticol”** (plată postinvestiție) cere: extras RVV, numere
    cadastrale, proiect de înființare, certificat de categorie biologică, certificat de membru IGP/DOP.
    https://aipa.gov.md/masura-sp-2-5-investitii-in-exploatatii-din-sectorul-viticol/
  - Majorare IGP +20 mii lei/ha (fișa de calcul AIPA, nov. 2025).
  - Recepția plantației în primul an de vegetație cere prindere ≥ 90 %; de aici toleranța de 10 % goluri.
  - Benzile de densitate folosite (15 / 30 / 35 / 40 mii lei/ha pentru ≤ 3000 / 3001–3500 / 3501–4000 / > 4000 butuci/ha,
    minim 0,5 ha) sunt din regimul anterior (HG 455/2017): **valori de referință**. Sumele de bază din HG 491/2023 nu au fost
    verificate (legis.md indisponibil).
- **Teledetecție**: LPIS / IACS în dezvoltare (MAIA + AGCC + AIPA, bază geospațială până la sfârșitul lui 2026, conform
  Legii 126/2025). Nu am găsit prevederi care să accepte oficial dronele ca probă.
  https://agrobiznes.md/moldova-lanseaza-dezvoltarea-lpis-sistemul-care-va-eficientiza-platile-agricole.html

## Ce e real și ce e demonstrativ
| Real | Demonstrativ |
|---|---|
| Măsurătorile din dronă (suprafață, densitate, goluri) | Înregistrările RVV (cod, soi, an, suprafață declarată) |
| Parcelele cadastrale ASP (nr. cadastral, folosință, suprafață) | Cererile AIPA și sumele cerute |
| Regulile: prag 0,15 ha, IGP Codru, prindere ≥ 90 % | Lista de soiuri admise IGP (de confirmat cu caietul de sarcini) |

În producție, extrasul RVV și lista cererilor AIPA vin prin schimb de date cu ONVV / AIPA. Pe laptop: `python -m pipeline.serve`,
tab „Conformitate” → „Încarcă extras RVV / AIPA (CSV)”; conformitatea se recalculează imediat.

## Comenzi
```bash
python -m pipeline.cadastre            # parcele ASP pentru zona de studiu (o dată; --refresh pentru date noi)
python -m pipeline.compliance --visit-route             # registru demo + traseul de control
python -m pipeline.compliance --registry-csv extras.csv # extras real
```
Date cadastrale: ASP, Departamentul Cadastru, prin geodata.gov.md (serviciu fără taxe și fără restricții de acces declarate).
