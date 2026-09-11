from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator
from typing import Any, Optional, List
import json


def _to_str(v: Any) -> Optional[str]:
    """Coerce LLM / DynamoDB values (int, float, dict, list) to a string."""
    if v is None or v == "":
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


def _to_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list):
        return [str(v)]
    return [str(i) for i in v if i is not None]


class FieldValidation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    field_name: str = "unknown"
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    status: str = "missing"  # correct | wrong | missing | unverified
    source_used: str = ""
    is_mandatory: bool = False
    reason: Optional[str] = None

    @field_validator("expected_value", "actual_value", "reason", mode="before")
    @classmethod
    def coerce_optional_str(cls, v: Any) -> Optional[str]:
        return _to_str(v)

    @field_validator("field_name", "status", "source_used", mode="before")
    @classmethod
    def coerce_required_str(cls, v: Any) -> str:
        return _to_str(v) or ""

    @field_validator("is_mandatory", mode="before")
    @classmethod
    def coerce_mandatory(cls, v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes")
        return bool(v)


class MandatoryFieldsResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total: int = 0
    passed: int = 0
    failed: int = 0
    failed_fields: List[str] = []

    @field_validator("failed_fields", mode="before")
    @classmethod
    def coerce_failed_fields(cls, v: Any) -> list[str]:
        return _to_str_list(v)

    @field_validator("total", "passed", "failed", mode="before")
    @classmethod
    def coerce_counts(cls, v: Any) -> int:
        if v is None or v == "":
            return 0
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


class LogSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    errors: List[str] = []
    warnings: List[str] = []
    execution_duration_ms: int = 0
    cold_start: bool = False

    @field_validator("errors", "warnings", mode="before")
    @classmethod
    def coerce_str_list(cls, v: Any) -> list[str]:
        return _to_str_list(v)

    @field_validator("execution_duration_ms", mode="before")
    @classmethod
    def coerce_int(cls, v: Any) -> int:
        if v is None or v == "":
            return 0
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    @field_validator("cold_start", mode="before")
    @classmethod
    def coerce_bool(cls, v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes")
        return bool(v)


class TestResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result_id: str
    project_id: str
    invoice_number: str = "unknown"
    timestamp: str = ""
    overall_score: float = 0.0
    status: str = "failed"  # passed | warning | failed
    vendor_name: Optional[str] = None
    api_status: Optional[int] = None
    field_validations: List[FieldValidation] = []
    prompt_suggestions: List[str] = []
    log_summary: LogSummary = LogSummary()
    raw_payload: dict = {}
    mandatory_fields_result: MandatoryFieldsResult = MandatoryFieldsResult()

    @field_validator("invoice_number", "timestamp", "status", "result_id", "project_id", mode="before")
    @classmethod
    def coerce_str_fields(cls, v: Any, info: ValidationInfo) -> str:
        s = _to_str(v) or ""
        if info.field_name == "invoice_number" and not s:
            return "unknown"
        if info.field_name == "status" and not s:
            return "failed"
        return s

    @field_validator("vendor_name", mode="before")
    @classmethod
    def coerce_vendor(cls, v: Any) -> Optional[str]:
        return _to_str(v)

    @field_validator("overall_score", mode="before")
    @classmethod
    def coerce_score(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    @field_validator("api_status", mode="before")
    @classmethod
    def coerce_api_status(cls, v: Any) -> Optional[int]:
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    @field_validator("prompt_suggestions", mode="before")
    @classmethod
    def coerce_suggestions(cls, v: Any) -> list[str]:
        return _to_str_list(v)

    @field_validator("log_summary", mode="before")
    @classmethod
    def coerce_log_summary(cls, v: Any) -> Any:
        return v if isinstance(v, dict) else {}

    @field_validator("field_validations", mode="before")
    @classmethod
    def coerce_validations(cls, v: Any) -> list:
        if not isinstance(v, list):
            return []
        return [i for i in v if isinstance(i, dict)]

    @field_validator("raw_payload", mode="before")
    @classmethod
    def coerce_payload(cls, v: Any) -> dict:
        return v if isinstance(v, dict) else {}

    @field_validator("mandatory_fields_result", mode="before")
    @classmethod
    def coerce_mandatory_result(cls, v: Any) -> Any:
        return v if isinstance(v, dict) else {}
