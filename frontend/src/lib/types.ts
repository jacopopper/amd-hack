// Mirrors backend models.py and the API response projections. Unknowns stay null.
export type Priority = "urgent" | "important" | "recommended";
export type Decision = "approve" | "edit" | "reject" | "defer";
export type Profile = { description: string; mobility_aid: string | null; aid_width_m: number | null; vision: string | null; support_needs: string | null; relevant_history: string | null; daily_routes: string[] };
export type ClinicalClaim = { field: string; value: string | number | boolean | null; status: "current" | "historical" | "negated" | "unknown"; source_document_id: string | null; page: number | null; quote: string | null; origin: "document" | "manual"; reviewer_confirmed: boolean };
export type Inputs = { label: string; profile: Profile; profile_confirmed: boolean; staged_data_confirmed: true; processing_consent: true; external_processing_consent: boolean; clinical_external_processing_consent: boolean; clinical_facts: ClinicalClaim[] };
export type Modes = { reconstruction: string; gemma: string; avatar: string; jev: string };
export type Photo = { id: string; scene_id: string; role: "overview" | "detail"; asset_url: string; width: number; height: number };
export type JobSummary = { id: string; kind: string; status: string; stage: string | null; error: string | null };
export type Assessment = { id: string; inputs: Inputs; status: string; input_revision: number; review_revision: number; created_at: string; photos: Photo[]; clinical_documents: { id: string }[]; jobs: JobSummary[]; reports: { id: string; status: string; input_revision: number; review_revision: number }[] };
export type Listing = { id: string; label: string; status: string; created_at: string }[];
export type Evidence = { photo_id: string; box: [number, number, number, number] | null };
// box_world arrives with the 3D geometry work (piano-3d-locale, fase 4c); optional until then.
export type BoxWorld = { center: [number, number, number]; size: [number, number, number]; yaw: number };
export type Anchor = { scene_id: string; position_world: [number, number, number]; method: string; source_photo_ids: string[]; uncertainty: string; box_world?: BoxWorld | null };
type HazardBody = { category: string; observation: string; profile_relevance: string; profile_fields: string[]; priority: Priority; evidence: Evidence[]; measurement_ids: string[]; guidance_ids: string[]; uncertainty: string; suggested_action: string; anchor: Anchor | null };
export type Review = { decision: Decision; note: string; reviewer: string; created_at: string; effective: HazardBody };
export type Hazard = HazardBody & { id: string; producer?: string; provenance?: string; review: Review | null };
export type Coverage = { photo_id: string; category: string; coverage: string; note: string };
export type Findings = { hazards: Hazard[]; provenance: string | null; coverage: Coverage[]; follow_up_requests: string[]; modes: Record<string, string> };
export type Scene = { id: string; label: string; status?: string; ply_url: string | null; provenance?: string; warning?: string; scale_state?: string; scale_factor?: number | null; display_transform?: number[][]; cameras?: [number, number, number][]; bounds?: [[number, number, number], [number, number, number]] };
export type Stage = { status: string; elapsed_seconds: number | null; provenance: string; warnings?: unknown[] };
export type Job = JobSummary & { stages: Record<string, Stage> };
export type Report = { id: string; status: string; snapshot_sha256: string; html_url: string; pdf_url: string; snapshot: { draft_provenance: string }; input_revision: number; review_revision: number };
