# Step Wise

**A personalized home fall-hazard assessment from photos and a patient description, with hazards highlighted in 3D and a professionally reviewed action plan.**

AMD Developer Hackathon: ACT III · Track 2: Health and Wellbeing

---

## 1. One-line pitch

A family member uploads overlapping photos of an older person's home and a description of their needs. Step Wise identifies candidate hazards in the photos, reconstructs the space in 3D with supported hazard locations highlighted, and produces a personalized action plan for professional review.

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

**Step Wise supports a remote home assessment from phone photos and patient context.**

## 3. Positioning

**For** hospital discharge teams, home-care cooperatives and families of older people
**who** need to make a home safe before or after a fall,
**Step Wise is** an AI home-safety assessment
**that** turns home photos and patient context into an evidenced, prioritized, professionally reviewed action plan,
**unlike** paper checklists (unmeasured, rarely completed), in-person therapist visits (scarce, slow) and fall-detection devices (they react after the fall instead of preventing it).

### Users and buyers

| Role | Who | What they get |
|---|---|---|
| Capturer | Family member or caregiver | Guided overlapping photos, then a clear list of fixes |
| Reviewer | Discharge nurse, occupational therapist, home-care coordinator | A 3D view with pinned findings to approve, edit or reject in minutes instead of a home visit |
| Beneficiary | The older person | A safer home and a lower chance of a fall or readmission |
| Buyer (hypotheses to validate) | Hospitals and regional health authorities, home-care cooperatives, long-term-care insurers, pharmacies offering health services | Fewer falls and readmissions, and scalable prevention |

### Value proposition

- **Personalized.** The supplied patient profile informs the relevance and priority of environmental findings.
- **Measured where supported.** Distances and heights come from calibrated geometry, with uncertainty and missing coverage shown explicitly.
- **Located.** Findings link to their source photos and, when reconstruction supports it, a location in the 3D scene.
- **Human in the loop.** A professional approves the final report. The AI does the slow part (the inspection) and the professional makes the decision.
- **Explicit data boundaries.** Reconstruction and Gemma run on our AMD infrastructure. The proposed Jev inspection sends selected photos to TypeSafe's API; keep the patient description on our infrastructure and make external image processing clear before upload.

## 4. Narrative and pitch

### Story

Maria is 84 and lives alone in Imperia. She is about to leave hospital after a hip fracture, and a second fall could be her last. Her daughter Giulia works in Milan and can't get there before discharge.

Giulia's cousin takes overlapping photos of Maria's flat. The nurse adds Maria's mobility and support needs. Step Wise first highlights candidate hazards in the photos, then places supported findings in the reconstructed rooms:

- a loose rug in the hallway,
- a bathtub with no grab bar,
- a hallway that measures **72 cm**, too narrow for the walker Maria will go home with,
- a dark path from the bedroom to the bathroom.

The nurse approves the report. Giulia gets a prioritized list of fixes with approximate costs, in plain Italian. Maria goes home to a safer flat.

### 3-minute pitch structure

1. **The person (20 s):** Maria's story.
2. **The comparison (20 s):** the deaths-at-work vs deaths-at-home table and "no risk assessment for the home."
3. **The gap (20 s):** 54% of falls happen at home, only 29% see the home as risky, and only 11% get advice.
4. **Live demo (90 s):** photos and patient profile in, image findings, 3D scene with pins, nurse review, final report.
5. **AMD + Google (15 s):** Jev supplies candidate image findings through its API. Gemma checks evidence against the patient profile and writes the action plan on AMD, alongside 3D reconstruction. Show where each stage runs and its measured timing.
6. **Close (15 s):** who pays, and the line *"We have a law for the factory floor. Step Wise is for the bathroom floor."*

## 5. Product experience

