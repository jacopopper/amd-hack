# Safe Home

**A home fall-risk assessment made from a 2-minute phone video, powered by open AI models running on AMD GPUs.**

AMD Developer Hackathon: ACT III · Track 2: Health and Wellbeing

---

## 1. One-line pitch

A family member films an older person's home with a phone. Safe Home rebuilds the home in 3D, finds and measures fall hazards, and produces a prioritized action plan that a nurse or care professional reviews before it goes to the family.

## 2. The problem

### Home is where older Italians get hurt

| | At work | At home |
|---|---|---|
| Deaths per year | **1,189** worker deaths reported in 2025 (INAIL) | **About 5,800** estimated deaths from home accidents (Ministry of Health, 2008 estimate) |
| Who dies | Mostly working-age adults | **74%** are over 80; half are women over 80 |
| Prevention system | Mandatory risk assessment, safety officers, inspections, funded prevention grants | None |

> Every year, home accidents kill about five times as many people in Italy as workplace accidents. We have a law for the factory floor. We have nothing for the bathroom floor.

### Scale

- **14.8 million** Italians are over 65 (25.1% of the population, the highest share in the EU), and **2.5 million** are over 85. *(ISTAT, 1 Jan 2026)*
- **1 in 5** people over 65 fell in the past 12 months. Of those who fell, **18%** broke a bone and **16%** were hospitalized. *(ISS, PASSI d'Argento 2023–2024)*
- **54%** of these falls happen inside the home. *(PASSI d'Argento)*
- About **100,000** hip fractures happen each year in Italy. **About half** of patients over 65 with a hip fracture die within five years. *(Osservatorio Fratture da Fragilità, 2021)*
- Fragility fractures cost the health system **over €800M a year**, plus **about €600M** for rehabilitation. *(Osservatorio Fratture da Fragilità, 2021)*

### Why nobody fixes it

- Only **29%** of older people think their home is a risky place.
- Only **11%** received advice on avoiding falls from a doctor or health worker in the past year.
- **33%** use no anti-fall aid in the bathroom, and only **22%** have grab bars.
- **34%** are afraid of falling, rising to **53%** among people over 85. Fear of falling leads to less movement, more frailty, and more falls.

*(All from ISS, PASSI d'Argento 2023–2024)*

### What already works, and why it doesn't scale

Clinical evidence (Cochrane reviews on environmental interventions) shows that a **home hazard assessment by an occupational therapist, followed by home modifications, reduces falls**, especially in high-risk people. However, therapists are scarce, home visits are expensive and slow, and they rarely happen before a patient is discharged. Paper checklists exist, but families don't use them and can't measure what matters, such as whether a hallway is wide enough for a walker.

**Safe Home makes that home visit possible from a phone video.**

## 3. Positioning

**For** hospital discharge teams, home-care cooperatives and families of older people
**who** need to make a home safe before or after a fall,
**Safe Home is** an AI home-safety assessment
**that** turns a phone video into a measured, prioritized, professionally reviewed action plan in minutes,
**unlike** paper checklists (unmeasured, rarely completed), in-person therapist visits (scarce, slow) and fall-detection devices (they react after the fall instead of preventing it).

### Users and buyers

| Role | Who | What they get |
|---|---|---|
| Capturer | Family member or caregiver | A guided 2-minute capture, then a clear list of fixes |
| Reviewer | Discharge nurse, occupational therapist, home-care coordinator | A 3D view with pinned findings to approve, edit or reject in minutes instead of a home visit |
| Beneficiary | The older person | A safer home and a lower chance of a fall or readmission |
| Buyer (hypotheses to validate) | Hospitals and regional health authorities, home-care cooperatives, long-term-care insurers, pharmacies offering health services | Fewer falls and readmissions, and scalable prevention |

### Value proposition

- **Measured, not guessed.** Real distances and heights (hallway width, step height, door width, bed and toilet height) come from 3D reconstruction at real scale.
- **Located.** Every hazard is pinned to a place in the home, so the family knows exactly what to fix and the professional can verify it.
- **Human in the loop.** A professional approves the final report. The AI does the slow part (the inspection) and the professional makes the decision.
- **Private by design.** The models are open and run on GPUs we control. Home videos and health information are never sent to a third-party AI API.

## 4. Narrative and pitch

### Story

Maria is 84 and lives alone in Imperia. She is about to leave hospital after a hip fracture, and a second fall could be her last. Her daughter Giulia works in Milan and can't get there before discharge.

On a video call, Giulia's cousin walks through Maria's flat with a phone. Within a few minutes, the discharge nurse sees the home in 3D with red pins:

- a loose rug in the hallway,
- a bathtub with no grab bar,
- a hallway that measures **72 cm**, too narrow for the walker Maria will go home with,
- a dark path from the bedroom to the bathroom.

The nurse approves the report. Giulia gets a prioritized list of fixes with approximate costs, in plain Italian. Maria goes home to a safer flat.

### 3-minute pitch structure

1. **The person (20 s):** Maria's story.
2. **The comparison (20 s):** the deaths-at-work vs deaths-at-home table and "no risk assessment for the home."
3. **The gap (20 s):** 54% of falls happen at home, only 29% see the home as risky, and only 11% get advice.
4. **Live demo (90 s):** video in, 3D scene out, pins, measurements, nurse review, final report.
5. **AMD (15 s):** the whole pipeline (3D reconstruction, vision, reasoning, speech) runs on one AMD Instinct MI300X with open models, with real timings shown.
6. **Close (15 s):** who pays, and the line *"We have a law for the factory floor. Safe Home is for the bathroom floor."*

## 5. Product experience

1. **Start.** The reviewer creates an assessment and sends the family a link. No app install is needed: it's a mobile web app.
2. **Prepare.** The family places a printed reference marker (an A4 sheet with an ArUco marker) on the floor of the first room. It gives the system real-world scale.
3. **Capture.** The app guides a slow walk through the main path: entrance, living room, bedroom, bathroom, kitchen and stairs. The capturer can speak while filming ("this is where mum showers") to add context.
4. **Processing.** The video is uploaded and processed on the AMD GPU. A target is under 5 minutes from upload to results.
5. **Review.** The reviewer opens a 3D view of the home with color-coded pins. Clicking a pin shows the source video frame, the hazard, the measurement, the reasoning and the suggested fix. The reviewer approves, edits or rejects each finding.
6. **Report.** The family receives a prioritized action plan (urgent, important, recommended) with approximate costs, in their language, as a web page and a PDF.
7. **Follow-up.** An optional second video after the fixes confirms they were done.

### Hazard checklist (v1)

| Category | Examples | How it is detected |
|---|---|---|
| Floor | Loose rugs, cables across the path, clutter on the floor, slippery surfaces | Vision model + objects found on the floor plane |
| Walking path | Hallways or passages too narrow for a walker or wheelchair, obstacles between bed and bathroom | 3D measurements of free floor space |
| Steps and stairs | Missing handrails, high or uneven steps, thresholds | Plane detection and height differences |
| Bathroom | No grab bars, no non-slip mat, bathtub instead of shower, low toilet | Vision model + height measurements |
| Bedroom | Bed too high or low, no light reachable from the bed | Vision model + height measurements |
| Lighting | Dark rooms or passages, no night light on the path to the bathroom | Image brightness per area, together with the vision model |
| Reach | Everyday items stored too high or too low | Vision model + height measurements |

Thresholds (for example, minimum free width for a walker or recommended toilet seat height) come from a knowledge base of public fall-prevention and accessibility guidance. They are configurable and every finding cites its source.

## 6. Technical architecture

### Overview

```
 Phone (mobile web app)
   │  video + optional voice narration
   ▼
 API server (FastAPI) ──► Object storage (videos, frames, 3D outputs)
   │                  ──► Database (assessments, findings, reviews)
   │  job queue (Redis)
   ▼
 GPU worker on AMD Developer Cloud (AMD Instinct MI300X, ROCm)
   ├─ 1. Preprocessing: frame extraction, keyframe selection, face blurring
   ├─ 2. 3D reconstruction: camera poses, depth, point cloud
   ├─ 3. Metric scale: ArUco marker → real-world units
   ├─ 4. Scene understanding: object detection + segmentation, lifted into 3D
   ├─ 5. Geometric checks: floor plane, widths, heights, steps
   ├─ 6. Vision-language hazard review on keyframes
   ├─ 7. Speech-to-text of the narration
   ├─ 8. Fusion: merge per-frame findings into unique 3D hazards
   └─ 9. Report agent: prioritized plan with guidance citations
   ▼
 Web app (React + three.js): 3D viewer, reviewer dashboard, family report
```

### AMD infrastructure

- **Hardware:** one AMD Instinct MI300X (192 GB of HBM3) on AMD Developer Cloud. All models run on this single GPU at once, which makes for a clear "Application of Technology" story.
- **Software:** ROCm, PyTorch for ROCm, and vLLM for ROCm. Start from AMD's official ROCm PyTorch and vLLM container images.
- **Serving:** vLLM serves the vision-language model and the language model through an OpenAI-compatible API. The other models (reconstruction, detection, segmentation, speech) run in a single PyTorch worker process.

### Models (all open)

| Step | Primary choice | Fallback | Role |
|---|---|---|---|
| 3D reconstruction | **VGGT** (feed-forward, about 1B parameters) | MASt3R / DUSt3R | Camera poses, depth maps and a dense point cloud from frames in seconds, without slow per-scene training |
| Metric scale | **ArUco marker** (OpenCV) | Metric depth model (for example MoGe-2) | Converts the reconstruction into real centimeters |
| Open-vocabulary detection | **OWLv2** or **Grounding DINO** | Detections from the vision-language model | Finds rugs, grab bars, handrails, beds, toilets, cables and doors |
| Segmentation | **SAM 2** | Bounding boxes only | Exact masks, which are lifted into 3D using depth and camera poses |
| Hazard review | **Qwen2.5-VL** (32B in bf16, or 72B in FP8) via vLLM | Smaller Qwen-VL variant | Looks at keyframes and returns structured findings against the checklist |
| Report agent | Same vision-language model, or **Gemma 3 27B** / **Llama 3.3 70B** via vLLM | Same model as hazard review | Writes the prioritized plan, citing the guidance knowledge base |
| Speech-to-text | **Whisper large-v3** | Whisper medium | Transcribes narration and links it to rooms and timestamps |
| Embeddings (knowledge base) | **bge-m3** (multilingual) | Any multilingual embedding model | Retrieval over the guidance documents |

**Rough GPU memory budget on one MI300X:** Qwen2.5-VL-32B in bf16 (about 70 GB including cache headroom), VGGT (about 10 GB with activations), SAM 2 + OWLv2 (under 10 GB), Whisper large-v3 (about 5 GB) and embeddings (about 2 GB). This totals well under 192 GB and leaves room to upgrade to a 72B vision model in FP8. Cap vLLM's GPU memory share (`--gpu-memory-utilization`) so the other models fit alongside it.

### Pipeline in detail

**1. Preprocessing**
- `ffmpeg` extracts frames at 2–3 fps.
- Keyframe selection removes blurry frames (Laplacian variance) and near-duplicates (frame difference), keeping roughly 60–150 frames.
- Faces are detected and blurred before anything is stored.

**2. 3D reconstruction**
- VGGT processes the keyframes in batches and predicts camera poses and per-frame depth maps.
- The depth maps are fused into a point cloud, downsampled and cleaned with Open3D.
- The output is a `.ply` file for the viewer, plus camera poses and depth maps used by the following steps.

**3. Metric scale**
- OpenCV detects the ArUco marker in the frames where it is visible. Its known physical size gives the scale factor applied to the whole reconstruction.
- A sanity check compares a standard object (an interior door is usually about 80 cm wide). If the two disagree too much, a warning is shown to the reviewer.

**4. Scene understanding**
- Open-vocabulary detection runs on keyframes with a fixed prompt list (rug, mat, cable, grab bar, handrail, bathtub, shower, toilet, bed, stairs, door, lamp and others).
- SAM 2 turns detections into masks. The masks are projected into 3D using depth and camera poses, then merged across views into object instances (clustering with DBSCAN).

**5. Geometric checks**
- **Floor plane:** RANSAC plane fitting on the point cloud (Open3D).
- **Walkable width:** a horizontal slice of the point cloud at 10–100 cm above the floor gives an occupancy map. The narrowest free width along the walking path, taken from the camera trajectory (the capturer actually walked it), is measured and compared with walker and wheelchair thresholds.
- **Steps and thresholds:** height differences between floor-level planes along the path.
- **Heights:** bed, toilet and shelf heights from object instances relative to the floor.
- **Floor obstacles:** objects lower than about 30 cm that sit on the walking path.
- **Lighting:** brightness statistics of frames per area as a proxy for poor lighting, confirmed by the vision model.

**6. Vision-language hazard review**
- Each keyframe, or a small group of frames from the same area, is sent to the vision-language model along with the checklist, the detected objects and the nearby measurements.
- Guided decoding in vLLM forces the output into a JSON schema: `{hazard_type, description, severity, confidence, evidence_box, suggested_fix}`.
- The measurements come from geometry, not from the model, so the model can't invent numbers. Its job is recognition and judgment.

**7. Speech-to-text**
- Whisper transcribes the narration with timestamps. The transcript is linked to the frames and areas being filmed at that moment, and passed as context to the hazard review and the report agent.

**8. Fusion**
- Per-frame findings are lifted into 3D using their evidence box, depth and camera pose.
- Findings of the same type that are close together are merged into one hazard. Its confidence comes from how many views agree.
- Each hazard keeps links to its best supporting frames for the reviewer.

**9. Report agent**
- Input: the final list of hazards with measurements, rooms and transcript context, plus the person's profile entered by the reviewer (uses a walker, poor eyesight, recent hip fracture and so on).
- Retrieval over a knowledge base of public fall-prevention and accessibility guidance (for example ISS material, the WHO *Step Safely* report and the CDC *STEADI* home-safety checklist).
- Output: a prioritized plan (urgent, important, recommended), each item with the reason, the source of the guidance, the fix, an approximate cost range and whether it's a do-it-yourself job or needs a professional.
- Languages: Italian by default, plus any language the family needs.

### Application stack

| Layer | Choice |
|---|---|
| Mobile capture | Mobile web app (PWA): `MediaRecorder` for video and audio, a guided capture overlay, resumable upload |
| Frontend | React (Next.js) + TypeScript, three.js for the 3D viewer (point cloud with clickable 3D pins), Tailwind CSS |
| Backend API | Python, FastAPI, Pydantic |
| Jobs | Redis + RQ (or Celery) for the GPU pipeline, with progress updates over Server-Sent Events |
| Database | PostgreSQL (SQLite is fine for the hackathon) |
| Storage | S3-compatible object storage (MinIO) or a mounted volume |
| 3D and vision tools | Open3D, OpenCV (ArUco), NumPy, scikit-learn (DBSCAN) |
| PDF report | HTML template rendered to PDF with WeasyPrint |
| Packaging | Docker Compose: `web`, `api`, `worker`, `vllm`, `redis`, `db`. The GPU containers are based on AMD ROCm images |
| Configuration | Environment variables only. No secrets in the repository |

### Data model (simplified)

- `Assessment`: id, status, person profile, created by, consent record
- `Capture`: video, audio, frames, reconstruction files, scale factor
- `Hazard`: type, room, 3D position, measurements, severity, confidence, evidence frames, model reasoning
- `Review`: reviewer, decision per hazard (approved, edited, rejected), notes
- `Report`: final plan, language, PDF link, delivery date

## 7. Privacy, safety and regulation

- **Sensitive data:** home videos and health conditions are sensitive personal data under GDPR. Safe Home requires explicit consent at capture time, blurs faces before storage, encrypts data in storage and in transit, and deletes raw video after a short retention period.
- **No third-party AI APIs:** all models are open and run on infrastructure we control. This is both a privacy advantage and a direct consequence of building on AMD.
- **Not a diagnosis:** Safe Home assesses the environment, not the patient. A professional reviews every report before the family sees it. The positioning ("home safety assessment support for care professionals") is chosen to stay on the right side of medical-device rules. Formal regulatory review would be needed before any clinical deployment.
- **Honest uncertainty:** every finding shows a confidence level. Low-confidence findings are marked "to verify" instead of being presented as facts.

## 8. Evaluation

What we measure, so the demo shows numbers and not just claims:

- **Measurement accuracy:** Safe Home measurements vs a tape measure on 15–20 real features (hallway widths, door widths, step heights, toilet and bed heights). Target: median error under 3 cm.
- **Hazard detection:** precision and recall against a hand-labelled checklist on 3–5 real homes filmed by the team.
- **Reviewer time:** time to review a Safe Home report vs a typical in-person home visit.
- **Performance on AMD:** time for each pipeline stage, total time from upload to report, vision-model throughput (tokens per second) and peak GPU memory, all measured on the MI300X.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A model doesn't run on ROCm | Smoke test every model on ROCm before building on it. Each step has a fallback model |
| Poor reconstruction (fast motion, mirrors, dark rooms) | A guided capture overlay, blurry-frame filtering, and a warning to re-film a specific room |
| Wrong scale | ArUco marker plus a sanity check against standard objects |
| Vision model hallucinations | Measurements come from geometry only, structured JSON output, agreement across views, and professional review |
| Slow live demo | Pre-processed demo homes, with one short live capture as a bonus |
| Running out of credits | Strict GPU on/off discipline and cached outputs |

## 10. How Safe Home meets the judging criteria

| Criterion | Safe Home |
|---|---|
| Application of Technology | Several open models (3D reconstruction, detection, segmentation, vision-language, speech, language) running together on one AMD MI300X with ROCm and vLLM, with measured performance |
| Business Value | A large, measurable problem (falls, fractures, readmissions) with clear users and buyers, replacing a scarce manual process |
| Originality | Spatial AI applied to fall prevention: measured, located hazards instead of a chatbot looking at photos |
| Presentation | A human story, a striking statistic, and a visual live demo (video in, 3D home with pins out) |

## 11. Sources

- INAIL, *Relazione annuale 2025* (presented 22 Sept 2026): 1,189 worker deaths reported in 2025. Coverage: https://lamilano.it/dai-media/infortuni-relazione-annuale-inail-2025-600mila-denunce-totali-e-1-198-morti-sul-lavoro/
- Ministero della Salute, *Relazione sullo Stato Sanitario del Paese*, section 3.3 on home accidents: 5,783 estimated home-accident deaths in 2008, 74% among people over 80. http://www.rssp.salute.gov.it
- Osservatorio Vega Engineering, analysis of ISTAT data on home accidents in 2022 (projection of about 6,600 deaths): https://www.vegaengineering.com/comunicati/infortuni-domestici-piu-di-2-milioni-nel-2022-lemergenza-continua-lavorare-sulla-prevenzione/
- ISTAT data reported by QuiFinanza (Apr 2026): 14.8 million people over 65 (25.1%) and 2.5 million over 85 on 1 Jan 2026. https://quifinanza.it/attualita/crisi-demografica-italia-istat-2026/964644/
- ISS, PASSI d'Argento surveillance 2023–2024 on falls among people over 65: https://www.epicentro.iss.it/passi-argento/dati/cadute-12-mesi (summary: https://www.sossanita.org/archives/27718)
- Osservatorio Fratture da Fragilità, reported by HealthDesk (Sept 2021): about 100,000 cases a year, about 50% five-year mortality, over €800M in health costs and about €600M for rehabilitation. https://www.healthdesk.it/scenari/fratture-femore-unepidemia-silenziosa
- Cochrane Library, editorial on environmental interventions for preventing falls (2023): https://www.cochranelibrary.com/es/cdsr/doi/10.1002/14651858.ED000162/full

> Note: the home-accident death figures are official *estimates*, and the most recent one is from 2008. Present them as estimates and show the year. Check the exact effect sizes in the Cochrane review before quoting a percentage.
