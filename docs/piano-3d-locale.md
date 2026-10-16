# Piano di implementazione — 3D reale in locale, deploy su AMD

**Redatto:** 16 ottobre 2026 · **Consegna:** 18 ottobre 2026 · **Si appoggia a:** [piano generale](../safe-home-implementation-plan.md), [stato runtime](runtime.md), [mappa interattiva](roadmap.html)

## Decisioni già prese

- Sviluppo e prove **in locale** su NVIDIA RTX 5000 Ada Laptop (16 GB, sm_89). Il deploy finale su **AMD Developer Cloud (MI300X, ROCm)** lo gestisce il team, con lo stesso codice.
- **Gemma via API esterna** compatibile OpenAI, non vLLM locale.
- Ricostruzione: **VGGT-1B**, senza bundle adjustment. Il BA è solo la prima leva se lo splat esce sfocato.
- Le misure sono **geometriche** (depth + scala ArUco). I LLM non producono numeri.
- Ordine di valore: **VGGT reale → box 3D → scala ArUco → viewer → report**. Lo splat gsplat viene dopo ed è opzionale.

## Vincoli della macchina locale

| Risorsa | Valore | Conseguenza |
|---|---|---|
| GPU | 16 GB VRAM | VGGT fino a circa 50 frame in bf16; per una stanza ne bastano 15–20 |
| Disco | 428 GB liberi dopo la pulizia del 16/10 | Nessun vincolo; Mip-NeRF 360 scaricabile se serve |
| RAM | 31 GB (circa 8 liberi al momento) | Chiudere i processi pesanti durante VGGT/gsplat |
| Software | Niente PyTorch, niente `nvcc` | Wheel PyTorch CUDA nel `.venv`; il toolkit CUDA serve solo se gsplat va compilato |

---

## Fase 0 — Ambiente (circa 1 h) · bloccante

1. ~~Liberare almeno 15 GB di disco~~ — fatto (16/10).
2. Creare il `.venv` come da README (`requirements.lock`, `pip install --no-deps -e .`), `npm --prefix web ci`, poi verificare che i **22 test** passino.
3. Installare PyTorch con una wheel CUDA 12.x compatibile con sm_89 e il driver 580 (`pip install torch torchvision --index-url https://download.pytorch.org/whl/cu12X`).
4. Clonare VGGT **fuori dal pacchetto**, in `model-cache/vggt` (già in `.gitignore`), al commit già validato dal team `a288dd0f14786c93483e45524328726ab7b1b4ce`, poi `pip install -e` senza far sostituire torch.
5. Scaricare a mano `model.pt` di `facebook/VGGT-1B` alla revisione `860abec7937da0a4c03c41d3c269c366e82abdf9` in `model-cache/`, verificare lo SHA256 `d15bf50a8615c8225ed48b51ea5cac673d82442ec0309036df555a053253afe0` e impostare `VGGT_CHECKPOINT`, `VGGT_COMMIT`, `VGGT_CHECKPOINT_SHA256`. L'adapter non scarica nulla da solo.