1. **Profile.** Create an assessment with a short patient description and reviewer-confirmed fields: mobility aid, vision limitations, relevant history, daily routes and support needs. Unknown fields stay unknown; the app does not infer diagnoses.
2. **Photos.** Upload overview photos with overlap and changes in viewpoint, plus detail photos of possible hazards. Start by testing 12-20 overlapping photos of one room or a connected small area; this is a capture target to evaluate, not a guarantee of reconstruction quality. Include a marker of known physical size if metric measurements are wanted.
3. **Image findings.** Jev evaluates focused checklist questions and proposes evidence boxes. Show candidate findings with source photos and an explicit "not visible / cannot assess" state.
4. **3D reconstruction.** Reconstruct from the full set of overlapping scene photos, then attach validated findings to the geometry. Disconnected rooms remain separate scenes. Request more photos when overlap or coverage is insufficient.
5. **Personalized assessment.** Gemma on AMD receives the patient profile, candidate findings, source crops and available measurements. It checks the evidence, proposes priorities and explains which supplied needs make a finding relevant.
6. **Review and report.** The professional approves, edits or rejects findings, inspects the report generated from accepted findings and releases the final web/PDF action plan. Include only sourced cost estimates.
7. **Follow-up.** Optional photos document changes for review; a new photo alone is not proof that every issue was resolved.

The first target is under five minutes from completed upload to a reviewable result, to be measured. Video ingestion is a later convenience that would extract photos into this same pipeline.

### Hazard checklist (v1)

| Category | Examples | How it is detected |
|---|---|---|
| Floor | Rugs, cables across the path, clutter, visible spills | Visual candidates; rug fixation and slip resistance require verification |
| Walking path | Hallways or passages too narrow for a walker or wheelchair, obstacles between bed and bathroom | 3D measurements of free floor space |
| Steps and stairs | Missing handrails, high or uneven steps, thresholds | Plane detection and height differences |
| Bathroom | No grab bars, no non-slip mat, bathtub instead of shower, low toilet | Vision model + height measurements |
| Bedroom | Bed too high or low, no light reachable from the bed | Vision model + height measurements |
| Lighting | Night-light visibility and areas needing a lighting check | Visual coverage questions; photos alone do not measure illumination |
| Reach | Everyday items stored too high or too low | Vision model + height measurements |

Thresholds (for example, minimum free width for a walker or recommended toilet seat height) come from a knowledge base of public fall-prevention and accessibility guidance. They are configurable and every finding cites its source.

## 6. Technical architecture

### Hackathon stack decision - 16 October 2026

Build for **Track 2: Health and Wellbeing**, with **AMD and Google** as the primary technology awards. The photo-first design uses **TypeSafe Jev 1.14** as a candidate detector and locator, **VGGT** for reconstruction, and **Gemma** for visual verification, patient-specific prioritization and report generation. Jev passed an initial public-photo smoke test; suitability on real patient-home captures remains unvalidated. Keep Gemma capable of performing the checklist inspection if Jev fails quality checks or is unavailable.

The working Google model is **`google/gemma-4-31B-it` in BF16**, served through **vLLM on ROCm** on one **AMD Instinct MI300X**. Google documents image/text support, and the official vLLM recipe documents single-MI300X deployment. GPU access, image inference, structured output, latency and memory still need testing. Jev's documented integration is a hosted API; do not count it as our AMD workload.

The first demo must complete one real photo set plus a patient profile, attach evidence to a few findings, place reliable findings in 3D, support professional review and produce an approved Italian action plan. Automatic whole-home measurements, segmentation, video ingestion and Evolus integration are extensions to that working path.

### Overview

```
 Phone / browser
   │  overlapping photos + patient description
   ▼
 API server (FastAPI, also serves the built React app)
   │                  ──► Mounted files (photos, evidence, 3D outputs)
   │                  ──► SQLite (assessments, jobs, findings, reviews)
   │  one background worker, persisted job status
   ▼
 Background worker
   ├─ 1. Pillow / OpenCV: upright photos, quality checks, stable image IDs
   ├─ 2. Jev API (external): visual checks + candidate boxes
   ├─ 3. VGGT on AMD: camera poses, depth, point cloud
   ├─ 4. OpenCV / Open3D: calibrated measurements + supported hazard pins
   └─ 5. Gemma on AMD: photos + findings + patient profile + measurements
   ▼
 Web app (React + three.js): professional reviews findings
   ▼
 Same Gemma endpoint: draft action plan from approved findings
   ▼
 Professional releases the final family report
```

### AMD infrastructure

