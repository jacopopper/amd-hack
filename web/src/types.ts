// Mirrors backend models.py and the API response projections. Unknowns stay null.
export type Priority = 'urgent' | 'important' | 'recommended';
export type Profile = {description: string; mobility_aid: string | null; aid_width_m: number | null; vision: string | null; support_needs: string | null; relevant_history: string | null; daily_routes: string[]};
export type Inputs = {label: string; profile: Profile; profile_confirmed: boolean; staged_data_confirmed: true; processing_consent: true; external_processing_consent: boolean};
export type Photo = {id: string; scene_id: string; role: 'overview' | 'detail'; asset_url: string; width: number; height: number};
export type Assessment = {id: string; inputs: Inputs; status: string; input_revision: number; review_revision: number; photos: Photo[]; jobs: {id: string; kind: string; status: string; stage: string | null; error: string | null}[]; reports: {id: string; status: string; input_revision: number; review_revision: number}[]};
export type Evidence = {photo_id: string; box: [number, number, number, number] | null};
export type Anchor = {scene_id: string; position_world: [number, number, number]; method: string; source_photo_ids: string[]; uncertainty: string};
export type Hazard = {id: string; category: string; observation: string; profile_relevance: string; profile_fields: string[]; priority: Priority; evidence: Evidence[]; measurement_ids: string[]; guidance_ids: string[]; uncertainty: string; suggested_action: string; anchor: Anchor | null; review: {decision: string; note: string; reviewer: string; effective: Omit<Hazard, 'id' | 'review'>} | null};
export type Findings = {hazards: (Hazard & {producer?: string; provenance?: string})[]; provenance: string | null; coverage: {photo_id: string; category: string; coverage: string; note: string}[]; follow_up_requests: string[]; modes: Record<string, string>};
export type Scene = {id: string; label: string; status?: string; ply_url: string | null; provenance?: string; warning?: string; scale_state?: string; display_transform?: number[][]; cameras?: [number, number, number][]};
// GET /api/assessments/{id}/avatar. Every field is null until an avatar has been generated.
export type Avatar = {status: string | null; reason: string | null; has_walker: boolean | null; preview_url: string | null; preview_width: number | null; preview_height: number | null; glb_url: string | null; illustrative_only: boolean | null};
export type Report = {id: string; status: string; snapshot_sha256: string; html_url: string; pdf_url: string; snapshot: {draft_provenance: string}; input_revision: number; review_revision: number};
