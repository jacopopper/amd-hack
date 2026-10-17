from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Text = Annotated[str, Field(min_length=1, max_length=2000)]
ID = Annotated[str, Field(min_length=1, max_length=100)]
Vector = tuple[float, float, float]
Box = tuple[float, float, float, float]


class Priority(StrEnum):
    urgent = "urgent"
    important = "important"
    recommended = "recommended"


class Profile(StrictModel):
    description: Text
    mobility_aid: str | None = Field(default=None, max_length=200)
    aid_width_m: float | None = Field(default=None, gt=0, le=3)
    vision: str | None = Field(default=None, max_length=500)
    support_needs: str | None = Field(default=None, max_length=500)
    relevant_history: str | None = Field(default=None, max_length=1000)
    daily_routes: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=20)


class AssessmentInput(StrictModel):
    label: Annotated[str, Field(min_length=1, max_length=120)]
    profile: Profile
    profile_confirmed: bool
    staged_data_confirmed: Literal[True]
    processing_consent: Literal[True]
    external_processing_consent: bool = False
    clinical_external_processing_consent: bool = False
    clinical_facts: list["ClinicalClaim"] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def confirmed_facts_are_reviewed(self):
        if self.profile_confirmed and any(not fact.reviewer_confirmed for fact in self.clinical_facts):
            raise ValueError("A confirmed profile may contain only reviewer-confirmed clinical facts")
        return self


class ClinicalClaim(StrictModel):
    field: Literal["mobility_aid", "aid_width_m", "vision", "support_needs", "relevant_history", "daily_routes", "age_group", "gender_identity", "hip_condition"]
    value: str | float | bool | None = None
    status: Literal["current", "historical", "negated", "unknown"]
    source_document_id: ID | None = None
    page: int | None = Field(default=None, ge=1)
    quote: Annotated[str, Field(max_length=800)] | None = None
    origin: Literal["document", "manual"] = "document"
    reviewer_confirmed: bool = False

    @model_validator(mode="after")
    def source_is_complete(self):
        if self.origin == "document" and not (self.source_document_id and self.page and self.quote and self.quote.strip()):
            raise ValueError("Document claims require a page and exact source quote")
        if self.origin == "manual" and any(value is not None for value in (self.source_document_id, self.page, self.quote)):
            raise ValueError("Manual claims cannot carry document citations")
        if self.status in {"negated", "unknown"} and self.value is not None:
            raise ValueError("Negated and unknown claims cannot carry a positive value")
        if self.status in {"current", "historical"} and self.value is None:
            raise ValueError("Current or historical claims require a value")
        return self


class DocumentClaim(ClinicalClaim):
    source_document_id: ID
    page: int = Field(ge=1)
    quote: Annotated[str, Field(min_length=1, max_length=800)]
    origin: Literal["document"] = "document"
    reviewer_confirmed: Literal[False] = False


class ClinicalDraft(StrictModel):
    claims: list[DocumentClaim] = Field(max_length=40)
    unknown_fields: list[Literal["mobility_aid", "aid_width_m", "vision", "support_needs", "relevant_history", "daily_routes", "age_group", "gender_identity", "hip_condition"]] = Field(default_factory=list, max_length=9)


class AssessmentEdit(AssessmentInput):
    expected_input_revision: int = Field(ge=1)


class Revisions(StrictModel):
    expected_input_revision: int = Field(ge=1)
    expected_review_revision: int = Field(ge=0)


class ProfileConfirmation(Revisions):
    profile: Profile
    claims: list[ClinicalClaim] = Field(default_factory=list, max_length=40)


Transform4 = tuple[
    tuple[float, float, float, float], tuple[float, float, float, float],
    tuple[float, float, float, float], tuple[float, float, float, float],
]