- **Hardware:** target one AMD Instinct MI300X (192 GB of HBM3) on AMD Developer Cloud. Raw GPU/container access is still unconfirmed; a hosted text API alone cannot run the reconstruction worker.
- **Software:** ROCm, PyTorch for ROCm and a Gemma-compatible vLLM ROCm image. Pin the versions that pass an image-plus-JSON smoke test.
- **Serving:** one Gemma endpoint handles visual assessment and report generation in separate calls. VGGT runs in the PyTorch worker. Process one assessment at a time and schedule heavy GPU stages sequentially.
- **External inspection:** the worker calls TypeSafe for Jev image checks. This service is separate from AMD inference and requires its own credentials and disclosed data handling.

### Models and evidence tools

| Step | Primary choice | Fallback | Role |
|---|---|---|---|
| Image inspection and localization | **`jev-1.14.0`**, TypeSafe API, experimental image/Box features | Gemma checklist inspection on AMD | Focused presence/coverage questions and one target box per question |
| 3D reconstruction | **VGGT** on ROCm PyTorch | Evidence photos remain usable when reconstruction fails | Camera poses, depth and point cloud; validate the chosen checkpoint and runtime |
| Metric scale | **ArUco marker** with known printed dimensions, OpenCV | A manually measured reference; otherwise leave scale unknown | Enables metric measurements only when calibration is valid |
| Personalized hazard assessment | **Gemma 4 31B IT**, BF16 via vLLM | **Gemma 4 26B-A4B IT** if measured latency requires it | Verify source evidence and assess relevance using the profile, checklist and measurements |
| Report generation | **The same Gemma endpoint** | A deterministic template of approved findings | A prioritized Italian action plan, with source IDs validated by the backend |
| Guidance | **Small versioned JSON checklist with source URLs** | Reviewer flags missing guidance | Select relevant entries by hazard type; no embedding service for the first build |
| Segmentation (optional) | **SAM 2**, only if evidence localization needs it | Reviewer-corrected image locations | Refines candidate regions before projection into 3D |

**GPU memory:** do not assume that model weights describe peak usage. Measure Gemma's image encoder, KV cache and runtime overhead, and VGGT's peak for the selected frame count. Cap vLLM's memory allocation and context length to leave measured headroom for reconstruction. Sequential requests alone do not release a resident server's allocations.

Keep one consistent finding schema for Jev candidates and Gemma's fallback inspection. Record the producing model and version. Compare their actual findings on labelled home photos before deciding whether the external stage improves the result.

**Jev smoke test, 16 October 2026:** six public source photos plus one crop, each tested twice. Both passes matched 42/42 shared presence labels and 8/8 positive boxes exceeded IoU 0.5 against approximate pre-labelled boxes; median request latency was 1.61 seconds. The two repeated choice errors concerned rug attachment when no rug candidate was present, showing that dependent answers need application-level gating. Thin-object localization was weakest. This small selected set used scene-specific Box descriptions and does not establish clinical hazard accuracy or automatic discovery quality. See the [evaluation report and reproducible script](eval/jev/README.md).

### Pipeline in detail

**1. Photo and profile intake**
- Accept the patient description and reviewer-confirmed structured fields. Store the profile separately from visual-inspection requests so Jev does not need patient identifiers or medical history.
- Assign stable photo IDs, rotate pixels using EXIF orientation, normalize formats, check blur and duplicate images, and group photos by room. Preserve overlap when selecting reconstruction inputs.
- Keep a canonical upright coordinate system and the crop/resize/padding transforms for every model input. TypeSafe receives prepared JPEG/PNG copies within its documented limits; reconstruction retains adequate detail.
- Redact identifying content before external processing and strip metadata. Keep originals only within the stated retention policy; never promise that face blurring alone anonymizes a home.

**2. Candidate hazard identification in images**
- Pin `jev-1.14.0` and enable its documented `TypeSafe-Danger-Zone-Images` header. Treat vision and Box as experimental features with versioned evaluation.
- Ask atomic visual questions against a fixed checklist, including coverage and an explicit unassessable state. Examples: whether a cable is visible, whether it crosses a visible walking area, and where that cable is.
- Pair every Box with a presence check: TypeSafe documents that Box always returns coordinates, even for an absent target. Ignore unsupported boxes. One Box question locates one target; repeated objects need distinct descriptions or bounded crop passes.
- Gate dependent attribute questions too: ignore rug-attachment answers when the rug presence check does not support a candidate. Preserve unknown attachment when a rug is visible but its underside cannot be inspected.
- Store source photo, question/category, typed response, model confidence, version and candidate box. A box is not a segmentation mask, and confidence is not the patient's probability of falling.
- Keep initial image findings available while reconstruction runs. Preserve unknown coverage, candidate absence and uncertainty separately.

