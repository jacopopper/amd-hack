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


class AssessmentEdit(AssessmentInput):
    expected_input_revision: int = Field(ge=1)


class Revisions(StrictModel):
    expected_input_revision: int = Field(ge=1)
    expected_review_revision: int = Field(ge=0)


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
