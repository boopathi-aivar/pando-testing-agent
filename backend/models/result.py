from pydantic import BaseModel
from typing import Optional, List


class FieldValidation(BaseModel):
    field_name: str
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    status: str  # correct | wrong | missing
    source_used: str = ""


class LogSummary(BaseModel):
    errors: List[str] = []
    warnings: List[str] = []
    execution_duration_ms: int = 0
    cold_start: bool = False


class TestResult(BaseModel):
    result_id: str
    project_id: str
    invoice_number: str
    timestamp: str
    overall_score: float
    status: str  # passed | warning | failed
    vendor_name: Optional[str] = None
    api_status: Optional[int] = None    # HTTP status from the Lambda API call (200/400/500 etc.)
    field_validations: List[FieldValidation] = []
    prompt_suggestions: List[str] = []
    log_summary: LogSummary = LogSummary()
    raw_payload: dict = {}