**3. 3D reconstruction**
- Feed VGGT the full selected set of overlapping scene photos, without detection overlays. Do not reconstruct only from hazard crops or photos containing hazards.
- Predict camera poses and depth, then fuse into a point cloud with Open3D. Save `.ply`, camera parameters, depth and confidence maps indexed by source photo ID.
- Disconnected photo groups remain separate scenes. Independently reconstructed chunks require alignment before merging. Ask for more views instead of inventing unseen connections or surfaces.

**4. Metric scale and geometric checks**
- OpenCV detects the known-size ArUco marker. Apply validated calibration only to its connected reconstruction; disconnected scenes need their own references.
- Check supported widths, heights or steps against tape-measured demo features. No reliable scale means no centimeter claims; relative 3D visualization can still be shown.
- Fit floor planes and measure a reviewer-selected or otherwise validated route. Photo capture positions do not establish a patient's walking path.
- Image brightness can trigger a request to check lighting, but exposure-adjusted photos do not measure illuminance or establish unsafe lighting by themselves.

**5. Hazard localization and fusion**
- Map each candidate box back through the recorded image transforms into the reconstruction's pixel coordinates. Use validated foreground depth and camera pose to propose a 3D location; a box can contain several surfaces, so its center alone is not a reliable anchor.
- Cross-check positions across available views. Add SAM 2 only when masks solve an observed foreground-localization problem. Missing-object findings can reference a verified fixture/area rather than invent a missing object's coordinates.
- Merge supported duplicates while preserving source photos, disagreement and uncertainty. Keep an image-only finding when its 3D position cannot be established; do not silently discard it.

**6. Personalized assessment on AMD**
- Gemma receives the patient profile, candidate findings, source photos/crops, coverage flags, available measurements and guidance. It verifies visual support and proposes a patient-specific priority and action.
- Give Gemma the complete room views for an additional checklist sweep so it is not restricted to Jev's positive detections. Record newly proposed findings separately and apply the same evidence/localization checks.
- Use vLLM structured output and Pydantic validation for `{hazard_type, observation, profile_relevance, proposed_priority, evidence_photo_ids, evidence_region, measurement_ids, uncertainty, suggested_fix, guidance_ids}`.
- The backend resolves measurement and guidance IDs from trusted records and rejects nonexistent references. Valid JSON or agreement between models does not establish correctness; all actionable findings remain subject to professional review.

**7. Review and report**
- The reviewer inspects highlighted photos and 3D pins, approves/edits/rejects findings, and can request more photos. Missing coverage must remain visible in the report.
- Input to the same Gemma endpoint: approved findings with measurements and rooms, plus the reviewer-confirmed patient profile.
- Supply relevant entries from the versioned guidance checklist (for example verified ISS, WHO or CDC material). Add document retrieval only when the collection outgrows explicit selection by hazard type.
- Output: a prioritized plan (urgent, important, recommended), each item with the reason, the source of the guidance, the fix, an approximate cost range and whether it's a do-it-yourself job or needs a professional.
- Languages: Italian by default, plus any language the family needs.
- Validate source and measurement references, then require professional approval of the final report before release. Cost ranges require a sourced price table or remain unspecified.

### Application stack

| Layer | Choice |
|---|---|
| Mobile capture | Multiple-photo upload, room labels and overlap guidance; patient description with structured confirmation fields |
| Frontend | Next.js (static export served by the API) + React + TypeScript, three.js for the point cloud and clickable pins, Tailwind CSS |
| Backend API | Python, FastAPI, Pydantic |
| Jobs | One background worker, persisted job states in SQLite, progress polling; identify interrupted jobs after restart |
| Database | SQLite |
| Storage | Mounted filesystem volume |
| Image and geometry tools | Pillow, OpenCV (ArUco), Open3D, NumPy |
| External model client | Official TypeSafe SDK or HTTP API; explicit model version, request limits, bounded retries and logged usage |
| PDF report | HTML template rendered to PDF with WeasyPrint |
| Packaging | Docker Compose: `api` (also serves built frontend), `worker`, `vllm`; ROCm images for GPU processes |
| Configuration | Environment variables only. No secrets in the repository |