class SceneLinkInput(Revisions):
    coordinate_space: Literal["metric_scene_coordinates"]
    transform_from_to: Transform4
    overlap_photo_pairs: list[tuple[ID, ID]] = Field(min_length=1, max_length=20)
    residual: float | None = Field(default=None, ge=0, le=1000)
    residual_unit: Literal["m"] = "m"
    uncertainty: Text

    @model_validator(mode="after")
    def rigid_transform(self):
        matrix = self.transform_from_to
        if any(abs(matrix[3][i] - expected) > 1e-6 for i, expected in enumerate((0, 0, 0, 1))):
            raise ValueError("Room transform must have homogeneous final row [0, 0, 0, 1]")
        rotation = [row[:3] for row in matrix[:3]]
        for i in range(3):
            for j in range(3):
                dot = sum(rotation[i][k] * rotation[j][k] for k in range(3))
                if abs(dot - (1 if i == j else 0)) > 0.02:
                    raise ValueError("Room transform rotation must be rigid and orthonormal")
        a, b, c = rotation
        determinant = (a[0] * (b[1] * c[2] - b[2] * c[1]) -
                       a[1] * (b[0] * c[2] - b[2] * c[0]) +
                       a[2] * (b[0] * c[1] - b[1] * c[0]))
        if not math.isclose(determinant, 1, abs_tol=0.02):
            raise ValueError("Room transform rotation must preserve handedness")
        return self


class CalibrationSegment(StrictModel):
    label: Annotated[str, Field(min_length=1, max_length=120)]
    point_a: Vector  # Raw scene coordinates before the display transform.
    point_b: Vector
    actual_distance_m: float = Field(gt=0.05, le=50)
    source_photo_ids: list[ID] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def distinct_points_and_sources(self):
        if math.dist(self.point_a, self.point_b) <= 1e-8:
            raise ValueError("Calibration points must be distinct")
        if any(abs(coordinate) > 1_000_000 for point in (self.point_a, self.point_b) for coordinate in point):
            raise ValueError("Calibration points are outside the supported scene-coordinate range")
        if len(self.source_photo_ids) != len(set(self.source_photo_ids)):
            raise ValueError("Calibration source photos must be unique")
        return self


class CalibrationInput(Revisions):
    fit_segments: list[CalibrationSegment] = Field(min_length=2, max_length=10)
    holdout_segments: list[CalibrationSegment] = Field(min_length=1, max_length=10)
    maximum_holdout_relative_error: float = Field(default=0.05, ge=0.01, le=0.10)

    @model_validator(mode="after")
    def unique_segment_labels(self):
        labels = [segment.label for segment in self.fit_segments + self.holdout_segments]
        if len(labels) != len(set(labels)):
            raise ValueError("Calibration segment labels must be unique")
        points = [point for segment in self.fit_segments + self.holdout_segments for point in (segment.point_a, segment.point_b)]
        if any(math.dist(left, right) <= 1e-6 for index, left in enumerate(points) for right in points[index + 1:]):
            raise ValueError("Calibration segments must use independent scene points")
        return self


class Evidence(StrictModel):
    photo_id: ID
    box: Box | None = None  # upright original-image pixels

    @model_validator(mode="after")
    def ordered(self):
        if self.box is not None:
            l, t, r, b = self.box
            if not (0 <= l < r and 0 <= t < b):
                raise ValueError("Evidence box must have positive extent")
        return self


class Anchor(StrictModel):
    scene_id: ID
    position_world: Vector
    method: Literal["foreground_multiview", "reviewer_corrected", "synthetic_fixture"]
    source_photo_ids: list[ID] = Field(min_length=1, max_length=40)
    uncertainty: Text


class Candidate(StrictModel):
    photo_id: ID
    category: ID
    question_id: ID
    presence: Literal["present", "absent", "unknown"]
    coverage: Literal["visible", "partial", "obscured", "out_of_frame", "unknown"]
    evidence: Evidence | None = None
    attachment: Literal["fixed", "loose", "unknown"] = "unknown"
    producer: ID
    provenance: Literal["fixture", "live", "replay"]
    provider_response: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def gate(self):
        if self.presence != "present":
            self.evidence = None
            self.attachment = "unknown"
        if self.evidence and self.evidence.photo_id != self.photo_id:
            raise ValueError("Candidate evidence belongs to another photo")
        return self


