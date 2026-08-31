from pydantic import BaseModel
from typing import Optional, List


class FileSlot(BaseModel):
    id: str
    label: str
    enabled: bool = False
    s3_bucket: Optional[str] = ""
    s3_key: Optional[str] = ""
    description: str = ""
    required: bool = False
    isCustom: bool = False


class ScoringWeights(BaseModel):
    charge_fields: int = 25
    address_fields: int = 25
    date_fields: int = 25
    amount_fields: int = 25


class ProjectConfig(BaseModel):
    project_id: str
    project_name: str
    cloudwatch_log_group: str = ""
    email_recipients: List[str] = []
    target_api_url: str = ""
    file_slots: List[FileSlot] = []
    scoring_weights: ScoringWeights = ScoringWeights()
    log_window_hours: int = 24
    invoice_filter: Optional[str] = ""
    notify_email: bool = True
    mandatory_fields: List[str] = []
    status: str = "incomplete"  # incomplete | configured | never_tested
    last_tested: Optional[str] = None
    last_score: Optional[float] = None


class ProjectCreate(BaseModel):
    project_id: str
    project_name: str
    cloudwatch_log_group: str = ""
    email_recipients: List[str] = []
    target_api_url: str = ""
    file_slots: List[FileSlot] = []
    scoring_weights: ScoringWeights = ScoringWeights()
    log_window_hours: int = 24
    invoice_filter: Optional[str] = ""
    notify_email: bool = True
    mandatory_fields: List[str] = []
    status: str = "configured"


class ProjectUpdate(BaseModel):
    project_name: Optional[str] = None
    cloudwatch_log_group: Optional[str] = None
    email_recipients: Optional[List[str]] = None
    target_api_url: Optional[str] = None
    file_slots: Optional[List[FileSlot]] = None
    scoring_weights: Optional[ScoringWeights] = None
    log_window_hours: Optional[int] = None
    invoice_filter: Optional[str] = None
    notify_email: Optional[bool] = None
    mandatory_fields: Optional[List[str]] = None
    status: Optional[str] = None
    last_tested: Optional[str] = None
    last_score: Optional[float] = None