### Data model (simplified)

- `Assessment`: id, status, person profile, created by, consent record
- `Capture`: photos with IDs and room labels, per-model image transforms, reconstruction files, per-scene scale and coverage status
- `CandidateFinding`: photo ID, question/category, typed response, presence/coverage, box, producing model/version
- `Hazard`: type, room, optional 3D position, measurements, priority, uncertainty, evidence photos, profile relevance and concise rationale
- `Review`: reviewer, decision per hazard (approved, edited, rejected), notes
- `Report`: final plan, language, PDF link, delivery date

## 7. Privacy, safety and regulation

- **Data handling design:** home photos can identify people and places, and the supplied patient description may contain health data. Specify consent, access control, encryption and retention before collecting real assessments. Use staged photos and fictional profiles for development.
- **External image processing:** the proposed Jev stage sends selected prepared photos to TypeSafe. Keep patient descriptions on our AMD infrastructure and show this processing boundary accurately. A Gemma-only inspection path can avoid that external image transfer; the Jev path must not be marketed as entirely self-hosted.
- **Not a diagnosis:** Step Wise assesses the environment, not the patient. A professional reviews every report before the family sees it. The positioning ("home safety assessment support for care professionals") is chosen to stay on the right side of medical-device rules. Formal regulatory review would be needed before any clinical deployment.
- **Honest uncertainty:** every finding shows a confidence level. Low-confidence findings are marked "to verify" instead of being presented as facts.

## 8. Evaluation

What we measure, so the demo shows numbers and not just claims:

- **Measurement accuracy:** Step Wise measurements vs a tape measure on 15–20 real features (hallway widths, door widths, step heights, toilet and bed heights). Target: median error under 3 cm.
- **Hazard detection:** precision and recall against a hand-labelled checklist on photo sets, including absent objects, obscured areas and repeated objects. Compare Jev candidates, Gemma-only inspection and the combined pipeline.
- **Localization:** manually check image boxes and 3D anchors; report wrong-surface pins, duplicate findings and findings that correctly abstain from localization.
- **Reviewer time:** time to review a Step Wise report vs a typical in-person home visit.
- **Performance on AMD:** time for each pipeline stage, total time from upload to report, vision-model throughput (tokens per second) and peak GPU memory, all measured on the MI300X.
- **Submission evidence:** preserve the hardware identity, exact model/checkpoint and container versions, real requests and outputs, and timed runs. Report AMD compute and TypeSafe API costs separately using measured usage and actual rates; include provisioned idle time when reporting deployment cost. Label cached demo replay explicitly.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A model doesn't run on ROCm | Smoke test every model on ROCm before building on it. Each step has a fallback model |
| Poor reconstruction (insufficient overlap, mirrors, dark rooms) | Overlap guidance, image-quality checks, separate disconnected rooms and request more photos |
| Jev preview fails or changes | Pin the version, test presence and localization separately, and retain Gemma checklist inspection as a fallback |
| Wrong scale | ArUco calibration plus measured reference checks; withhold unreliable dimensions |
| Vision model hallucinations | Measurements come from geometry only, structured JSON output, agreement across views, and professional review |
| Slow live demo | Pre-processed demo homes, with one short live capture as a bonus |
| Running out of credits | Strict GPU on/off discipline and cached outputs |

## 10. How Step Wise meets the judging criteria

### Award priorities