class Hazard(StrictModel):
    category: ID
    observation: Text
    profile_relevance: Text
    profile_fields: list[Literal["description", "mobility_aid", "aid_width_m", "vision", "support_needs", "relevant_history", "daily_routes"]] = Field(max_length=7)
    priority: Priority
    evidence: list[Evidence] = Field(min_length=1, max_length=8)
    measurement_ids: list[ID] = Field(default_factory=list, max_length=8)
    guidance_ids: list[ID] = Field(min_length=1, max_length=8)
    anchor: Anchor | None = None
    uncertainty: Text
    suggested_action: Text


class Coverage(StrictModel):
    photo_id: ID
    category: ID
    coverage: Literal["visible", "partial", "obscured", "out_of_frame", "unknown"]
    note: Text


NormalizedCoordinate = Annotated[float, Field(ge=0, le=1000)]


class ProposalEvidence(StrictModel):
    photo_id: ID
    box_yxyx_1000: tuple[NormalizedCoordinate, NormalizedCoordinate, NormalizedCoordinate, NormalizedCoordinate] | None = Field(
        description="[ymin, xmin, ymax, xmax] normalized to 0..1000 in the supplied image, or null when not localizable")

    @model_validator(mode="after")
    def ordered(self):
        if self.box_yxyx_1000:
            top, left, bottom, right = self.box_yxyx_1000
            if not (top < bottom and left < right):
                raise ValueError("Proposal evidence needs positive box extent")
        return self


class HazardProposal(StrictModel):
    category: ID
    observation: Text
    profile_fields: list[Literal["description", "mobility_aid", "aid_width_m", "vision", "support_needs", "relevant_history", "daily_routes"]] = Field(max_length=7)
    priority: Priority
    evidence: list[ProposalEvidence] = Field(min_length=1, max_length=8)
    guidance_ids: list[ID] = Field(min_length=1, max_length=1)
    uncertainty: Text


class CoverageProposal(StrictModel):
    photo_id: ID
    guidance_id: ID
    coverage: Literal["visible", "partial", "obscured", "out_of_frame", "unknown"]
    note: Text


class VisualAssessmentProposal(StrictModel):
    hazards: list[HazardProposal] = Field(max_length=60)
    coverage: list[CoverageProposal] = Field(max_length=280)
    follow_up_requests: list[Text] = Field(max_length=40)


class AssessmentOutput(StrictModel):
    hazards: list[Hazard] = Field(max_length=60)
    coverage: list[Coverage] = Field(max_length=280)
    follow_up_requests: list[Text] = Field(max_length=40)


class ReviewInput(Revisions):
    decision: Literal["approve", "edit", "reject", "defer"]
    observation: Text | None = None
    priority: Priority | None = None
    suggested_action: Text | None = None
    note: str = Field(default="", max_length=2000)
    anchor: Anchor | None = None
    remove_anchor: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if self.anchor and self.remove_anchor:
            raise ValueError("Cannot replace and remove anchor together")
        if self.decision == "edit" and not any((self.observation, self.priority, self.suggested_action, self.anchor, self.remove_anchor)):
            raise ValueError("An edit must change at least one field")
        if self.decision == "defer" and not self.note.strip():
            raise ValueError("Deferred findings need a follow-up note")
        return self


class DraftItem(StrictModel):
    hazard_id: ID
    # These must match reviewed values; prose cannot replace trusted fields.
    observation: Text
    suggested_action: Text
    priority: Priority


class DraftOutput(StrictModel):
    title: Literal["Piano d’azione per la casa"]
    items: list[DraftItem] = Field(max_length=60)


class GuidanceSource(StrictModel):
    title: Text
    url: HttpUrl
    verified_on: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    applicability: Text


class GuidanceEntry(StrictModel):
    id: ID
    category: ID
    question: Text
    action: Text


class GuidanceChecklist(StrictModel):
    schema_version: Literal[1]
    version: ID
    source: GuidanceSource
    entries: list[GuidanceEntry] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("Duplicate guidance IDs")
        return self


