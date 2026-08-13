from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


CaseStage = Literal["arbitration", "litigation"]
PartySide = Literal["worker", "employer"]


class RequestCodeInput(BaseModel):
    email: EmailStr


class VerifyCodeInput(RequestCodeInput):
    code: str = Field(min_length=6, max_length=6)


class PasswordLoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class CreateCaseInput(BaseModel):
    title: str = Field(default="劳动争议案件", min_length=1, max_length=200)
    case_stage: CaseStage
    party_side: PartySide
    facts: str = Field(default="", max_length=20000)
    claims_text: str = Field(default="", max_length=10000)


class UpdateCaseInput(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    data: dict[str, Any] | None = None


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    consent_cloud_processing: bool = False


class UpdateEvidenceInput(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    purpose: str | None = Field(default=None, max_length=3000)


class RedeemInput(BaseModel):
    code: str = Field(min_length=4, max_length=100)


class ClaimCalculationInput(BaseModel):
    kind: str
    inputs: dict[str, Any]


class LegalSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class InternalGenerationJobInput(BaseModel):
    version: int = Field(default=1, ge=1, le=1)
    job_id: str = Field(min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)


class InternalEvidenceAnalysisInput(BaseModel):
    version: int = Field(default=1, ge=1, le=1)
    evidence_id: str = Field(min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)


class InternalOtpInput(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class InternalCaseAnalysisInput(BaseModel):
    facts: str = Field(min_length=1, max_length=20000)
    claims_text: str = Field(min_length=1, max_length=10000)
    supplement: str = Field(default="", max_length=20000)
    current_data: dict[str, Any] = Field(default_factory=dict)
    round: Literal[1, 2] = 1


class InternalLegalSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class ExtractionPatch(BaseModel):
    """Only these case-data keys may be returned by a configured language model."""

    model_config = ConfigDict(extra="forbid")

    parties: dict[str, Any] | None = None
    employment_facts: dict[str, Any] | None = None
    arbitration: dict[str, Any] | None = None
    claims: list[dict[str, Any]] | None = None
    unresolved_conflicts: list[str] | None = None
    evidence_gaps: list[str] | None = None