Working strategy, based on the [official ACT III page](https://lablab.ai/ai-hackathons/amd-developer-hackathon-act-iii), checked 16 October 2026:

| Opportunity | Decision |
|---|---|
| Health and Wellbeing, Track 2 | Primary entry: discharge preparation and professional review |
| AMD, advertised $5,000 pool | Primary target: demonstrate and measure the core workload on AMD |
| Google, advertised $5,000 pool | Primary target: Gemma verifies visual evidence, personalizes priorities and generates the action plan |
| Vibe Generation special prize | Strengthen the human/AI workflow and business case |
| Imment incubation for Italian teams | Pursue if eligible; no extra integration |
| Evolus partner award | Stretch after the main demo is reliable |

The event permits overlapping main and partner awards. The public page does not specify Google's prize split or a separate Google scoring rubric.

For Evolus, use its event workspace, our AMD-hosted vLLM endpoint, **workflows + knowledge libraries**, and app-driven REST calls. A possible extension prepares a guidance-backed plan after review. Validate this integration against the partner requirements before claiming eligibility. Keep raw home media on our infrastructure; use public guidance and synthetic or sanitized findings for the integration demo, and disclose external processing. Its listed prizes are plans, credits and promotion, so it must not displace the core build.

| Criterion | Step Wise |
|---|---|
| Application of Technology | Jev's typed photo findings, followed by Gemma's personalized assessment and spatial reconstruction on AMD, with each stage's compute location and performance documented |
| Business Value | A large, measurable problem (falls, fractures, readmissions) with clear users and buyers, replacing a scarce manual process |
| Originality | Spatial AI applied to fall prevention: measured, located hazards instead of a chatbot looking at photos |
| Presentation | A human story and a visual demo: photos plus profile, highlighted findings, 3D pins, professional review and a useful report |

## 11. Sources

- Hackathon tracks, partner requirements and prizes: https://lablab.ai/ai-hackathons/amd-developer-hackathon-act-iii
- Google Gemma 4 model card (modalities and model sizes): https://ai.google.dev/gemma/docs/core/model_card_4
- Official vLLM Gemma 4 recipe (AMD deployment, multi-image input and structured output): https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html
- AMD Gemma 4 support: https://www.amd.com/en/developer/resources/technical-articles/2026/day-0-support-for-gemma-4-on-amd-processors-and-gpus.html
- VGGT code, outputs and checkpoint license distinctions: https://github.com/facebookresearch/vggt
- TypeSafe Jev 1.14 release and experimental vision support: https://typesafe.ai/blog/jev-1-14
- TypeSafe image preparation, input limits and API: https://docs.typesafe.ai/images
- TypeSafe Box outputs, presence checks and single-target behavior: https://docs.typesafe.ai/primitives/box
- INAIL, *Relazione annuale 2025* (presented 22 Sept 2026): 1,189 worker deaths reported in 2025. Coverage: https://lamilano.it/dai-media/infortuni-relazione-annuale-inail-2025-600mila-denunce-totali-e-1-198-morti-sul-lavoro/
- Ministero della Salute, *Relazione sullo Stato Sanitario del Paese*, section 3.3 on home accidents: 5,783 estimated home-accident deaths in 2008, 74% among people over 80. http://www.rssp.salute.gov.it
- Osservatorio Vega Engineering, analysis of ISTAT data on home accidents in 2022 (projection of about 6,600 deaths): https://www.vegaengineering.com/comunicati/infortuni-domestici-piu-di-2-milioni-nel-2022-lemergenza-continua-lavorare-sulla-prevenzione/
- ISTAT data reported by QuiFinanza (Apr 2026): 14.8 million people over 65 (25.1%) and 2.5 million over 85 on 1 Jan 2026. https://quifinanza.it/attualita/crisi-demografica-italia-istat-2026/964644/
- ISS, PASSI d'Argento surveillance 2023–2024 on falls among people over 65: https://www.epicentro.iss.it/passi-argento/dati/cadute-12-mesi (summary: https://www.sossanita.org/archives/27718)
- Osservatorio Fratture da Fragilità, reported by HealthDesk (Sept 2021): about 100,000 cases a year, about 50% five-year mortality, over €800M in health costs and about €600M for rehabilitation. https://www.healthdesk.it/scenari/fratture-femore-unepidemia-silenziosa
- Cochrane Library, editorial on environmental interventions for preventing falls (2023): https://www.cochranelibrary.com/es/cdsr/doi/10.1002/14651858.ED000162/full

> Note: the home-accident death figures are official *estimates*, and the most recent one is from 2008. Present them as estimates and show the year. Check the exact effect sizes in the Cochrane review before quoting a percentage.