> **Già fatto dal team (commit `e3e1a8c`):** VGGT ha girato **su una sola immagine** con `live_scene`, su RTX 5090 Laptop (24 GB), PyTorch `2.8.0+cu128`: 13,8 s, 7,02 GiB di picco. Anche Jev è stato provato dal vivo, con una domanda generica sui pericoli. Restano da fare il multi-vista, la scala, la pipeline completa e AMD; vedi [runtime](runtime.md#local-single-image-vggt-experiment). Lo script `scripts/render_vggt_image.py` è un buon punto di partenza per la fase 2.
6. Registrare versioni, commit e hash in `docs/runtime.md`.

**Gate:** `python -c "import torch; print(torch.cuda.is_available())"` stampa `True` e `scripts/gpu_smoke.py` non segnala più l'assenza della GPU. Il preflight è scritto per AMD: segnare a parte cosa è NVIDIA locale.

## Fase 1 — Cattura reale (circa 30 min) · in parallelo alla fase 0

1. Stampare un marker ArUco (dizionario `DICT_4X4_50`, id 0, lato circa 15 cm) e **misurare il lato stampato** con il righello.
2. Allestire la stanza: tappeto, cavo che attraversa il passaggio, un ostacolo, una zona volutamente non visibile.
3. Fare 15–20 foto all'altezza del petto lungo il perimetro, con il marker visibile in almeno 5 foto, su superficie piana. Fare 2–3 **misure col metro**: larghezza del passaggio, lato del tappeto, distanza tra due mobili.
4. Scrivere le etichette **prima** di chiamare i modelli: presenza, box approssimative, copertura.
5. Creare il manifest reale (`data_kind: "staged_capture"`, checksum, `tape_measurements`, `calibration` con il lato del marker) e il caso `demo/cases/room-002.json`. Il caso deve richiedere scala calibrata (non più `unknown-scale`), pin supportati per tappeto, cavo e ostacolo, e l'errore di scala rispetto al metro sotto una soglia da decidere prima, per esempio 5%.

Le foto restano in `data/` (gitignored). Nel repo vanno solo manifest, checksum ed etichette.

**Gate:** il manifest si risolve e i checksum tornano. `case run room-002 --mode live` non è più bloccato per "synthetic dataset".

## Fase 2 — VGGT reale in locale (circa 1–2 h)

1. Lanciare `RECON_BACKEND=vggt` sul solo stage di ricostruzione: `case run demo/cases/room-002.json --mode live --stage reconstruct` dopo `prepare`. Verificare che il runner accetti lo stage isolato in live; se no, correggere il runner.
2. Controllare a occhio nel viewer: nuvola di punti riconoscibile, posizioni delle camere lungo il percorso reale, pareti non doppie.
3. Registrare nel manifest picco di VRAM e tempo (già misurati dall'adapter).
4. Se la qualità è scarsa: alzare la soglia di confidenza, togliere foto mosse o con specchi, rifare la cattura. Non cambiare modello.

**Gate:** scena reale `ready` con nuvola di punti credibile. Da qui in avanti tutto si sviluppa riusando questo output (`--reuse-run`), senza rieseguire VGGT.

## Fase 3 — Modelli via API (circa 1 h)

**Gemma:**
1. Aggiungere `GEMMA_API_KEY` a `config.py` e inviare `Authorization: Bearer …` in `gemma.complete()`. Oggi `post_json` parte senza header perché era pensato per un vLLM interno. Il valore non deve mai finire in log o manifest.
2. Gestire il caso in cui il provider restituisce un nome modello diverso da quello richiesto. Oggi il client rifiuta la risposta. Aggiungere una variabile esplicita per il nome restituito atteso, senza disattivare il controllo.
3. Verificare che il provider supporti **immagini** e `response_format: json_schema`.
4. Aggiornare il confine di privacy: il profilo ora esce verso un servizio esterno. Aggiornare il testo del consenso nell'intake, `docs/runtime.md` e `.env.example`.
5. Aggiungere un test offline con trasporto simulato: header presente, chiave assente dai file di audit.

**Jev:** `JEV_ENABLED=true` più `JEV_API_KEY`, con il consenso esterno spuntato nell'intake.

**Gate:** `case run room-002 --mode live` completa `jev` e `gemma` riusando la ricostruzione della fase 2; `case check` mostra i risultati reali.

## Fase 4 — Geometria: pavimento, scala, box 3D (circa 4–5 h) · il cuore del lavoro

Tutto in `pipeline/geometry.py`, più un nuovo modulo `pipeline/calibration.py` se diventa lungo. Si sviluppa e si testa **su CPU** con array sintetici.

**4a. Pavimento e gravità**
- RANSAC su punti ad alta confidenza per trovare il piano dominante orizzontale. Tra i piani candidati scegliere quello sotto le camere, cioè il pavimento e non una parete.
- Calcolare la rotazione che porta la normale del pavimento su +Y e salvarla nel manifest della scena come `display_transform`, che il viewer già applica.
- Test: nuvola sintetica con piano inclinato noto → normale recuperata entro 2°.

**4b. Scala da ArUco**
- Nuova dipendenza: `opencv-contrib-python-headless`, da aggiungere a `pyproject.toml` e `requirements.lock`.
- Per ogni foto con il marker: trovare i 4 angoli sull'immagine originale, portarli nello spazio 518 con `scale_xy`/`offset_xy` (come fa già `geometry`), leggere i `world_points` attorno agli angoli (mediana in una piccola finestra, solo pixel ad alta confidenza) e calcolare la lunghezza dei 4 lati in unità VGGT.
- Fattore di scala = lato reale / mediana dei lati su tutte le foto. Dispersione tra le foto = incertezza dichiarata.
- Compilare i campi già presenti nel manifest della scena: `scale_state: "calibrated"`, `scale_factor`, `calibration` (foto usate, dispersione). Con meno di 2 foto valide: resta `unknown`.
- Confronto automatico con le `tape_measurements` del manifest, come check del caso.
- Test: marker sintetico proiettato con scala nota → fattore recuperato entro l'1%.

**4c. Da pin a box 3D**
- Partire dal codice esistente, che già trasforma la box, prende il 60% centrale, filtra per confidenza e richiede almeno 2 viste coerenti.
- Usare tutta la box (non solo il 60% centrale) per l'estensione e filtrare lo sfondo con la **depth mediana** (pixel entro ±X% della mediana). Niente SAM2.
- Nel sistema allineato alla gravità: percentili 2–98 → box orientata con un solo angolo attorno all'asse verticale, così resta dritta rispetto al pavimento.
- Fusione tra foto: stessa categoria e centroidi vicini (o IoU 3D > soglia) → unione. I casi visti in una sola foto restano **astensioni visibili**, come oggi.
- Estendere `Anchor` in `models.py` con un campo opzionale `box_world` (centro, dimensioni, angolo attorno all'asse verticale). Aggiornare `types.ts` di conseguenza; i pin esistenti restano compatibili.
- Estendere le categorie localizzabili a `cable` (oggi solo `rug`, `path`, `stairs`). Le categorie "mancanza di qualcosa" (maniglione, luce notturna) restano senza box.
- Con scala calibrata: dimensioni della box in metri **nel dato geometrico**, mai nel testo di Gemma. Il validatore che rifiuta numeri con unità nel testo resta com'è.

**Gate:** `case check` su room-002 passa i controlli di scala (errore rispetto al metro sotto soglia) e di box (tappeto, cavo, ostacolo localizzati). Ogni box viene ispezionata a occhio e registrata con `case attest --check scene-and-pins`.

## Fase 5 — Viewer (circa 2 h)

In `web/src/components/SceneViewer.tsx`:
1. Disegnare le box 3D come wireframe (`LineSegments` + `EdgesGeometry`), colorate per priorità, con etichetta. Le box rifiutate restano nascoste, come già avviene per i pin.
2. Clic su una box → selezione del pericolo, che apre già la foto con la box 2D.
3. Didascalia: "scala calibrata ±x%" oppure "scala relativa".
4. Typecheck e build (`npm --prefix web run typecheck`), più `scripts/browser_smoke.py`.

**Gate:** demo navigabile su una scena reale: pavimento in piano, box al posto giusto, clic dalla box alla foto e ritorno.

## Fase 6 — Integrazione end-to-end (circa 1 h)

1. Dall'interfaccia, su un database pulito: intake, upload delle foto reali, analisi live, revisione reale, bozza, rilascio.
2. Ispezionare il PDF.
3. `case attest` dei tre controlli manuali e `case baseline` su room-002. Sarà la **prima baseline reale**.
4. Annotare in `docs/runtime.md` i tempi e la VRAM su NVIDIA, dichiarandoli come **non AMD**.

**Gate:** report reale rilasciato. È la **demo minima completa**. Da qui si fa il commit e poi il deploy AMD.

## Fase 7 — Splat gsplat (opzionale, circa 3–4 h) · solo se la fase 6 è chiusa

Non usare `demo_colmap.py` per lo splat: rifarebbe l'inferenza VGGT con una propria pre-elaborazione e produrrebbe **pose diverse** da quelle delle box. Al suo posto:

1. Nuovo `scripts/export_colmap.py`: dall'NPZ di una run scrive un modello COLMAP con le **stesse pose**. Le K vanno riportate alla risoluzione delle foto originali, togliendo padding e scala con la trasformazione salvata, così gsplat si allena sulle foto a piena risoluzione. Punti iniziali presi dalla nuvola ad alta confidenza.
2. Allenare con gsplat (wheel o compilazione, da verificare per sm_89 e la versione di torch), circa 7k iterazioni, **con la normalizzazione del mondo disattivata**. Il nome esatto del parametro in `simple_trainer.py` va verificato. Controllare l'allineamento sovrapponendo lo splat alla nuvola di punti.
3. Applicare la scala ArUco e il `display_transform` allo splat (o al gruppo three.js che lo contiene).
4. Ridurre il peso per il web: armoniche sferiche di grado 0 e/o un formato compresso.
5. Viewer: livello splat con **Spark** dentro lo stesso `THREE.Group`, con un interruttore punti/splat. Le box restano sopra.
6. Se lo splat è sfocato: rieseguire con BA e usare le pose raffinate **anche** per le box, rigenerando la geometria.

**Gate:** splat e box coincidono visivamente. Se a metà del 17 non è così, abbandonare: la demo resta sulla nuvola di punti.

## Fase 8 — Deploy AMD (team)

Stessi commit VGGT, stesso checkpoint e SHA256, immagine `rocm/pytorch` e `amd_gsplat` se c'è lo splat. Rieseguire `case run room-002 --mode live` sull'MI300X e confrontarlo con la baseline locale (`case compare`). Registrare `rocm-smi`, tempi e VRAM per la demo.

---

## Calendario

| Quando | Cosa |
|---|---|
| 16 sera | Fase 0 + Fase 1 (cattura), poi avvio Fase 2 |
| 17 mattina | Fase 2 chiusa, Fase 3, inizio Fase 4 |
| 17 pomeriggio | Fase 4 + Fase 5 |
| 17 sera | Fase 6: demo minima completa, commit. Decisione sullo splat |
| 18 | Fase 7 se c'è tempo, deploy AMD, video, consegna |

## Regole durante il lavoro

- Ogni modifica Python: `pyright` sui file toccati più i test unittest. Ogni modifica TS: `npx tsc --noEmit`.
- Nuovi test **offline** per ogni funzione geometrica (piano, scala, box, fusione) con dati sintetici a soluzione nota.
- Niente fallback silenziosi su fixture in live mode, come prevede già l'architettura.
- Nessuna chiave in log, manifest, audit o frontend.

## Rischi principali

| Rischio | Segnale | Mitigazione |
|---|---|---|
| Ambiente diverso tra le macchine del team (5090 vs RTX 5000 Ada) | risultati non riproducibili | stessi commit e checkpoint; registrare la GPU in ogni run |
| VGGT di scarsa qualità sulla stanza | pareti doppie, camere sbagliate | rifare la cattura (luce, meno specchi, più sovrapposizione) prima di cambiare modello |
| ArUco letto male o depth rumorosa sul marker | dispersione della scala > 5% | marker più grande, più foto con il marker, solo angoli ad alta confidenza |
| Provider Gemma senza `json_schema` o immagini | errori 4xx, output non valido | cambiare provider; nessun rilassamento del validatore |
| gsplat non installabile o disallineato | build fallita, splat spostato | abbandonare la fase 7 e tenere la nuvola di punti |