class FunctionalProfile(StrictModel):
    schema_version: Literal[1] = 1
    confirmed: bool
    profile: Profile
    confirmed_facts: list[ClinicalClaim]
    unconfirmed_facts: list[ClinicalClaim]
    unknown_fields: list[str]


class HazardFinding(Hazard):
    id: ID
    producer: str | None
    provenance: str | None
    review: dict | None


class RoomPhoto(StrictModel):
    id: ID
    role: Literal["overview", "detail"]
    sha256: str | None
    asset_url: str


class RoomBundle(StrictModel):
    id: ID
    label: str
    status: Literal["ready", "partial", "unknown"]
    reconstruction_status: str
    reconstruction_kind: str | None
    provenance: str | None
    anchor_eligibility: str | None
    selected_photo_ids: list[ID]
    analysis_sha256: str | None
    checkpoint_sha256: str | None
    vggt_commit: str | None
    runtime: dict | None
    scale_state: str
    scale_factor: float | None
    calibration: dict | None
    floor_state: str
    gravity_state: str
    gravity_up: Vector | None
    floor_plane: dict | None
    alignment_state: str
    coordinate_convention: str | None
    raw_coordinate_units: Literal["relative", "m"]
    display_transform: Transform4 | None
    bounds: tuple[Vector, Vector] | None
    input_revision: int | None
    reconstruction_job_id: ID | None
    manifest_url: str | None
    analysis_url: str | None
    warnings: list[str | dict]
    ply_url: str | None
    photos: list[RoomPhoto]


class RoomPlacement(StrictModel):
    scene_id: ID
    reference_scene_id: ID
    transform_to_reference: Transform4
    coordinate_space: Literal["component_metric_coordinates", "raw_scene_relative"]
    coordinate_units: Literal["m", "relative"]
    scale_factor_m_per_unit: float | None


class HouseReconstruction(StrictModel):
    status: Literal["unknown", "single_room", "connected", "partial"]
    reconstruction_state: Literal["ready", "partial", "unknown"]
    components: list[list[ID]]
    links: list[dict]
    placements: list[RoomPlacement]
    registered_scene_ids: list[ID]
    unregistered_scene_ids: list[ID]
    composition: str


class AvatarStyle(StrictModel):
    walker: bool


class AvatarArtifact(StrictModel):
    """Frontend contract: show preview_url as a plain image, or load glb_url into any glTF viewer."""
    status: str | None = None
    provenance: str | None = None
    reason: str | None = None
    format: Literal["glb"] | None = None
    sha256: str | None = None
    profile_sha256: str | None = None
    generator: str | None = None
    blender_version: str | None = None
    mpfb_version: str | None = None
    illustrative_only: bool | None = None
    clinical_text_embedded: Literal[False] | None = None
    mesh_count: int | None = None
    rigged: bool | None = None
    asset_pack_sha256: str | None = None
    profile_json_sha256: str | None = None
    has_walker: bool | None = None
    callout_ids: list[str] | None = None
    style: AvatarStyle | None = None
    preview_sha256: str | None = None
    preview_width: int | None = None
    preview_height: int | None = None
    asset_id: ID | None = None
    preview_asset_id: ID | None = None
    profile_json_asset_id: ID | None = None
    glb_url: str | None = None
    preview_url: str | None = None
    profile_json_url: str | None = None


class AssessmentBundle(StrictModel):
    schema_version: Literal[1]
    assessment_id: ID
    input_revision: int
    review_revision: int
    profile_confirmed: bool
    profile: Profile
    clinical_facts: list[ClinicalClaim]
    functional_profile: FunctionalProfile
    clinical_documents: list[dict]
    rooms: list[RoomBundle]
    house: HouseReconstruction
    hazards: list[HazardFinding]
    coverage: list[Coverage]
    follow_up_requests: list[str]
    anchor_abstentions: list[dict]
    avatar: AvatarArtifact
    provenance: dict
