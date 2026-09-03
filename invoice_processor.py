import json
import logging
import boto3
import os
import time
import requests
import ssl
import smtplib
import re
from typing import Dict, Any, Optional, List, Tuple
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from pathlib import Path
from datetime import datetime, timedelta
import tempfile
from botocore.exceptions import EndpointConnectionError, ClientError, ReadTimeoutError
from urllib.parse import unquote_plus
from schema import TOOL_SCHEMA
from botocore.config import Config
from difflib import SequenceMatcher
import csv
from io import BytesIO

# ---------- CONFIGURE LOGGING ----------
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

try:
    import fitz  # PyMuPDF
    from PIL import Image
    PDF2IMAGE_AVAILABLE = True
    # Disable PIL decompression bomb warning
    Image.MAX_IMAGE_PIXELS = None
except ImportError:
    PDF2IMAGE_AVAILABLE = False
    logger.warning("pymupdf (fitz) or PIL not available, Buddy Moore Trucking cropping will be disabled")

# ---------- COST TRACKING UTILITIES ----------
class ClaudeCostTracker:
    """Centralized cost tracking for Claude API calls."""
    
    # Claude 3.5 Sonnet pricing (as of 2024)
    INPUT_TOKEN_COST = 0.000003  # $3 per 1M input tokens
    OUTPUT_TOKEN_COST = 0.000015  # $15 per 1M output tokens
    
    def __init__(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.call_count = 0
        
    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost for a single Claude call."""
        input_cost = input_tokens * self.INPUT_TOKEN_COST
        output_cost = output_tokens * self.OUTPUT_TOKEN_COST
        return input_cost + output_cost
    
    def track_call(self, input_tokens: int, output_tokens: int, operation: str = "claude_call"):
        """Track a Claude API call and log detailed cost information."""
        cost = self.calculate_cost(input_tokens, output_tokens)
        
        # Update totals
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost += cost
        self.call_count += 1
        
        # Log detailed cost information
        logger.info(f"=== CLAUDE COST TRACKING - {operation.upper()} ===")
        logger.info(f"Input tokens: {input_tokens:,}")
        logger.info(f"Output tokens: {output_tokens:,}")
        logger.info(f"Input cost: ${input_tokens * self.INPUT_TOKEN_COST:.6f}")
        logger.info(f"Output cost: ${output_tokens * self.OUTPUT_TOKEN_COST:.6f}")
        logger.info(f"Total cost for this call: ${cost:.6f}")
        logger.info(f"Running totals - Calls: {self.call_count}, Input: {self.total_input_tokens:,}, Output: {self.total_output_tokens:,}, Total cost: ${self.total_cost:.6f}")
        logger.info(f"=== END CLAUDE COST TRACKING ===")
        
        return cost
    
    def get_summary(self) -> Dict[str, Any]:
        """Get cost tracking summary."""
        return {
            "total_calls": self.call_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": self.total_cost,
            "average_cost_per_call": self.total_cost / self.call_count if self.call_count > 0 else 0
        }
    
    def log_final_summary(self, job_id: str = None):
        """Log final cost summary for the entire processing job."""
        summary = self.get_summary()
        job_info = f" for job {job_id}" if job_id else ""
        
        logger.info(f"=== FINAL CLAUDE COST SUMMARY{job_info} ===")
        logger.info(f"Total Claude API calls: {summary['total_calls']}")
        logger.info(f"Total input tokens: {summary['total_input_tokens']:,}")
        logger.info(f"Total output tokens: {summary['total_output_tokens']:,}")
        logger.info(f"Total Claude cost: ${summary['total_cost']:.6f}")
        logger.info(f"Average cost per call: ${summary['average_cost_per_call']:.6f}")
        logger.info(f"=== END FINAL CLAUDE COST SUMMARY ===")
        
        return summary

# Global cost tracker instance
cost_tracker = ClaudeCostTracker()


def _format_json_for_log(obj: Any) -> str:
    """Format a dict/list or JSON-serializable object as pretty-printed JSON for logs."""
    try:
        return json.dumps(obj, indent=2, default=str, ensure_ascii=False)
    except Exception:
        return str(obj)


# ---------- LOAD ENV VARIABLES ----------
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', '')
HAIKU_BEDROCK_MODEL_ID = os.environ.get('HAIKU_BEDROCK_MODEL_ID', BEDROCK_MODEL_ID)
REGION = os.environ.get('REGION', 'us-east-1')
FROM_EMAIL = os.environ.get('FROM_EMAIL', '')
API_ENDPOINT = ''
INTERNAL_TOKEN = ''
TRANSACTION_STATUS_PAYLOAD_URL = ''
REJECTION_ENDPOINT = ''
CLIENT_ID = '36'
STATUS_TRACKER_BUCKET = os.environ.get('S3_STATUS_BUCKET', '')
STATUS_TRACKER_PREFIX = 'GE-Status-record'
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', '')
S3_BUCKET = os.environ.get('S3_BUCKET', '')
S3_PREFIX = os.environ.get('S3_BUCKET_KEY', '')
# Archived invoice crops (e.g. J & R Schugel TRUCK#–DESCRIPTION). Override with CROPPED_IMAGES_S3_PREFIX (trailing / added if missing).
_raw_cropped_prefix = os.environ.get("CROPPED_IMAGES_S3_PREFIX", "cropped images/").strip()
CROPPED_IMAGES_S3_PREFIX = _raw_cropped_prefix if _raw_cropped_prefix.endswith("/") else _raw_cropped_prefix + "/"
USER_S3_BUCKET = os.environ.get('USER_S3_BUCKET', '')
AUTHORIZATION_TOKEN = os.environ.get('AUTHORIZATION_TOKEN', '')
SMTP_SERVER = os.environ.get('SMTP_SERVER', '')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_SECRET_NAME = os.environ.get('SMTP_SECRET_NAME', '')
APP_SECRET_NAME = os.environ.get('APP_SECRET_NAME', '')

# ── Pando Testing Agent integration ──────────────────────────────────────────
# Set these in the Lambda console after deploying the testing agent.
# TESTING_AGENT_URL  = API Gateway URL from SAM Outputs (e.g. https://xxx.execute-api.us-east-1.amazonaws.com)
# TESTING_AGENT_KEY  = same value as INTAKE_API_KEY in the testing agent backend
# TESTING_PROJECT_ID = project_id of the matching project in the testing agent (e.g. "ge-freight")
# Accept both TESTING_AGENT_KEY (current Lambda console name) and the older
# TESTING_AGENT_API_KEY name for backward compatibility.
TESTING_AGENT_URL     = os.environ.get('TESTING_AGENT_URL', '')
TESTING_AGENT_API_KEY = os.environ.get('TESTING_AGENT_KEY', '') or os.environ.get('TESTING_AGENT_API_KEY', '')
TESTING_PROJECT_ID    = os.environ.get('TESTING_PROJECT_ID', '')


def _post_to_testing_agent(
    final_payload: dict,
    api_response_data: dict,
    original_input_bucket: str,
    original_input_key: str,
    llm_response: dict = None,
    prompt_text: str = "",
) -> None:
    """
    Fire-and-forget: push invoice data to the Pando Testing Agent for independent validation.
    Called immediately after the Pando API call so the testing agent can score the result.
    Never raises — invoice processing must never be blocked by this call.
    Timeout is 5 seconds to avoid adding latency to Lambda execution.
    """
    if not TESTING_AGENT_URL or not TESTING_AGENT_API_KEY:
        logger.info(
            "[TestingAgent] Skipped — TESTING_AGENT_URL or TESTING_AGENT_KEY "
            "env var is not set on this Lambda."
        )
        return

    try:
        payload_data = {}
        if final_payload and "data" in final_payload and final_payload["data"]:
            payload_data = final_payload["data"][0]

        # Ensure the PDF S3 location is inside payload.custom so the
        # testing agent can download and independently validate it
        custom = dict(payload_data.get("custom") or {})
        if not custom.get("attachment_bucket") and original_input_bucket:
            custom["attachment_bucket"] = original_input_bucket
            custom["attachment_key"]    = original_input_key
            payload_data = {**payload_data, "custom": custom}

        body = {
            "invoice_number":         str(payload_data.get("invoice_number") or "unknown"),
            "payload":                payload_data,
            "llm_response":           llm_response or {},
            "prompt":                 prompt_text or "",
            "api_status":             api_response_data.get("status_code"),
            "execution_duration_ms":  0,
            "cold_start":             False,
            "errors":                 [],
            "warnings":               [],
        }
        # project_id tells the testing agent which project config to validate
        # against — without it, intake cannot be routed to a project.
        if TESTING_PROJECT_ID:
            body["project_id"] = TESTING_PROJECT_ID

        if not TESTING_PROJECT_ID:
            logger.warning(
                "[TestingAgent] TESTING_PROJECT_ID is not set — intake may fail "
                "to route unless s3_bucket/log_group matching succeeds instead."
            )

        resp = requests.post(
            f"{TESTING_AGENT_URL}/api/intake",
            headers={
                "Content-Type": "application/json",
                "X-Intake-Key": TESTING_AGENT_API_KEY,
            },
            json=body,
            timeout=5,
        )
        logger.info(
            f"[TestingAgent] Pushed invoice {body['invoice_number']} → "
            f"status {resp.status_code} | response={resp.text[:300]}"
        )

    except Exception as e:
        logger.warning(f"[TestingAgent] Push failed (non-critical): {e}")


# ---------------------------------------------------------------------------
# Canonical Location Mapping — Unified structure combining exact-name matches,
# substring-name matches, name-only AND-groups, and address+city paired patterns
# for each location. See the per-key comments on the dict below for the full
# matching semantics (name_equals / name_contains / name_and_groups /
# address_city_patterns).
# ---------------------------------------------------------------------------
CANONICAL_LOCATION_MAPPING = {
    # ------------------------------------------------------------------------
    # Per-canonical-value config keys:
    #   "name_equals"           : list of strings — the NAME field must EQUAL this
    #                              string exactly (case-insensitive, space-insensitive).
    #                              Used for short/ambiguous codes (e.g. "MRO", "APF")
    #                              that would produce false positives if matched as
    #                              a substring.
    #   "name_contains"         : list of strings — match if ANY one is a
    #                              case-insensitive, space-insensitive SUBSTRING of
    #                              the NAME field.
    #   "name_and_groups"       : list of groups (each a list of strings) — ALL
    #                              strings in a group must be present as substrings
    #                              WITHIN THE NAME FIELD ITSELF (order independent).
    #                              E.g. ["GE", "CAMDEN"] matches "GE Appliances Camden".
    #   "address_contains"      : list of strings — match if ADDRESS field contains
    #                              any one as a case/space-insensitive SUBSTRING.
    #                              No city required — address alone is sufficient.
    #   "address_city_patterns" : list of {"address_contains": str, "city_equals": str}.
    #                              BOTH conditions must be true together:
    #                                - ADDRESS field contains the given substring
    #                                - CITY field EQUALS the given city exactly
    #                              If the city matches but the address does not
    #                              contain the required substring (or vice versa),
    #                              this is NOT a match — city alone is never
    #                              sufficient to trigger canonical mapping.
    # ------------------------------------------------------------------------
    "AP1": {
        "name_equals": [],
        "name_contains": ["AP1", "GE BLDG 1", "GE BUILDING 1", "AP#1"],
        "name_and_groups": [],
        "address_city_patterns": []
    },
    "AP2": {
        "name_equals": [],
        "name_contains": ["AP2", "GE BLDG 2", "GE BUILDING 2", "AP#2"],
        "name_and_groups": [],
        "address_city_patterns": []
    },
    "AP3": {
        "name_equals": [],
        "name_contains": ["AP3", "GE BLDG 3", "GE BUILDING 3", "AP#3"],
        "name_and_groups": [],
        "address_city_patterns": []
    },
    "AP4": {
        "name_equals": [],
        "name_contains": ["AP4", "GE BLDG 4", "GE BUILDING 4", "AP#4"],
        "name_and_groups": [],
        "address_city_patterns": []
    },
    "AP5": {
        "name_equals": [],
        "name_contains": ["AP5", "GE BLDG 5", "GE BUILDING 5", "AP#5"],
        "name_and_groups": [],
        "address_city_patterns": []
    },
    "AP35": {
        "name_equals": [],
        "name_contains": ["AP35", "AP#35", "AP# 35"],
        "name_and_groups": [],
        "address_contains": ["APPL PARK AP35"],
        "address_city_patterns": []
    },
    "KLC": {
        "name_equals": [],
        "name_contains": ["KLC", "KENTUCKY LOG"],
        "name_and_groups": [],
        "address_city_patterns": [{"address_contains": "2501 Export", "city_equals": "Louisville"}]
    },
    "MRO": {
        "name_equals": ["MRO"],
        "name_contains": ["MONOGRAM", "MONO"],
        "name_and_groups": [],
        "address_city_patterns": [{"address_contains": "1020 TENNESSEE", "city_equals": "SELMER"}]
    },
    "CAM": {
        "name_equals": [],
        "name_contains": ["CAM"],
        "name_and_groups": [["GE", "CAMDEN"]],
        "address_city_patterns": [{"address_contains": "50 Haier", "city_equals": "CAMDEN"}]
    },
    "DPF": {
        "name_equals": [],
        "name_contains": ["DPO", "DPF"],
        "name_and_groups": [["GE", "DECATUR"]],
        "address_city_patterns": [{"address_contains": "2328 Point Mallard", "city_equals": "Decatur"}]
    },
    "SLC": {
        "name_equals": [],
        "name_contains": ["SLC", "SOUTHERN LOG"],
        "name_and_groups": [],
        "address_city_patterns": []
    },
    "RPF": {
        "name_equals": [],
        "name_contains": ["RPF", "ROPER"],
        "name_and_groups": [],
        "address_city_patterns": [{"address_contains": "1507 Broomtown", "city_equals": "LaFayette"}]
    },
    "CMC": {
        "name_equals": [],
        "name_contains": ["CMC", "Container Mgmt", "Container Management"],
        "name_and_groups": [],
        "address_city_patterns": []
    },
    "APF": {
        "name_equals": ["APF"],
        "name_contains": [],
        "name_and_groups": [["GE CONSUMER", "INDUSTRIAL G02"]],
        "address_city_patterns": []
    },
    "APX": {
        "name_equals": [],
        "name_contains": ["APX"],
        "name_and_groups": [],
        "address_city_patterns": [{"address_contains": "6001 Global Distribution Way", "city_equals": "Louisville"}]
    },
    "DERBY": {
        "name_equals": [],
        "name_contains": [],
        "name_and_groups": [],
        "address_city_patterns": [{"address_contains": "4451 Robards", "city_equals": "Louisville"}]
    },
}

# Section header keywords that indicate source (Ship From) or destination (Ship To)
_CANONICAL_SOURCE_HEADERS = frozenset([
    "ship from", "shipper", "shipper's location", "shippers location",
    "origin", "source location", "consignor", "orig:",
    # NOTE: standalone "from" / "to" intentionally excluded — memorandum-type
    # BOL documents use From/To with REVERSED perspective vs the freight invoice,
    # which would incorrectly flip source and destination canonical values.
])
_CANONICAL_DEST_HEADERS = frozenset([
    "ship to", "consignee", "consignee location", "destination",
    "deliver to", "delivery", "recipient", "dest:",
    # NOTE: standalone "to" excluded for the same reason as "from" above.
])


def _extract_name_from_address_string(address_string: str) -> str:
    """
    Extract just the company/location name from a full address string.
    
    Examples:
        "JONES PLASTIC 470 BENTON INDUSTRIAL RD CAMDEN, TN 38320" → "JONES PLASTIC"
        "GE APPLIANCES AP5 5 APPLIANCE PARK LOUISVILLE, KY 40225" → "GE APPLIANCES AP5"
        "MRO 1020 TENNESSEE AVE SELMER" → "MRO"
    
    Args:
        address_string: Full address string from KV
    
    Returns:
        Extracted name (first line/part before street address)
    """
    if not address_string:
        return ""
    
    import re
    
    # Common patterns that indicate the START of a street address (not the name)
    street_indicators = [
        r'\s+\d+\s+[A-Z]',  # Space + digits + space + capital letter (e.g., " 470 BENTON")
        r'\s+\d+[A-Z]',     # Space + digits + capital letter (e.g., " 470BENTON")
        r'\bPO\s*BOX\b',    # PO BOX variations
        r'\bP\.?\s*O\.?\s*BOX\b',
    ]
    
    # Try to find where the street address starts
    for pattern in street_indicators:
        match = re.search(pattern, address_string, re.IGNORECASE)
        if match:
            # Everything before the matched pattern is likely the name
            name_part = address_string[:match.start()].strip()
            if name_part:
                # Remove common trailing punctuation
                name_part = name_part.rstrip(',-:')
                logger.info(f"Name extraction: Found street pattern '{pattern}' in address, extracted name: '{name_part}'")
                return name_part
    
    # If no street indicator found, look for common city/state patterns at the end
    # and take everything before that
    # Pattern: ends with ", STATE ZIP" or ", CITY, STATE ZIP"
    city_state_pattern = r',\s+[A-Z]{2}\s+\d{5}(-\d{4})?$'
    match = re.search(city_state_pattern, address_string)
    if match:
        # Remove the state+zip, then look for the city
        before_state = address_string[:match.start()]
        # Now look for the last comma which separates city from address
        parts = before_state.rsplit(',', 1)
        if len(parts) == 2:
            # First part is likely name + address, second part is city
            # Now split the first part on newline or multiple spaces
            name_addr = parts[0].strip()
            # Take only the first line or part before address number
            lines = name_addr.split('\n')
            if lines:
                logger.info(f"Name extraction: Using city/state pattern, extracted name: '{lines[0].strip()}'")
                return lines[0].strip()
            logger.info(f"Name extraction: Using city/state pattern, extracted name: '{name_addr}'")
            return name_addr
    
    # Fallback: split by newline and take first line, or split by multiple spaces
    lines = address_string.split('\n')
    if len(lines) > 1:
        logger.info(f"Name extraction: Multiple lines found, using first line: '{lines[0].strip()}'")
        return lines[0].strip()
    
    # If single line, try to split on multiple spaces (often name and address are separated by extra spaces)
    parts = re.split(r'\s{2,}', address_string)
    if len(parts) > 1:
        logger.info(f"Name extraction: Multiple space delimiter found, using first part: '{parts[0].strip()}'")
        return parts[0].strip()
    
    # Last resort: take first ~2 words (likely the company name)
    # This is a conservative approach - better to take too little than too much
    words = address_string.split()
    if len(words) > 4:
        # Likely "COMPANY NAME ADDR1 ADDR2 CITY STATE ZIP"
        # Take first 2 words as name (conservative)
        extracted = ' '.join(words[:2])
        logger.info(f"Name extraction: Last resort, taking first 2 words: '{extracted}'")
        return extracted
    
    # Give up and return the whole thing (will likely not match, which is fine)
    logger.warning(f"Name extraction: Could not extract name from address '{address_string[:50]}...', returning full string")
    return address_string.strip()


def _extract_address_and_city_from_address_string(address_string: str) -> Tuple[str, str]:
    """
    Extract the street/address portion and the city from a full combined KV value
    (used so KV-based canonical matching can apply address_city_patterns, which need
    the ADDRESS and CITY fields separated — city alone must never trigger a match).

    Examples:
        "MRO 1020 TENNESSEE AVE SELMER, TN 38375" → ("1020 TENNESSEE AVE", "SELMER")
        "GE APPLIANCES 2501 EXPORT DR LOUISVILLE, KY 40225" → ("2501 EXPORT DR", "LOUISVILLE")
        "JONES PLASTIC" (no street/city pattern found) → ("", "")

    Returns:
        (address, city) — either or both may be "" if not confidently extractable.
    """
    if not address_string:
        return "", ""

    # Pattern: "..., STATE ZIP" or "..., CITY, STATE ZIP" at the end of the string
    city_state_pattern = r',\s*([A-Za-z .]+?),?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?$'
    match = re.search(city_state_pattern, address_string)
    city = ""
    address_part = address_string
    if match:
        city = match.group(1).strip()
        address_part = address_string[:match.start()].strip()
    else:
        # Fallback: try "CITY, ST ZIP" without a leading comma-separated address (e.g. just "SELMER, TN 38375")
        simple_city_pattern = r'([A-Za-z .]+?),\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?$'
        m2 = re.search(simple_city_pattern, address_string)
        if m2:
            city = m2.group(1).strip()
            address_part = address_string[:m2.start()].strip()

    # From the remaining address_part, strip the leading name portion (reuse street indicators)
    street_indicators = [
        r'\s+\d+\s+[A-Z]',
        r'\s+\d+[A-Z]',
        r'\bPO\s*BOX\b',
        r'\bP\.?\s*O\.?\s*BOX\b',
    ]
    address = ""
    for pattern in street_indicators:
        m3 = re.search(pattern, address_part, re.IGNORECASE)
        if m3:
            # Street starts at the first digit within the matched span
            digit_match = re.search(r'\d', address_part[m3.start():])
            if digit_match:
                street_start = m3.start() + digit_match.start()
                address = address_part[street_start:].strip()
            break

    return address, city


def _normalize_for_matching(text: str) -> str:
    """Normalize text for space-insensitive matching: remove all spaces, lowercase."""
    if not text:
        return ""
    return re.sub(r'\s+', '', text.lower())


def _name_equals(name_value: str, target: str) -> bool:
    """Case-insensitive, space-insensitive EXACT equality between name_value and target."""
    if not name_value or not target:
        return False
    return _normalize_for_matching(name_value) == _normalize_for_matching(target)


def _name_contains(name_value: str, substring: str) -> bool:
    """
    Case-insensitive, word-boundary SUBSTRING match: `substring` must appear in name_value
    as its own distinct token, not merely embedded inside a different, longer word.

    A real word boundary (start/end of string, or a non-alphanumeric character such as a
    space, comma, hyphen, or parenthesis) is required immediately before and after the
    matched span. Whitespace WITHIN `substring` itself is tolerated between characters
    (e.g. "AP4" also matches text like "AP 4").

    Examples:
        _name_contains("JONES PLASTIC & ENGINEERING CORP CAMDEN", "CAM")  -> False
            ("CAM" is only the leading letters of the unrelated word "CAMDEN" — no boundary
             between "CAM" and the "DEN" that follows it directly.)
        _name_contains("GE (CAM)", "CAM")                                -> True
        _name_contains("APF GE APPLIANCES-COCONUT(AP4)", "AP4")          -> True
    """
    if not name_value or not substring:
        return False
    chars = [c for c in substring if not c.isspace()]
    if not chars:
        return False
    pattern = r'\b' + r'\s*'.join(re.escape(c) for c in chars) + r'\b'
    return re.search(pattern, name_value, re.IGNORECASE) is not None


def _city_equals(city_value: str, target_city: str) -> bool:
    """Case-insensitive, space-insensitive EXACT equality for the city field."""
    if not city_value or not target_city:
        return False
    return _normalize_for_matching(city_value) == _normalize_for_matching(target_city)


def _canonical_match_in_text(text: str):
    """
    DEPRECATED: Use _canonical_match_with_fields() instead for proper field separation.
    Kept only for backward compatibility with any external callers; not used internally.

    Return (canonical_value, variation_found) if any name_contains/name_equals/name_and_groups
    string matches in *text* using space-insensitive, case-insensitive matching. Address/city
    patterns are NOT evaluated here since this helper receives a single flat text blob with no
    field separation (city-alone matches must never trigger a canonical result).

    Returns (canonical_value, variation_string) or (None, None).
    """
    if not text:
        return None, None

    text_normalized = _normalize_for_matching(text)

    for canonical, config in CANONICAL_LOCATION_MAPPING.items():
        for variation in config.get("name_equals", []):
            if variation and _normalize_for_matching(variation) == text_normalized:
                return canonical, variation
        for variation in config.get("name_contains", []):
            if variation and _normalize_for_matching(variation) in text_normalized:
                return canonical, variation
        for group in config.get("name_and_groups", []):
            if group and all(_normalize_for_matching(s) in text_normalized for s in group):
                return canonical, " (and) ".join(group)

    return None, None


def _canonical_match_with_fields(name: str, address: str = "", city: str = "", state: str = "", full_text: str = ""):
    """
    CANONICAL MATCHING LOGIC (field-separated):

    - name_equals: NAME field must EQUAL the string exactly (case/space-insensitive).
    - name_contains: NAME field must CONTAIN the string anywhere (case/space-insensitive substring).
    - name_and_groups: ALL strings in the group must be present as substrings ACROSS the NAME,
      ADDRESS, and CITY fields COMBINED for the same location (order independent, and the strings
      do NOT need to be in the same field) — e.g. ["GE", "DECATUR"] matches name "GE APPLIANCE" +
      city "DECATUR" even though "DECATUR" is not part of the name text itself.
    - address_city_patterns: BOTH conditions must hold together:
        * ADDRESS field contains "address_contains" (substring, case/space-insensitive)
        * CITY field EQUALS "city_equals" exactly (case/space-insensitive)
      If the city matches alone but the address does NOT contain the required substring (or vice
      versa), this is NOT a match. City alone is never sufficient to trigger canonical mapping.

    The `state` and `full_text` parameters are accepted for backward-compatible call signatures
    but are not used in name/address/city matching (kept as no-ops to avoid breaking callers).

    Args:
        name: Location name (source_name or destination_name)
        address: Location address (street line)
        city: Location city — used ONLY paired with address_city_patterns, never alone
        state: Location state (unused, kept for signature compatibility)
        full_text: unused, kept for signature compatibility

    Returns:
        (canonical_value, variation_string) or (None, None)
    """
    name = name or ""
    address = address or ""
    city = city or ""

    if not name and not address:
        return None, None

    for canonical, config in CANONICAL_LOCATION_MAPPING.items():
        # 1) name_equals — exact match against the name field
        for variation in config.get("name_equals", []):
            if variation and _name_equals(name, variation):
                logger.info(f"Canonical match: name '{name}' EQUALS '{variation}' → {canonical}")
                return canonical, variation

        # 2) name_contains — substring match against the name field
        for variation in config.get("name_contains", []):
            if variation and _name_contains(name, variation):
                logger.info(f"Canonical match: name '{name}' CONTAINS '{variation}' → {canonical}")
                return canonical, variation

        # 3) name_and_groups — ALL strings in the group must be substrings found somewhere across
        #    name + address + city COMBINED (each string can be in any of the three fields; they
        #    do not all need to be in the name field itself).
        for group in config.get("name_and_groups", []):
            if group and all(
                _name_contains(name, s) or _name_contains(address, s) or _name_contains(city, s)
                for s in group
            ):
                variation_str = " (and) ".join(group)
                logger.info(f"Canonical match: name '{name}'/address '{address}'/city '{city}' matched AND-group '{variation_str}' (across fields) → {canonical}")
                return canonical, variation_str

        # 4) address_contains — address field contains substring (no city required).
        for addr_needle in config.get("address_contains", []):
            if addr_needle and _name_contains(address, addr_needle):
                logger.info(f"Canonical match: address '{address}' contains '{addr_needle}' → {canonical}")
                return canonical, addr_needle

        # 5) address_city_patterns — address CONTAINS substring AND city EQUALS target city.
        #    City alone (without the matching address substring) never triggers a match.
        for pattern in config.get("address_city_patterns", []):
            addr_needle = pattern.get("address_contains", "")
            city_target = pattern.get("city_equals", "")
            if not addr_needle or not city_target:
                continue
            address_ok = _name_contains(address, addr_needle)  # substring match, reuse normalizer
            city_ok = _city_equals(city, city_target)
            if address_ok and city_ok:
                variation_str = f"{addr_needle} (and) {city_target}"
                logger.info(
                    f"Canonical match: address '{address}' contains '{addr_needle}' AND city '{city}' == '{city_target}' → {canonical}"
                )
                return canonical, variation_str

    return None, None


def _invoice_date_too_old(date_str: str, months: int = 6) -> bool:
    """Return True if date_str (DD-MMM-YYYY) is more than `months` months before today."""
    if not date_str or not str(date_str).strip():
        return False
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            dt = datetime.strptime(str(date_str).strip(), fmt)
            cutoff = datetime.now() - timedelta(days=months * 30.44)
            return dt < cutoff
        except Exception:
            continue
    return False


def _extract_invoice_date_value(extracted_info: dict) -> str:
    """Pull the plain invoice_date string from structured LLM output."""
    raw = (extracted_info or {}).get("invoice_date", "")
    if isinstance(raw, dict):
        raw = raw.get("value", "") or ""
    return str(raw).strip()


def _maybe_retry_stale_invoice_date(extracted_info: dict, raw_text: str, carrier_name: str = None) -> dict:
    """If invoice_date is >6 months old, re-call Claude once with a format-only correction note."""
    inv_date = _extract_invoice_date_value(extracted_info)
    if not _invoice_date_too_old(inv_date):
        return extracted_info

    logger.warning(
        "INVOICE_DATE_VALIDATION | '%s' is more than 6 months old — retrying LLM with format correction note",
        inv_date,
    )
    date_retry_note = (
        f"\n\n⚠️ CORRECTION NEEDED: Your previous extraction returned invoice_date='{inv_date}', "
        f"which is more than 6 months in the past and is almost certainly wrong.\n"
        f"Re-extract invoice_date using the SAME invoice date field already defined in this prompt "
        f"(do not change which field to look for).\n"
        f"FORMAT RULES ONLY:\n"
        f"1. Prefer MM/DD/YYYY (4-digit year) over MM/DD/YY when both appear.\n"
        f"2. MM/DD = month then day — never swap month and day.\n"
        f"3. If only a 2-digit year (YY) is present, it means 20YY (e.g. '26' = 2026, not 2006).\n"
        f"4. Output must be DD-MMM-YYYY (e.g. 09-Jul-2026).\n"
    )
    retried = extract_information_with_claude(
        raw_text,
        carrier_name=carrier_name,
        date_retry_note=date_retry_note,
    )
    logger.info(
        "INVOICE_DATE_VALIDATION | after retry invoice_date='%s'",
        _extract_invoice_date_value(retried),
    )
    return retried


def _value_is_already_canonical(value: Any) -> bool:
    """
    True if `value` (a plain string or a structured {value, explanation, confidence} dict)
    is already one of the canonical location codes (e.g. "MRO", "AP1", "RPF").

    Used to decide, BEFORE running carrier-specific cropped-image extraction, whether
    source_name / destination_name was already resolved to a canonical code by the LLM
    (Priority 1 — BOL canonical mapping). If so, the image extraction step must NOT
    override that name — it should only fill in address/city/state details.
    """
    if isinstance(value, dict):
        value = value.get("value", "")
    v = str(value or "").strip().upper()
    if not v:
        return False
    return v in CANONICAL_LOCATION_MAPPING


def _apply_canonical_location_mapping(extracted_info: dict,
                                       freight_kvs: dict,
                                       bol_lines_by_page: dict,
                                       bol_kvs_by_page: dict = None) -> dict:
    """
    Post-process extracted_info to override source_name / destination_name
    using programmatic canonical location matching.

    Rules:
      - source and destination are resolved INDEPENDENTLY — no cross-field checks.
      - Priority: freight invoice KV pairs first, then BOL textract KV pairs.
      - Matching uses space-insensitive contains and AND-logic for complex variations.
      - When canonical match found, also sync address/city/state/zip from same section.
      - First canonical match found for each field wins.
      - Only applies to TL/LTL delivery types in ROAD-mode shipments.
      - If no canonical match is found for a field, the LLM-extracted value is kept.
    """
    source_canonical = None
    dest_canonical   = None
    source_matched_via = None   # for logging
    dest_matched_via   = None   # for logging
    source_section_kvs = {}  # Store KVs from matched source section
    dest_section_kvs = {}    # Store KVs from matched destination section

    # Known section header keywords — must stay in sync with _CANONICAL_SOURCE_HEADERS /
    # _CANONICAL_DEST_HEADERS so that KV-key matching mirrors the section headers used
    # everywhere else in this function.
    # IMPORTANT: These must match exactly to avoid false positives like "Shipper Signature/Date"
    source_kv_keys = {"shipper", "ship from", "shipper's location", "shippers location", "origin", "source location", "source name"}
    dest_kv_keys   = {"consignee", "ship to", "consignee location", "consignees location", "destination", "deliver to", "destination name"}

    def _scan_kvs(kvs: dict, label: str):
        """Return (source_canonical, src_via, src_kvs, dest_canonical, dst_via, dst_kvs) from a flat KV dict."""
        s_canon = s_via = d_canon = d_via = None
        s_kvs = {}
        d_kvs = {}
        
        for key, val in (kvs or {}).items():
            # Normalize key: lowercase, replace any punctuation with spaces, collapse runs.
            # This makes "Ship.To" match "ship to", "Shipper:" match "shipper", etc.
            key_lower = re.sub(r'[^a-z0-9 ]+', ' ', key.lower().strip())
            key_lower = re.sub(r' +', ' ', key_lower).strip()
            
            # Check if this is a source section - EXACT match only (not substring)
            # This prevents "Shipper Signature/Date" from matching "shipper"
            if key_lower in source_kv_keys:
                # Store this KV pair for later address field extraction
                s_kvs[key] = val
                if not s_canon:
                    # IMPORTANT: Sometimes Textract combines SHIPPER and SHIP TO in the same value
                    # Example: "SHIPPER: GREENVILLE...SHIP TO AP4 DOCK 4"
                    # We need to extract only the SHIPPER portion before any destination markers
                    val_for_matching = val or ''
                    
                    # Split on destination markers to get only the source portion
                    dest_markers = ['SHIP TO', 'SHIPTO', 'CONSIGNEE', 'DELIVER TO', 'DELIVERTO']
                    for marker in dest_markers:
                        # Case-insensitive search for marker
                        marker_pos = val_for_matching.upper().find(marker)
                        if marker_pos > 0:  # Found marker (not at start)
                            # Take only text BEFORE the destination marker
                            val_for_matching = val_for_matching[:marker_pos].strip()
                            break
                    
                    # NEW: Extract just the NAME portion from the KV value
                    # KV values often contain full address: "COMPANY NAME 123 STREET CITY, STATE ZIP"
                    # We need to extract just "COMPANY NAME" for name matching
                    name_only = _extract_name_from_address_string(val_for_matching)
                    # Also extract address (street) and city separately so address_city_patterns
                    # can be evaluated correctly — city alone must never trigger a match.
                    addr_only, city_only = _extract_address_and_city_from_address_string(val_for_matching)
                    
                    logger.info(f"KV '{key}': extracted name '{name_only}', address '{addr_only}', city '{city_only}' from full value '{val_for_matching[:80]}...'")
                    
                    # Use field-separated matching: name/address/city extracted from the same KV value
                    canon, var = _canonical_match_with_fields(name=name_only, address=addr_only, city=city_only, state="", full_text=val_for_matching)
                    if canon:
                        s_canon = canon
                        s_via   = f"{label} KV '{key}' (variation '{var}')"
            
            # Check if this is a destination section - EXACT match only (not substring)
            elif key_lower in dest_kv_keys:
                # Store this KV pair for later address field extraction
                d_kvs[key] = val
                if not d_canon:
                    # Similar issue: sometimes Textract combines sections
                    # For destination keys, if there are source markers, extract only the destination portion
                    val_for_matching = val or ''
                    
                    # Split on source markers to get only the destination portion
                    source_markers = ['SHIPPER', 'SHIP FROM', 'SHIPFROM', 'ORIGIN', 'PICK UP', 'PICKUP']
                    for marker in source_markers:
                        # Case-insensitive search for marker
                        marker_pos = val_for_matching.upper().find(marker)
                        if marker_pos > 0:  # Found marker (not at start)
                            # Take only text BEFORE the source marker
                            val_for_matching = val_for_matching[:marker_pos].strip()
                            break
                    
                    # NEW: Extract just the NAME portion from the KV value
                    name_only = _extract_name_from_address_string(val_for_matching)
                    # Also extract address (street) and city separately so address_city_patterns
                    # can be evaluated correctly — city alone must never trigger a match.
                    addr_only, city_only = _extract_address_and_city_from_address_string(val_for_matching)
                    
                    logger.info(f"KV '{key}': extracted name '{name_only}', address '{addr_only}', city '{city_only}' from full value '{val_for_matching[:80]}...'")
                    
                    # Use field-separated matching: name/address/city extracted from the same KV value
                    canon, var = _canonical_match_with_fields(name=name_only, address=addr_only, city=city_only, state="", full_text=val_for_matching)
                    if canon:
                        d_canon = canon
                        d_via   = f"{label} KV '{key}' (variation '{var}')"
        
        return s_canon, s_via, s_kvs, d_canon, d_via, d_kvs

    # ── Pass 1: freight invoice KV pairs ────────────────────────────────────────
    sc, sv, s_kvs, dc, dv, d_kvs = _scan_kvs(freight_kvs, "invoice")
    if sc:
        source_canonical, source_matched_via = sc, sv
        source_section_kvs = s_kvs
    if dc:
        dest_canonical, dest_matched_via = dc, dv
        dest_section_kvs = d_kvs

    # ── Pass 2: BOL textract KV pairs (per-page, same key-based matching) ───────
    # Each BOL page's KV pairs are scanned identically to Pass 1 — only KV keys
    # that match the known source/destination section headers are considered.
    # This naturally restricts matching to the SHIPPER / CONSIGNEE address fields
    # and prevents any free-form text (cargo descriptions, driver notes, etc.)
    # from being matched.
    if not source_canonical or not dest_canonical:
        for pg_key in sorted((bol_kvs_by_page or {}).keys(),
                              key=lambda x: int(x) if str(x).isdigit() else 0):
            pg_kvs = bol_kvs_by_page[pg_key]
            if not isinstance(pg_kvs, dict):
                continue
            sc, sv, s_kvs, dc, dv, d_kvs = _scan_kvs(pg_kvs, f"BOL pg {pg_key}")
            if not source_canonical and sc:
                source_canonical, source_matched_via = sc, sv
                source_section_kvs = s_kvs
            if not dest_canonical and dc:
                dest_canonical, dest_matched_via = dc, dv
                dest_section_kvs = d_kvs
            if source_canonical and dest_canonical:
                break

    # ── Log what was resolved ───────────────────────────────────────────────────
    logger.info("─── Canonical Location Mapping Result ───")
    logger.info(f"  source_name  : {'→ ' + repr(source_canonical) + ' via ' + source_matched_via if source_canonical else '(not found — LLM value kept)'}")
    logger.info(f"  destination_name: {'→ ' + repr(dest_canonical) + ' via ' + dest_matched_via if dest_canonical else '(not found — LLM value kept)'}")

    # ── Apply overrides to each ROAD-mode shipment ─────────────────────────────────
    # Note: delivery_type check removed because it's set later in flattening.
    # Canonical mapping now applies to all ROAD mode shipments.
    if source_canonical or dest_canonical:
        for shipment in extracted_info.get("shipments", []):
            if not isinstance(shipment, dict):
                continue
            
            # Check mode is ROAD
            mode_raw = shipment.get("mode", "")
            if isinstance(mode_raw, dict):
                mode_raw = mode_raw.get("value", "")
            if str(mode_raw).upper() != "ROAD":
                logger.info(f"  Skipping canonical mapping for mode='{mode_raw}' (not ROAD)")
                continue

            # Apply source canonical value and sync address fields
            if source_canonical:
                existing = shipment.get("source_name", "")
                if isinstance(existing, dict):
                    existing = existing.get("value", "")
                shipment["source_name"] = {
                    "value": source_canonical,
                    "explanation": f"Canonical mapping via {source_matched_via}",
                    "confidence": 1.0
                }
                logger.info(f"  Applied source_name '{existing}' → '{source_canonical}'")
                
                # Sync source address fields from the same section
                _sync_address_fields(shipment, source_section_kvs, "source")

            # Apply destination canonical value and sync address fields
            if dest_canonical:
                existing = shipment.get("destination_name", "")
                if isinstance(existing, dict):
                    existing = existing.get("value", "")
                shipment["destination_name"] = {
                    "value": dest_canonical,
                    "explanation": f"Canonical mapping via {dest_matched_via}",
                    "confidence": 1.0
                }
                logger.info(f"  Applied destination_name '{existing}' → '{dest_canonical}'")
                
                # Sync destination address fields from the same section
                _sync_address_fields(shipment, dest_section_kvs, "destination")
    
    logger.info("─────────────────────────────────────────")
    return extracted_info


def _sync_address_fields(shipment: dict, section_kvs: dict, field_prefix: str):
    """
    Sync address, city, state, zip fields from the same KV section where canonical match was found.
    
    Args:
        shipment: Shipment dict to update
        section_kvs: KV pairs from the matched section
        field_prefix: "source" or "destination"
    """
    if not section_kvs:
        return
    
    # Extract address components from section KVs
    address_fields = {
        f"{field_prefix}_address": ["address", "street", "addr", "location"],
        f"{field_prefix}_city": ["city"],
        f"{field_prefix}_state": ["state", "st"],
        f"{field_prefix}_zip": ["zip", "postal", "zipcode", "zip code", "postal code"],
    }
    
    for target_field, search_keys in address_fields.items():
        for kv_key, kv_val in section_kvs.items():
            kv_key_lower = kv_key.lower()
            # Check if this KV key matches any of our search keywords
            if any(sk in kv_key_lower for sk in search_keys):
                if kv_val and str(kv_val).strip():
                    # Update the shipment field if we found a value
                    existing = shipment.get(target_field, "")
                    if isinstance(existing, dict):
                        existing = existing.get("value", "")
                    
                    shipment[target_field] = {
                        "value": str(kv_val).strip(),
                        "explanation": f"Synced from canonical matched section (KV: '{kv_key}')",
                        "confidence": 1.0
                    }
                    logger.info(f"    Synced {target_field}: '{existing}' → '{kv_val}'")
                    break  # Found a match, move to next field

# ---------- GLOBAL PROMPT CACHE ----------
_PROMPT_TEMPLATE_CACHE = {}
_PROMPT_TEMPLATE_CACHE_TIMESTAMP = {}
_PROMPT_TEMPLATE_CACHE_TTL = 300  # in seconds, e.g., 5 minutes
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [1, 2, 4]

# Initialize AWS clients
s3 = boto3.client('s3')
textract = boto3.client('textract', region_name=REGION)
custom_config = Config(
    connect_timeout=30,
    read_timeout=400,
    retries={
        'max_attempts': 7,  # Increased from default 3 to 7 for better resilience
        'mode': 'adaptive'  # adaptive mode adjusts retry strategy based on error patterns
    }
)
bedrock = boto3.client('bedrock-runtime', region_name=REGION, config=custom_config)
dynamodb_client = boto3.client('dynamodb')

# ---------- HELPER FUNCTIONS ----------
def calculate_weighted_confidence(json_data):
    """
    Calculate the weighted confidence score from structured JSON data, 
    giving different priorities to different fields with highest priority to
    invoice number, invoice date, payment due date, bill of lading number, and charges.
    
    Args:
        json_data: The JSON data containing confidence scores
        
    Returns:
        float: Weighted confidence score
    """
    # Define field priorities (weights) - higher number means more important
    field_priorities = {
        # Highest priority fields as specified
        "invoice_number": 10.0,
        "invoice_date": 10.0,
        "payment_due_date": 10.0,
        "bill_of_lading_number": 10.0,
        
        # Charge fields (also high priority)
        "charge_code": 8.0,
        "charge_name": 8.0,
        "charge_gross_amount": 10.0,
        "currency": 5.0,
        
        # Secondary but still important fields
        "vendor_reference_id": 5.0,
        "total_invoice_value": 10.0,
        
        # Shipment fields
        "shipment_number": 10.0,
        "mode": 4.0,
        
        # Source and destination fields
        "source_name": 3.0,
        "source_city": 2.0,
        "source_country": 2.0,
        "source_state": 2.0,
        "source_zip_code": 2.0,
        "destination_name": 3.0,
        "destination_city": 2.0,
        "destination_country": 2.0,
        "destination_state": 2.0,
        "destination_zip_code": 2.0,
        
        # Measurement fields
        "shipment_weight": 3.0,
        "shipment_creation_date":3.0,
        "shipment_volume": 3.0,
        "shipment_weight_uom": 2.0,
        "shipment_volume_uom": 2.0,
        "shipment_total_value": 3.0,
        
        # Additional info fields
        "total_match": 4.0,
        "total_charges": 4.0,
        "key": 2.0,
        
        # Default weight for any other fields
        "default": 1.0
    }
    
    weighted_scores = []
    total_weight = 0
    
    def process_field(field_name, confidence_value):
        """Process a single confidence score with appropriate weighting"""
        weight = field_priorities.get(field_name, field_priorities["default"])
        weighted_scores.append(confidence_value * weight)
        return weight
    
    def extract_weighted_confidence(obj, path=""):
        """Recursively extract weighted confidence scores from nested objects."""
        nonlocal total_weight
        
        if isinstance(obj, dict):
            # Check if this is a field object with value and confidence
            if "value" in obj and "confidence" in obj:
                field_name = path.split(".")[-1]  # Get the field name from the path
                weight = process_field(field_name, obj["confidence"])
                total_weight += weight
            
            # Continue recursion for all dictionary items
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key
                extract_weighted_confidence(value, new_path)
                
        elif isinstance(obj, list):
            # Process each item in the list
            for i, item in enumerate(obj):
                new_path = f"{path}[{i}]"
                extract_weighted_confidence(item, new_path)
    
    # Start extraction
    extract_weighted_confidence(json_data)
    
    # Calculate weighted average if there are any scores
    if weighted_scores:
        return sum(weighted_scores) / total_weight if total_weight > 0 else 0.0
    else:
        return 0.0  # Return 0 if no confidence scores found

def prepare_extracted_fields_array(extracted_info):
    """
    Prepare an array of extracted fields with their values, confidence scores, and explanations.
    Properly handles nested structures including arrays.
    
    Args:
        extracted_info: The structured output from Claude
        
    Returns:
        list: Array of dictionaries with field_name, value, confidence, and explanation
    """
    extracted_fields = []
    
    def process_field(field_name, field_data, parent_path=""):
        current_path = f"{parent_path}.{field_name}" if parent_path else field_name
        
        # Handle dictionary case
        if isinstance(field_data, dict):
            # Check if this is a leaf node with value
            if "value" in field_data:
                # Store the value with its full path for context
                field_entry = {
                    "field_name": current_path,
                    "value": str(field_data.get("value", ""))  # Convert to string for consistency
                }
                
                # Add confidence if available
                if "confidence" in field_data:
                    field_entry["confidence"] = field_data.get("confidence", 0)
                
                # Add explanation if available
                if "explanation" in field_data:
                    field_entry["explanation"] = field_data.get("explanation", "")
                
                extracted_fields.append(field_entry)
            else:
                # Recursively process nested fields
                for key, value in field_data.items():
                    # Skip metadata fields
                    if key not in ["confidence", "explanation"]:
                        process_field(key, value, current_path)
        
        # Handle list case (like charges array or shipments array)
        elif isinstance(field_data, list):
            # Process each item in the list
            for i, item in enumerate(field_data):
                item_path = f"{current_path}[{i}]"
                
                # If item is a dictionary, process its fields
                if isinstance(item, dict):
                    for key, value in item.items():
                        # Skip metadata fields
                        if key not in ["confidence", "explanation"]:
                            process_field(key, value, item_path)
                
                # If item is a primitive value, add it directly
                elif not isinstance(item, (list, dict)):
                    extracted_fields.append({
                        "field_name": item_path,
                        "value": str(item)
                    })
                
                # If item is another list, recurse
                elif isinstance(item, list):
                    process_field(f"item{i}", item, current_path)
        
        # Handle primitive value case (shouldn't normally happen at top level)
        elif field_data is not None:
            extracted_fields.append({
                "field_name": current_path,
                "value": str(field_data)
            })
    
    # Start processing from the root
    for field_name, field_data in extracted_info.items():
        if field_name != "additional_info":
            process_field(field_name, field_data)
        else:
            # Handle additional_info specially
            # Process total_charges and total_match from additional_info
            if isinstance(field_data, list):
                for i, item in enumerate(field_data):
                    if isinstance(item, dict):
                        # Extract total_charges if available
                        if "total_charges" in item:
                            total_charges = item["total_charges"]
                            if isinstance(total_charges, dict) and "value" in total_charges:
                                field_entry = {
                                    "field_name": "additional_info.total_charges",
                                    "value": str(total_charges.get("value", ""))
                                }
                                if "confidence" in total_charges:
                                    field_entry["confidence"] = total_charges.get("confidence", 0)
                                if "explanation" in total_charges:
                                    field_entry["explanation"] = total_charges.get("explanation", "")
                                extracted_fields.append(field_entry)
                        
                        # Extract total_match if available
                        if "total_match" in item:
                            total_match = item["total_match"]
                            if isinstance(total_match, dict) and "value" in total_match:
                                field_entry = {
                                    "field_name": "additional_info.total_match",
                                    "value": str(total_match.get("value", ""))
                                }
                                if "confidence" in total_match:
                                    field_entry["confidence"] = total_match.get("confidence", 0)
                                if "explanation" in total_match:
                                    field_entry["explanation"] = total_match.get("explanation", "")
                                extracted_fields.append(field_entry)
    
    logger.info(f"Prepared extracted fields array: {len(extracted_fields)} fields")
    return extracted_fields

def get_secret(secret_name: str) -> Dict[str, str]:
    """Retrieve a secret from AWS Secrets Manager."""
    try:
        client = boto3.client('secretsmanager', region_name=REGION)
        response = client.get_secret_value(SecretId=secret_name)
        return json.loads(response['SecretString'])
    except Exception as e:
        logger.error(f"Error retrieving secret {secret_name}: {str(e)}")
        raise

def validate_email_address(email: str) -> bool:
    """
    Validate email address format using regex pattern.
    
    Args:
        email: Email address to validate
        
    Returns:
        bool: True if email is valid, False otherwise
    """
    if not email or not isinstance(email, str) or not email.strip():
        return False
    
    import re
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(email_pattern, email.strip()))

def get_reply_recipients(sender_email, classification):
    """
    Determine the correct reply recipients based on sender email.
    Simplified version - returns original sender.
    
    Args:
        sender_email: The email address of the sender
        classification: The classification of the email (not used in simplified version)
    
    Returns:
        list: List of email addresses to reply to
    """
    # Validate and clean sender email
    if not sender_email or not sender_email.strip():
        logger.error(f"Empty or missing sender_email: '{sender_email}'")
        return []
    
    if not validate_email_address(sender_email):
        logger.error(f"Invalid sender_email format: {sender_email}")
        return []
    
    sender_email = sender_email.lower().strip()
    logger.info(f"Returning original sender as reply recipient: {sender_email}")
    return [sender_email]

def format_date_to_dd_mmm_yyyy(date_str: str) -> str:
    """
    Convert various date formats to DD-MMM-YYYY format.
    
    Args:
        date_str: Date string in various formats (MM-DD-YYYY, MM/DD/YYYY, YYYY-MM-DD, etc.)
                  Can also be an integer timestamp (Unix timestamp in seconds or milliseconds)
        
    Returns:
        str: Date in DD-MMM-YYYY format (e.g., "08-Sep-2025")
    """
    # Handle integer timestamps (Unix timestamp)
    if isinstance(date_str, (int, float)):
        try:
            # Check if it's milliseconds (13 digits) or seconds (10 digits)
            if date_str > 1e12:  # Likely milliseconds
                dt = datetime.fromtimestamp(date_str / 1000)
            else:  # Likely seconds
                dt = datetime.fromtimestamp(date_str)
            month_names = {
                1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
                7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"
            }
            return f"{dt.day:02d}-{month_names[dt.month]}-{dt.year}"
        except (ValueError, OSError) as e:
            logger.warning(f"Error converting timestamp {date_str} to date: {e}")
            return str(date_str)
    
    # Convert to string and validate
    if not date_str:
        return ""
    
    date_str = str(date_str).strip()
    
    if date_str == "" or date_str.lower() in ["null", "none", "n/a"]:
        return ""
    
    # Month name mapping
    month_names = {
        1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
        7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"
    }
    
    # Common date patterns to try
    date_patterns = [
        # MM-DD-YYYY or MM/DD/YYYY
        (r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$', lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))),
        # YYYY-MM-DD
        (r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$', lambda m: (int(m.group(2)), int(m.group(3)), int(m.group(1)))),
        # DD-MM-YYYY or DD/MM/YYYY
        (r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$', lambda m: (int(m.group(2)), int(m.group(1)), int(m.group(3)))),
        # MMM DD, YYYY (e.g., "Sep 08, 2025")
        (r'^([A-Za-z]{3})\s+(\d{1,2}),?\s+(\d{4})$', lambda m: (m.group(1), int(m.group(2)), int(m.group(3)))),
        # DD MMM YYYY (e.g., "08 Sep 2025")
        (r'^(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})$', lambda m: (m.group(2), int(m.group(1)), int(m.group(3)))),
    ]
    
    # Try to parse with different patterns
    for pattern, extractor in date_patterns:
        match = re.match(pattern, date_str)
        if match:
            try:
                if pattern.startswith(r'^([A-Za-z]{3})'):  # Month name patterns
                    month_name, day, year = extractor(match)
                    # Convert month name to number
                    month_num = None
                    for num, name in month_names.items():
                        if name.lower() == month_name.lower():
                            month_num = num
                            break
                    if month_num:
                        return f"{day:02d}-{month_names[month_num]}-{year}"
                    # If month_num is None, continue to next pattern
                else:  # Numeric patterns
                    month, day, year = extractor(match)
                    # Validate month and day ranges
                    if 1 <= month <= 12 and 1 <= day <= 31:
                        return f"{day:02d}-{month_names[month]}-{year}"
            except (ValueError, IndexError) as e:
                logger.warning(f"Error parsing date '{date_str}' with pattern '{pattern}': {e}")
                continue
    
    # If no pattern matches, try using datetime.strptime with common formats
    common_formats = [
        '%m-%d-%Y', '%m/%d/%Y', '%Y-%m-%d', '%Y/%m/%d',
        '%d-%m-%Y', '%d/%m/%Y', '%m-%d-%y', '%m/%d/%y',
        '%d-%m-%y', '%d/%m/%y', '%Y-%m-%d', '%Y/%m/%d',
        '%b %d, %Y', '%d %b %Y', '%B %d, %Y', '%d %B %Y'
    ]
    
    for fmt in common_formats:
        try:
            parsed_date = datetime.strptime(date_str, fmt)
            return f"{parsed_date.day:02d}-{month_names[parsed_date.month]}-{parsed_date.year}"
        except ValueError:
            continue
    
    # If all parsing attempts fail, log warning and return original string
    logger.warning(f"Could not parse date '{date_str}' to DD-MMM-YYYY format")
    return date_str

def format_dates_in_extracted_data(extracted_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format invoice_date and payment_due_date in extracted data to DD-MMM-YYYY format.
    
    Args:
        extracted_info: The structured output from Claude
        
    Returns:
        Dict: Updated extracted_info with formatted dates
    """
    try:
        # Format invoice_date
        if "invoice_date" in extracted_info:
            invoice_date_field = extracted_info["invoice_date"]
            if isinstance(invoice_date_field, dict) and "value" in invoice_date_field:
                original_date = invoice_date_field["value"]
                # Handle None, empty strings, and check if it's a valid date value
                if original_date is not None and (isinstance(original_date, (int, float)) or (isinstance(original_date, str) and original_date.strip())):
                    # Pass the original value (could be int, float, or str) to format function
                    formatted_date = format_date_to_dd_mmm_yyyy(original_date)
                    if formatted_date and formatted_date != str(original_date):
                        logger.info(f"Formatted invoice_date from '{original_date}' to '{formatted_date}'")
                    invoice_date_field["value"] = formatted_date
        
        # Format payment_due_date
        if "payment_due_date" in extracted_info:
            payment_due_date_field = extracted_info["payment_due_date"]
            if isinstance(payment_due_date_field, dict) and "value" in payment_due_date_field:
                original_date = payment_due_date_field["value"]
                # Handle None, empty strings, and check if it's a valid date value
                if original_date is not None and (isinstance(original_date, (int, float)) or (isinstance(original_date, str) and original_date.strip())):
                    # Pass the original value (could be int, float, or str) to format function
                    formatted_date = format_date_to_dd_mmm_yyyy(original_date)
                    if formatted_date and formatted_date != str(original_date):
                        logger.info(f"Formatted payment_due_date from '{original_date}' to '{formatted_date}'")
                    payment_due_date_field["value"] = formatted_date
        
        # Also format dates in shipments if they exist
        if "shipments" in extracted_info and isinstance(extracted_info["shipments"], list):
            for shipment in extracted_info["shipments"]:
                if isinstance(shipment, dict) and "shipment_creation_date" in shipment:
                    creation_date_field = shipment["shipment_creation_date"]
                    if isinstance(creation_date_field, dict) and "value" in creation_date_field:
                        original_date = creation_date_field["value"]
                        # Handle None, empty strings, and check if it's a valid date value
                        if original_date is not None and (isinstance(original_date, (int, float)) or (isinstance(original_date, str) and original_date.strip())):
                            # Pass the original value (could be int, float, or str) to format function
                            formatted_date = format_date_to_dd_mmm_yyyy(original_date)
                            if formatted_date and formatted_date != str(original_date):
                                logger.info(f"Formatted shipment_creation_date from '{original_date}' to '{formatted_date}'")
                            creation_date_field["value"] = formatted_date
        
        return extracted_info
        
    except Exception as e:
        logger.error(f"Error formatting dates in extracted data: {str(e)}")
        return extracted_info

# Get SMTP credentials from Secrets Manager
try:
    SMTP_CONFIG = get_secret(SMTP_SECRET_NAME)
    SMTP_USERNAME = SMTP_CONFIG["SMTP_USERNAME"]
    SMTP_PASSWORD = SMTP_CONFIG["SMTP_PASSWORD"]
    logger.info(f"Successfully retrieved SMTP credentials from Secrets Manager using secret: {SMTP_SECRET_NAME}")
except Exception as e:
    logger.error(f"Failed to retrieve SMTP credentials from secret {SMTP_SECRET_NAME}: {str(e)}")
    SMTP_USERNAME = ""
    SMTP_PASSWORD = ""

# Get app credentials from Secrets Manager
try:
    if APP_SECRET_NAME:
        _app_secret = get_secret(APP_SECRET_NAME)
        API_ENDPOINT = _app_secret.get('API_ENDPOINT', API_ENDPOINT)
        INTERNAL_TOKEN = _app_secret.get('INTERNAL_TOKEN', INTERNAL_TOKEN)
        CLIENT_ID = _app_secret.get('CLIENT_ID', CLIENT_ID)
        TRANSACTION_STATUS_PAYLOAD_URL = _app_secret.get('TRANSACTION_STATUS_PAYLOAD', TRANSACTION_STATUS_PAYLOAD_URL)
        REJECTION_ENDPOINT = _app_secret.get('REJECTION_ENDPOINT', REJECTION_ENDPOINT)
        logger.info(f"Successfully retrieved app credentials from Secrets Manager using secret: {APP_SECRET_NAME}")
    else:
        logger.warning("APP_SECRET_NAME not set — using environment variable values for app credentials")
except Exception as e:
    logger.error(f"Failed to retrieve app credentials from secret {APP_SECRET_NAME}: {str(e)}")

# ---------- PROMPT TEMPLATE HANDLING ----------
def load_prompt_template_from_s3(bucket_name, key, force_refresh=False):
    """Load a prompt template from an S3 bucket with time-based cache invalidation."""
    cache_key = f"{bucket_name}/{key}"
    current_time = datetime.now().timestamp()
    
    # Check if we need to refresh the cache
    cache_expired = False
    if cache_key in _PROMPT_TEMPLATE_CACHE_TIMESTAMP:
        last_update_time = _PROMPT_TEMPLATE_CACHE_TIMESTAMP.get(cache_key, 0)
        cache_expired = (current_time - last_update_time) > _PROMPT_TEMPLATE_CACHE_TTL
    
    # Use cache only if it exists, isn't expired, and we're not forcing a refresh
    if not force_refresh and cache_key in _PROMPT_TEMPLATE_CACHE and not cache_expired:
        logger.info("Using cached prompt templates")
        return _PROMPT_TEMPLATE_CACHE[cache_key]
    # -------- ACTUAL LOAD from S3 if not cached --------
    try:
        obj = s3.get_object(Bucket=bucket_name, Key=key)
        content = obj['Body'].read().decode('utf-8')
        local_vars = {}
        exec(content, {}, local_vars)
        templates = local_vars.get("PROMPT_TEMPLATES", {})
        
        # Save to cache
        _PROMPT_TEMPLATE_CACHE[cache_key] = templates
        _PROMPT_TEMPLATE_CACHE_TIMESTAMP[cache_key] = current_time
        logger.info("Loaded and cached prompt templates from S3")
        return templates
    except Exception as e:
        logger.error(f"Error loading prompt template from S3: {e}")
        return {}

def get_prompt_template(carrier_name: str) -> Optional[str]:
    """Get the prompt template for a specific carrier from the prompt templates."""
    try:
        # Get the carrier's prompt template
        carrier_data = load_prompt_template_from_s3(S3_BUCKET, S3_PREFIX).get(carrier_name)
        if not carrier_data:
            logger.error(f"No prompt template found for carrier: {carrier_name}")
            return None
            
        prompt_template = carrier_data.get('prompt_template')
        if not prompt_template:
            logger.error(f"Missing prompt template for {carrier_name}")
            return None
            
        logger.info(f"Successfully loaded prompt template for {carrier_name}")
        return prompt_template
        
    except Exception as e:
        logger.error(f"Error loading prompt template for {carrier_name}: {str(e)}")
        return None

# ---------- CARRIER CLASSIFICATION ----------
class CarrierClassifier:
    def __init__(self, force_refresh=False):
        """Initialize the carrier classifier with Claude model."""
        self.carrier_templates = load_prompt_template_from_s3(S3_BUCKET, S3_PREFIX)
        if not self.carrier_templates:
            raise ValueError("Prompt template loading failed: no templates found.")
        self.supported_carriers = list(self.carrier_templates.keys())
        self._supported_upper = {sc.upper(): sc for sc in self.supported_carriers}
        logger.info(f"Initialized carrier classifier with {len(self.supported_carriers)} supported carriers")

    def _resolve_classified_carrier_name(self, carrier_raw: str) -> Optional[str]:
        """Map model output to a PROMPT_TEMPLATES key (handles _LLC suffix drift, spacing)."""
        carrier = carrier_raw.strip().strip('\'"')
        if not carrier:
            return None
        u = carrier.upper()
        if u in self._supported_upper:
            return self._supported_upper[u]
        if f"{u}_LLC" in self._supported_upper:
            return self._supported_upper[f"{u}_LLC"]
        u_underscore = "_".join(u.split())
        if u_underscore in self._supported_upper:
            return self._supported_upper[u_underscore]
        if f"{u_underscore}_LLC" in self._supported_upper:
            return self._supported_upper[f"{u_underscore}_LLC"]
        u_spaced = u.replace("_", " ")
        if u_spaced in self._supported_upper:
            return self._supported_upper[u_spaced]
        if f"{u_spaced.replace(' ', '_')}_LLC" in self._supported_upper:
            return self._supported_upper[f"{u_spaced.replace(' ', '_')}_LLC"]
        # SCAC / short codes sometimes returned instead of template keys
        scac_aliases = {
            "CLIM": "CIRCLE_LOGISTICS_INC",
            "PFBH": "PRIVATE_FLEET_BACKHAUL_LLC",
            "PRSP": "PRECISION STRIP TRANSPORT",
            "MSGR": "M_S_LOGISTICS_LLC",
            "GPAB": "GP_TRANSCO",
        }
        if u in scac_aliases:
            canon = scac_aliases[u].upper()
            if canon in self._supported_upper:
                return self._supported_upper[canon]
        return None

    def _infer_carrier_from_classification_text(self, text: str) -> Optional[str]:
        """Regex-based fallback when the LLM returns GENERIC or an unknown label.

        Textract often splits carrier names across lines (e.g. 'M-S' and 'LOGISTICS'
        on separate rows).  After collapsing whitespace we can reliably detect patterns
        that Claude misses when there are many line breaks between tokens.
        """
        if not text or not self._supported_upper:
            return None
        collapsed = re.sub(r"\s+", " ", text, flags=re.UNICODE)
        cu = collapsed.upper()
        # M-S LOGISTICS LLC — matches 'M-S LOGISTICS', 'MS LOGISTICS', 'MSGR'
        if "M_S_LOGISTICS_LLC" in self._supported_upper:
            if "MSGR" in cu or re.search(r"\bM[- ]?S\s+LOGISTICS\b", cu):
                return self._supported_upper["M_S_LOGISTICS_LLC"]
        return None

    def classify_document(self, text: str) -> Optional[str]:
        """Classify a document using Claude (converse API) to determine the carrier."""
        try:
            logger.info(f"Starting document classification with Claude")
            classification_sample = text[:4000]

            # Build carrier list from template: show both the KEY (what Claude must return)
            # and the human-readable carrier_name so Claude can match against invoice text.
            carrier_lines = []
            for key in self.supported_carriers:
                template_data = self.carrier_templates.get(key, {})
                display_name = template_data.get("carrier_name", key)
                if display_name and display_name != key:
                    carrier_lines.append(f"  - KEY: {key}  |  NAME: {display_name}")
                else:
                    carrier_lines.append(f"  - KEY: {key}")
            carrier_list = "\n".join(carrier_lines)

            system_prompt = (
                "You are a freight-invoice carrier identification specialist. "
                "Your sole job is to read Textract-extracted invoice text, identify the carrier, "
                "and return a JSON object with the carrier KEY and your reasoning. "
                "Do NOT include any text outside the JSON object."
            )

            user_message = f"""Identify the freight carrier for the invoice text below.

APPROVED CARRIERS — each entry shows the KEY you must return and the carrier's display NAME
that may appear on the invoice:
{carrier_list}

INSTRUCTIONS:
1. Search the invoice text for any carrier company name, trade name, or abbreviation.
2. Look in: letterhead, "PLEASE REMIT TO", "BILL FROM", company header, footer, or watermark.
3. Find the entry whose NAME (or a recognisable abbreviation/variant of it) appears in the text.
4. Return the corresponding KEY for that entry.
5. If no carrier can be confidently identified, use GENERIC as the key.

INVOICE TEXT:
{classification_sample}

Respond with ONLY this JSON (no markdown, no extra text):
{{"carrier_key": "<KEY or GENERIC>", "reason": "<one sentence: what text you matched and where you found it>"}}"""

            model_id = HAIKU_BEDROCK_MODEL_ID
            response = bedrock.converse(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_message}]}],
                inferenceConfig={
                    "maxTokens": 200,
                    "temperature": 0.0,
                }
            )

            raw_response = response["output"]["message"]["content"][0]["text"].strip()
            usage = response.get("usage", {})
            stop_reason = response.get("stopReason", "unknown")

            logger.info(
                f"Carrier classifier LLM raw response: {raw_response} | "
                f"stop_reason={stop_reason} | "
                f"input_tokens={usage.get('inputTokens', 'N/A')} | "
                f"output_tokens={usage.get('outputTokens', 'N/A')}"
            )

            # Parse JSON response to extract carrier key and reasoning
            carrier = "GENERIC"
            try:
                clean = raw_response.lstrip("```json").lstrip("```").rstrip("```").strip()
                parsed = json.loads(clean)
                carrier = str(parsed.get("carrier_key", "GENERIC")).strip()
                reason = str(parsed.get("reason", "")).strip()
                logger.info(f"Carrier classifier decision: key='{carrier}' | reason='{reason}'")
            except (json.JSONDecodeError, KeyError, TypeError):
                # Fallback: treat raw text as the carrier key
                carrier = raw_response.strip().strip('"\'')
                logger.warning(
                    f"Carrier classifier: could not parse JSON, treating raw text as key: '{carrier}'"
                )

            matched_carrier = self._resolve_classified_carrier_name(carrier)
            if matched_carrier:
                logger.info(
                    f"Carrier classification result: LLM returned '{carrier}' → "
                    f"resolved to template key '{matched_carrier}'"
                )
            else:
                logger.warning(
                    f"Carrier classification result: LLM returned '{carrier}' → "
                    f"no direct template key match, trying regex inference..."
                )
                inferred = self._infer_carrier_from_classification_text(classification_sample)
                if inferred:
                    logger.info(
                        f"Carrier classification result: LLM returned '{carrier}' → "
                        f"regex inference resolved to '{inferred}'"
                    )
                    matched_carrier = inferred
                else:
                    logger.warning(
                        f"Carrier classification result: LLM returned '{carrier}' → "
                        f"regex inference also found no match → carrier=UNCLASSIFIED"
                    )

            if matched_carrier:
                logger.info(f"Document classified as {matched_carrier}")
                return matched_carrier
            else:
                logger.warning(f"Claude returned unknown carrier: {carrier}")
                return None

        except Exception as e:
            logger.error(f"Error classifying document with Claude: {str(e)}")
            raise

# ---------- STRUCTURED OUTPUT HANDLING ----------
def clean_structured_output(data, schema, path="root"):
    if schema.get("type") == "object":
        cleaned = {}
        for key, subschema in schema.get("properties", {}).items():
            value = data.get(key) if isinstance(data, dict) else None
            if isinstance(value, dict) and 'value' in subschema.get("properties", {}) and 'explanation' in subschema.get("properties", {}):
                cleaned[key] = {
                    "value": "" if value.get("value") == "<UNKNOWN>" else value.get("value", ""),
                    "explanation": "" if value.get("explanation") == "<UNKNOWN>" else value.get("explanation", "")
                }
            elif value is None:
                cleaned[key] = value
            else:
                cleaned[key] = clean_structured_output(value, subschema, path=f"{path}.{key}")
        return cleaned

    elif schema.get("type") == "array":
        item_schema = schema.get("items", {})
        return [
            clean_structured_output(item, item_schema, path=f"{path}[{i}]")
            for i, item in enumerate(data or [])
        ]

    else:
        return "" if data == "<UNKNOWN>" else data

def flatten_structured_output(data):
    """
    Flatten a nested structured output into a simple key-value dictionary.
    Only keeps field names and their values, removing confidence, explanation, and other metadata.
    """
    if not isinstance(data, dict):
        return data
        
    result = {}
    for key, value in data.items():
        # Skip additional_info field
        if key == "additional_info":
            continue
            
        # Handle custom_fields object - flatten nested structured format
        if key == "custom_fields" and isinstance(value, dict):
            flattened_custom_fields = {}
            for custom_field_key, custom_field_value in value.items():
                if isinstance(custom_field_value, dict) and "value" in custom_field_value:
                    flattened_custom_fields[custom_field_key] = custom_field_value["value"]
                else:
                    flattened_custom_fields[custom_field_key] = custom_field_value
            result[key] = flattened_custom_fields
            continue
            
        # Handle dict with "value" and "explanation" keys
        if isinstance(value, dict) and "value" in value:
            result[key] = value["value"]
            continue
            
        # Special handling for shipments array
        if key == "shipments" and isinstance(value, list):
            # If we have a list of shipments, process each one
            result[key] = []
            for shipment in value:
                if isinstance(shipment, dict):
                    flattened_shipment = {}
                    
                    # Process each field in the shipment
                    for shipment_key, shipment_value in shipment.items():
                        # Handle charges array specially
                        if shipment_key == "charges" and isinstance(shipment_value, list):
                            flattened_shipment["charges"] = []
                            for charge in shipment_value:
                                if isinstance(charge, dict):
                                    flattened_charge = {}
                                    for charge_key, charge_value in charge.items():
                                        # Skip confidence and explanation metadata
                                                if charge_key not in ['confidence', 'explanation']:
                                                    if isinstance(charge_value, dict) and "value" in charge_value:
                                                        flattened_charge[charge_key] = charge_value["value"]
                                                    else:
                                                        # Preserve plain values (e.g., charge_name, charge_code) that aren't in structured dicts
                                                        flattened_charge[charge_key] = charge_value
                                                else:
                                                    flattened_charge[charge_key] = charge_value
                                    flattened_shipment["charges"].append(flattened_charge)
                        # Handle container array specially
                        elif shipment_key == "container" and isinstance(shipment_value, list):
                            flattened_shipment["container"] = []
                            for container in shipment_value:
                                if isinstance(container, dict):
                                    flattened_container = {}
                                    for container_key, container_value in container.items():
                                        if isinstance(container_value, dict) and "value" in container_value:
                                            flattened_container[container_key] = container_value["value"]
                                        else:
                                            flattened_container[container_key] = container_value
                                    flattened_shipment["container"].append(flattened_container)
                        # Handle custom object in shipment - flatten nested structured format
                        elif shipment_key == "custom" and isinstance(shipment_value, dict):
                            flattened_custom = {}
                            for custom_key, custom_value in shipment_value.items():
                                if isinstance(custom_value, dict) and "value" in custom_value:
                                    flattened_custom[custom_key] = custom_value["value"]
                                else:
                                    flattened_custom[custom_key] = custom_value
                            flattened_shipment[shipment_key] = flattened_custom
                        # Handle normal shipment fields
                        elif isinstance(shipment_value, dict) and "value" in shipment_value:
                            flattened_shipment[shipment_key] = shipment_value["value"]
                        else:
                            flattened_shipment[shipment_key] = shipment_value
                            
                    result[key].append(flattened_shipment)
            continue
            
        # Special handling for charges when it's a string
        if key == "charges" and isinstance(value, str):
            try:
                # Try to parse the string as JSON
                # First, clean up any commas after the closing brackets that could cause issues
                clean_json_str = value.strip()
                
                # Try to find the actual JSON array part
                try:
                    # Find the first '[' and the last ']' to extract just the array part
                    start_idx = clean_json_str.find('[')
                    end_idx = clean_json_str.rfind(']')
                    
                    if start_idx >= 0 and end_idx > start_idx:
                        clean_json_str = clean_json_str[start_idx:end_idx+1]
                    
                    # Remove trailing commas before closing brackets
                    clean_json_str = re.sub(r',\s*]', ']', clean_json_str)
                    
                    # Remove any trailing commas
                    if clean_json_str.endswith(','):
                        clean_json_str = clean_json_str[:-1]
                    
                    charges_list = json.loads(clean_json_str)
                    result[key] = []
                    for charge_item in charges_list:
                        if isinstance(charge_item, dict):
                            flattened_charge = {}
                            for charge_key, charge_value in charge_item.items():
                                # Only include core charge fields, skip confidence and explanation
                                if charge_key not in ['confidence', 'explanation']:
                                    if isinstance(charge_value, dict) and "value" in charge_value:
                                        flattened_charge[charge_key] = charge_value["value"]
                                    else:
                                        flattened_charge[charge_key] = charge_value
                            result[key].append(flattened_charge)
                    logger.info(f"Successfully parsed charges from JSON string: {len(result[key])} charges found")
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse charges string as JSON: {e}")
                    # Try a more aggressive approach - extract each object separately
                    try:
                        # Find all JSON objects in the string
                        object_pattern = r'\{[^{}]*\}'
                        objects = re.findall(object_pattern, clean_json_str)
                        
                        if objects:
                            charges_array = []
                            for obj_str in objects:
                                try:
                                    obj = json.loads(obj_str)
                                    charges_array.append(obj)
                                except json.JSONDecodeError:
                                    pass  # Skip invalid objects
                            
                            if charges_array:
                                result[key] = charges_array
                                logger.info(f"Extracted {len(charges_array)} charges using regex approach")
                            else:
                                result[key] = []
                                logger.warning("Could not extract any valid charges, setting to empty list")
                        else:
                            result[key] = []
                            logger.warning("No JSON objects found in charges string, setting to empty list")
                    except Exception as regex_err:
                        logger.error(f"Regex extraction failed: {regex_err}")
                        result[key] = []
                        logger.warning("Setting charges to empty array due to parsing error")
            except Exception as e:
                logger.error(f"Failed to process charges: {e}")
                # In case of parsing error, set charges to an empty array instead of keeping the string
                result[key] = []
                logger.warning("Setting charges to empty array due to parsing error")
            continue
                
        # Normal handling for charges as a list
        if key == "charges" and isinstance(value, list):
            result[key] = []
            for charge_item in value:
                if isinstance(charge_item, dict):
                    flattened_charge = {}
                    for charge_key, charge_value in charge_item.items():
                        # Only include core charge fields, skip confidence and explanation
                        if charge_key not in ['confidence', 'explanation']:
                            if isinstance(charge_value, dict) and "value" in charge_value:
                                flattened_charge[charge_key] = charge_value["value"]
                            else:
                                flattened_charge[charge_key] = charge_value
                    result[key].append(flattened_charge)
            continue
            
        # Handle other lists
        if isinstance(value, list):
            result[key] = [flatten_structured_output(item) for item in value]
            continue
            
        # Handle nested dictionaries
        if isinstance(value, dict):
            result[key] = flatten_structured_output(value)
            continue
            
        # Simple values pass through
        result[key] = value
            
    return result

def log_validation_failures(structured_output, validator_fails, logger):
    reason_map = {
        "val_true_conf_false": (
            "Reason: Value is present but model confidence is less than 0.90 (i.e., you got an answer, but the model is not confident about it)"
        ),
        "val_false_conf_true": (
            "Reason: Value is missing or blank, but model is confident (>= 0.90) (i.e., model is very confident that the field should be empty or is not found)"
        ),
        "val_false_conf_false": (
            "Reason: Value is missing or blank, and model confidence is less than 0.90 (i.e., model is not confident and could not extract a value)"
        ),
    }
    finding = ''
    for category, items in validator_fails.items():
        if not items:
            continue
        
        for key in items:
            if key.startswith("charges["):
                import re
                m = re.match(r"charges\\\\$\\\\$(\d+)\\\\$\\\\$\.(.+)", key)
                if m:
                    idx = int(m.group(1))
                    subfield = m.group(2)
                    field = structured_output.get("charges", [{}])[idx].get(subfield, {})
                else:
                    field = {}
            else:
                field = structured_output.get(key, {})

            value = field.get("value")
            confidence = field.get("confidence")
            explanation = field.get("explanation")
            logger.info(
                f"\n {reason_map[category]} \n {key} \n"
                f" value       : {repr(value)} \n"
                f" confidence  : {repr(confidence)} \n"
                f" explanation : {explanation}"
            )
            finding += (
                f"\n {reason_map[category]} \n {key} \n"
                f" value       : {repr(value)} \n"
                f" confidence  : {repr(confidence)} \n"
                f" explanation : {explanation}"
            )

    return finding

# ---------- TEXTRACT PDF ----------
def extract_text_from_pdf(bucket: str, key: str):
    """
    Extracts forms, tables, and text lines from a PDF in S3 using Textract.
    Returns: (forms_dict, tables_list, text_lines_list, page_count)
    """
    def start_document_analysis(bucket, key, features=['FORMS', 'TABLES']):
        response = textract.start_document_analysis(
            DocumentLocation={'S3Object': {'Bucket': bucket, 'Name': key}},
            FeatureTypes=features
        )
        return response['JobId']
   
    def wait_for_job(job_id, max_attempts=42, delay=10):
        for attempt in range(max_attempts):
            response = textract.get_document_analysis(JobId=job_id)
            status = response['JobStatus']
            logger.info(f"Attempt {attempt + 1}: Job {job_id} status = {status}")
            if status == 'SUCCEEDED':
                return True
            elif status == 'FAILED':
                raise Exception(f"Textract job failed: {response.get('StatusMessage')}")
            time.sleep(delay)
        raise TimeoutError("Textract job timed out")

    def get_all_blocks(job_id):
        blocks = []
        next_token = None
        while True:
            kwargs = {'JobId': job_id}
            if next_token:
                kwargs['NextToken'] = next_token
            response = textract.get_document_analysis(**kwargs)
            blocks.extend(response['Blocks'])
            next_token = response.get('NextToken')
            if not next_token:
                break
        return blocks

    def get_text_for_block(block, block_map):
        text = ''
        for rel in block.get('Relationships', []):
            if rel['Type'] == 'CHILD':
                for cid in rel['Ids']:
                    word = block_map.get(cid)
                    if word and word['BlockType'] == 'WORD':
                        text += word['Text'] + ' '
                    elif word and word['BlockType'] == 'SELECTION_ELEMENT' and word.get('SelectionStatus') == 'SELECTED':
                        text += '☑ '
        return text.strip()

    def extract_kv_pairs(blocks, block_map):
        key_map = {}
        value_map = {}
        for block in blocks:
            if block['BlockType'] == 'KEY_VALUE_SET':
                if 'KEY' in block.get('EntityTypes', []):
                    key_map[block['Id']] = block
                else:
                    value_map[block['Id']] = block
        kvs = {}
        for key_id, key_block in key_map.items():
            key_text = get_text_for_block(key_block, block_map)
            value_block = None
            for rel in key_block.get('Relationships', []):
                if rel['Type'] == 'VALUE':
                    for value_id in rel['Ids']:
                        value_block = value_map.get(value_id)
            value_text = get_text_for_block(value_block, block_map) if value_block else ''
            if key_text:
                kvs[key_text] = value_text
        return kvs

    def extract_tables(blocks, block_map):
        tables = []
        for block in blocks:
            if block['BlockType'] == 'TABLE':
                table = {}
                # Get IDs of child CELL blocks
                table_cells = []
                if 'Relationships' in block:
                    for rel in block['Relationships']:
                        if rel['Type'] == 'CHILD':
                            table_cells.extend(rel['Ids'])

                for cell_id in table_cells:
                    cell = block_map[cell_id]
                    if cell['BlockType'] != 'CELL':
                        continue
                    row = cell['RowIndex']
                    col = cell['ColumnIndex']
                    text = get_text_for_block(cell, block_map)
                    table.setdefault(row, {})[col] = text

                if not table:
                    continue
                    
                max_row = max(table.keys())
                max_col = max(max(row.keys()) for row in table.values())
                grid = [['' for _ in range(max_col)] for _ in range(max_row)]
                for r in table:
                    for c in table[r]:
                        grid[r - 1][c - 1] = table[r][c]

                tables.append(grid)
        return tables

    def extract_text_lines(blocks):
        return [block['Text'] for block in blocks if block['BlockType'] == 'LINE']

    try:
        logger.info(f"Starting Textract analysis for s3://{bucket}/{key}")
        
        # Start textract job
        analysis_job_id = start_document_analysis(bucket, key)
        wait_for_job(analysis_job_id)
        analysis_blocks = get_all_blocks(analysis_job_id)
        block_map = {b['Id']: b for b in analysis_blocks}

        # Extract content
        kvs = extract_kv_pairs(analysis_blocks, block_map)
        tables = extract_tables(analysis_blocks, block_map)
        lines = extract_text_lines(analysis_blocks)
        
        # Count pages
        page_count = sum(1 for block in analysis_blocks if block['BlockType'] == 'PAGE')
        
        logger.info(f"Extracted {len(lines)} lines, {len(kvs)} key-value pairs, {len(tables)} tables, {page_count} pages.")
        return kvs, tables, lines, page_count
    except Exception as e:
        logger.error(f"Error extracting text from PDF: {str(e)}")
        raise

# ---------- CLAUDE-BASED SHIPPER/CONSIGNEE EXTRACTION ----------
def extract_shipper_consignee_with_claude(image_bytes: bytes, carrier_name: str) -> Dict[str, Any]:
    """
    Extract SHIPPER and CONSIGNEE details from image using Claude LLM.
    
    Args:
        image_bytes: The cropped image bytes (PNG format) containing SHIPPER and CONSIGNEE information
        carrier_name: Name of the carrier (for logging)
    
    Returns:
        dict with source and destination details:
        - source_name, source_address, source_city, source_state
        - destination_name, destination_address, destination_city, destination_state
    """
    result = {}
    
    try:
        if carrier_name and "DAYTON" in carrier_name.upper():
            prompt = """Extract SHIPPER and CONSIGNEE information from the document image provided.

The image contains a Dayton Freight invoice section with SHIPPER (source, left box) and CONSIGNEE
(destination, right box) details, laid out roughly like:

  Shipper                              Consignee
  EXEMPLARY FOAM          0052643      TOP LINE MATERIAL HANDLING     001802A
  1235 W HIVELY AVE                    2600 HOLLOWAY RD
  ELKHART, IN 46517                    LOUISVILLE, KY 40299

For each of SHIPPER and CONSIGNEE, the FIRST LINE contains the company/location name, and it is
often followed on the SAME line by a separate numeric reference/account code (e.g. "0052643",
"001802A") — that number is NOT part of the name. Extract ONLY the company/location name text
from the first line; DO NOT include that trailing numeric/alphanumeric code in source_name or
destination_name, and do not use that number as the name if the name text itself is missing.

For SHIPPER (source):
- source_name: The company/location name text from the FIRST LINE of the Shipper box ONLY —
  exclude any numeric reference code that appears on that same line (e.g. if the line reads
  "EXEMPLARY FOAM 0052643", extract "EXEMPLARY FOAM", not "0052643" or "EXEMPLARY FOAM 0052643").
- source_address: Street address from the line below the name (e.g. "1235 W HIVELY AVE").
- source_city: City name from the last line of the Shipper box, before the comma.
- source_state: State abbreviation (2 letters) from the last line of the Shipper box.

For CONSIGNEE (destination):
- destination_name: The company/location name text from the FIRST LINE of the Consignee box ONLY —
  exclude any numeric reference code that appears on that same line (e.g. if the line reads
  "TOP LINE MATERIAL HANDLING 001802A", extract "TOP LINE MATERIAL HANDLING", not "001802A").
- destination_address: Street address from the line below the name.
- destination_city: City name from the last line of the Consignee box, before the comma.
- destination_state: State abbreviation (2 letters) from the last line of the Consignee box.

Extract the information and return it as a JSON object with the fields listed above.
If any information is not found, use null for that field.
Only return valid JSON, no additional text or explanation."""
        else:
            prompt = """Extract SHIPPER and CONSIGNEE information from the document image provided.

The image contains shipping information with SHIPPER (source) and CONSIGNEE (destination) details.
Please extract the following information:

For SHIPPER (source):
- source_name: Company or person name (usually the first line of the text and extract till '-') should only be picked from Shipper section
- source_address: Street address should only be picked from second line of the text should only be picked from Shipper section
- source_city: City name should only be picked from Shipper section
- source_state: State abbreviation (2 letters) should only be picked from Shipper section

For CONSIGNEE (destination):
- destination_name: Company or person name (usually the first line of the text and extract till '-') should only be picked from Consignee section
- destination_address: Street address should only be picked from second line of the text should only be picked from Consignee section
- destination_city: City name should only be picked from Consignee section
- destination_state: State abbreviation (2 letters) should only be picked from Consignee section

Extract the information and return it as a JSON object with the fields listed above.
If any information is not found, use null for that field.
Only return valid JSON, no additional text or explanation."""

        model_id = BEDROCK_MODEL_ID
        
        logger.info(f"Calling Claude to extract SHIPPER/CONSIGNEE from image for {carrier_name}...")
        logger.info(f"Image size: {len(image_bytes)} bytes ({len(image_bytes)/1024:.2f} KB)")
        
        response = bedrock.converse(
            modelId=model_id,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "image": {
                            "format": "png",
                            "source": {
                                "bytes": image_bytes
                            }
                        }
                    },
                    {"text": prompt}
                ]
            }],
            additionalModelRequestFields={
                "max_tokens": 2000,
            }
        )
        
        # Track cost
        try:
            usage = response.get('usage', {})
            input_tokens = usage.get('inputTokens', 0)
            output_tokens = usage.get('outputTokens', 0)
            cost_tracker.track_call(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                operation="claude_shipper_consignee_extraction"
            )
        except Exception as e:
            logger.warning(f"Failed to extract cost info: {e}")
        
        # Extract response text
        response_text = ""
        if 'content' in response:
            for msg in response.get("content", []):
                if isinstance(msg, dict) and "text" in msg:
                    response_text += msg["text"]
        
        # If not found in content, try output.message.content path
        if not response_text and 'output' in response:
            output = response.get('output', {})
            message = output.get('message', {})
            content_list = message.get('content', [])
            for msg in content_list:
                if isinstance(msg, dict) and "text" in msg:
                    response_text += msg["text"]
        
        if not response_text:
            logger.warning("No text response from Claude")
            return result
        
        logger.info(f"Claude response: {response_text[:200]}...")
        
        # Parse JSON from Claude response
        # Try to extract JSON from the response (might be wrapped in markdown code blocks)
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            json_str = response_text.strip()
        
        # Remove markdown code block markers if present
        json_str = re.sub(r'^```json\s*', '', json_str, flags=re.IGNORECASE)
        json_str = re.sub(r'^```\s*', '', json_str)
        json_str = re.sub(r'```\s*$', '', json_str)
        json_str = json_str.strip()
        
        try:
            parsed_result = json.loads(json_str)
            
            # Map to expected field names
            if 'source_name' in parsed_result or 'shipper_name' in parsed_result:
                result['source_name'] = parsed_result.get('source_name') or parsed_result.get('shipper_name')
            if 'source_address' in parsed_result or 'shipper_address' in parsed_result:
                result['source_address'] = parsed_result.get('source_address') or parsed_result.get('shipper_address')
            if 'source_city' in parsed_result or 'shipper_city' in parsed_result:
                result['source_city'] = parsed_result.get('source_city') or parsed_result.get('shipper_city')
            if 'source_state' in parsed_result or 'shipper_state' in parsed_result:
                result['source_state'] = parsed_result.get('source_state') or parsed_result.get('shipper_state')
            
            if 'destination_name' in parsed_result or 'consignee_name' in parsed_result:
                result['destination_name'] = parsed_result.get('destination_name') or parsed_result.get('consignee_name')
            if 'destination_address' in parsed_result or 'consignee_address' in parsed_result:
                result['destination_address'] = parsed_result.get('destination_address') or parsed_result.get('consignee_address')
            if 'destination_city' in parsed_result or 'consignee_city' in parsed_result:
                result['destination_city'] = parsed_result.get('destination_city') or parsed_result.get('consignee_city')
            if 'destination_state' in parsed_result or 'consignee_state' in parsed_result:
                result['destination_state'] = parsed_result.get('destination_state') or parsed_result.get('consignee_state')
            
            logger.info(f"Successfully extracted SHIPPER: {result.get('source_name', 'N/A')}, {result.get('source_city', 'N/A')}, {result.get('source_state', 'N/A')}")
            logger.info(f"Successfully extracted CONSIGNEE: {result.get('destination_name', 'N/A')}, {result.get('destination_city', 'N/A')}, {result.get('destination_state', 'N/A')}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from Claude response: {str(e)}")
            logger.error(f"Response text: {response_text}")
            # Fallback: cannot use text-based fallback when using image input
            logger.warning("JSON parsing failed, but cannot use text-based fallback with image input")
        
    except Exception as e:
        logger.error(f"Error extracting SHIPPER/CONSIGNEE with Claude: {str(e)}")
        logger.warning("Cannot use text-based fallback when using image input")
    
    return result


def extract_shipper_consignee_from_textract_with_llm(text: str) -> Dict[str, Any]:
    """
    LLM fallback to extract SHIPPER (source) and CONSIGNEE (destination) names from
    Textract-derived text. Used when source_name or destination_name are empty before API send.

    Args:
        text: Raw text from Textract (key-value pairs, tables, OCR lines).

    Returns:
        dict with source_name and destination_name (and optionally other fields).
    """
    result = {}
    if not text or (isinstance(text, str) and not text.strip()):
        logger.warning("extract_shipper_consignee_from_textract_with_llm: empty text")
        return result
    try:
        prompt = """Extract SHIPPER and CONSIGNEE information from the following text extracted from a document (OCR/Textract).

For SHIPPER (source):
- source_name: Company or person name (usually the first line of the text in the Shipper section, extract till '-' if present). Pick only from Shipper section.
- source_address, source_city, source_state: optional.

For CONSIGNEE (destination):
- destination_name: Company or person name (usually the first line in the Consignee section). Pick only from Consignee section.
- destination_address, destination_city, destination_state: optional.

Return a JSON object with at least source_name and destination_name. Use null for missing fields.
Only return valid JSON, no additional text or explanation."""

        user_content = f"{prompt}\n\nDocument text:\n{text[:50000]}"
        model_id = BEDROCK_MODEL_ID
        if not model_id:
            logger.warning("BEDROCK_MODEL_ID not set, skipping LLM fallback for source/destination")
            return result
        logger.info("Calling Claude to extract source/destination from Textract text (LLM fallback)")
        response = bedrock.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": user_content}]}],
            additionalModelRequestFields={"max_tokens": 2000},
        )
        try:
            usage = response.get("usage", {})
            cost_tracker.track_call(
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                operation="claude_shipper_consignee_textract_fallback",
            )
        except Exception as e:
            logger.warning(f"Failed to extract cost info: {e}")
        response_text = ""
        for msg in response.get("content", []):
            if isinstance(msg, dict) and "text" in msg:
                response_text += msg["text"]
        if not response_text and "output" in response:
            for msg in response.get("output", {}).get("message", {}).get("content", []):
                if isinstance(msg, dict) and "text" in msg:
                    response_text += msg["text"]
        if not response_text:
            logger.warning("No text response from Claude in Textract fallback")
            return result
        json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", response_text, re.DOTALL)
        json_str = json_match.group(0) if json_match else response_text.strip()
        json_str = re.sub(r"^```json\s*", "", json_str, flags=re.IGNORECASE)
        json_str = re.sub(r"^```\s*", "", json_str)
        json_str = re.sub(r"```\s*$", "", json_str).strip()
        parsed = json.loads(json_str)
        result["source_name"] = parsed.get("source_name") or parsed.get("shipper_name")
        result["destination_name"] = parsed.get("destination_name") or parsed.get("consignee_name")
        if result.get("source_name") or result.get("destination_name"):
            logger.info(f"LLM fallback extracted from Textract: source_name={result.get('source_name')}, destination_name={result.get('destination_name')}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON from Claude Textract fallback: {e}")
    except Exception as e:
        logger.error(f"Error in extract_shipper_consignee_from_textract_with_llm: {e}", exc_info=True)
    return result


def extract_shipper_consignee_fallback(text: str) -> Dict[str, Any]:
    """
    Fallback method to extract SHIPPER and CONSIGNEE using simple text parsing.
    Used when Claude extraction fails.
    """
    result = {}
    lines = text.split('\n')
    
    shipper_idx = -1
    consignee_idx = -1
    
    for i, line in enumerate(lines):
        if 'SHIPPER' in line.upper():
            shipper_idx = i
        if 'CONSIGNEE' in line.upper():
            consignee_idx = i
    
    # Extract SHIPPER details
    if shipper_idx >= 0:
        end_idx = consignee_idx if consignee_idx > shipper_idx else min(shipper_idx + 5, len(lines))
        shipper_lines = [l.strip() for l in lines[shipper_idx+1:end_idx] if l.strip()]
        
        if len(shipper_lines) >= 1:
            result['source_name'] = shipper_lines[0]
        if len(shipper_lines) >= 2:
            result['source_address'] = shipper_lines[1]
        if len(shipper_lines) >= 3:
            location_line = shipper_lines[2]
            parts = location_line.rsplit(',', 1)
            if len(parts) == 2:
                result['source_city'] = parts[0].strip()
                state_zip = parts[1].strip().split()
                if state_zip:
                    result['source_state'] = state_zip[0].strip()
    
    # Extract CONSIGNEE details
    if consignee_idx >= 0:
        consignee_lines = [l.strip() for l in lines[consignee_idx+1:min(consignee_idx + 5, len(lines))] if l.strip()]
        
        if len(consignee_lines) >= 1:
            result['destination_name'] = consignee_lines[0]
        if len(consignee_lines) >= 2:
            result['destination_address'] = consignee_lines[1]
        if len(consignee_lines) >= 3:
            location_line = consignee_lines[2]
            parts = location_line.rsplit(',', 1)
            if len(parts) == 2:
                result['destination_city'] = parts[0].strip()
                state_zip = parts[1].strip().split()
                if state_zip:
                    result['destination_state'] = state_zip[0].strip()
    
    return result


# ---------- CLAUDE-BASED BILL TO ADDRESS EXTRACTION ----------
def extract_bill_to_address_with_claude(image_bytes: bytes, carrier_name: str) -> Dict[str, Any]:
    """
    Extract BILL TO address details from image using Claude LLM.
    
    Args:
        image_bytes: The cropped image bytes (PNG format) containing BILL TO address information
        carrier_name: Name of the carrier (for logging)
    
    Returns:
        dict with bill to address details:
        - bill_to_name: Company or person name
        - bill_to_address: Street address
    """
    result = {}
    
    try:
        prompt = """Extract BILL TO address information from the document image provided.

The image contains billing information with BILL TO address details.
Please extract the following information:

For BILL TO:
- bill_to_name: Company or person name (usually the first line of the text)
- bill_to_address: Complete street address formatted as a single line with commas separating address components (e.g., "PO BOX 35620, MMCG, LOUISVILLE, KY 40232"). Do not include newline characters (\n) in the address.

Extract the information and return it as a JSON object with the fields listed above.
If any information is not found, use null for that field.
Only return valid JSON, no additional text or explanation."""

        model_id = BEDROCK_MODEL_ID
        
        logger.info(f"Calling Claude to extract BILL TO address from image for {carrier_name}...")
        logger.info(f"Image size: {len(image_bytes)} bytes ({len(image_bytes)/1024:.2f} KB)")
        
        response = bedrock.converse(
            modelId=model_id,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "image": {
                            "format": "png",
                            "source": {
                                "bytes": image_bytes
                            }
                        }
                    },
                    {"text": prompt}
                ]
            }],
            additionalModelRequestFields={
                "max_tokens": 2000,
            }
        )
        
        # Track cost
        try:
            usage = response.get('usage', {})
            input_tokens = usage.get('inputTokens', 0)
            output_tokens = usage.get('outputTokens', 0)
            cost_tracker.track_call(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                operation="claude_bill_to_address_extraction"
            )
        except Exception as e:
            logger.warning(f"Failed to extract cost info: {e}")
        
        # Extract response text
        response_text = ""
        if 'content' in response:
            for msg in response.get("content", []):
                if isinstance(msg, dict) and "text" in msg:
                    response_text += msg["text"]
        
        # If not found in content, try output.message.content path
        if not response_text and 'output' in response:
            output = response.get('output', {})
            message = output.get('message', {})
            content_list = message.get('content', [])
            for msg in content_list:
                if isinstance(msg, dict) and "text" in msg:
                    response_text += msg["text"]
        
        if not response_text:
            logger.warning("No text response from Claude")
            return result
        
        logger.info(f"Claude response: {response_text[:200]}...")
        
        # Parse JSON from Claude response
        # Try to extract JSON from the response (might be wrapped in markdown code blocks)
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            json_str = response_text.strip()
        
        # Remove markdown code block markers if present
        json_str = re.sub(r'^```json\s*', '', json_str, flags=re.IGNORECASE)
        json_str = re.sub(r'^```\s*', '', json_str)
        json_str = re.sub(r'```\s*$', '', json_str)
        json_str = json_str.strip()
        
        try:
            parsed_result = json.loads(json_str)
            
            # Map to expected field names and clean up newlines
            if 'bill_to_name' in parsed_result:
                bill_to_name = parsed_result.get('bill_to_name')
                # Remove newlines and extra whitespace from name
                if isinstance(bill_to_name, str):
                    bill_to_name = bill_to_name.replace('\n', ' ').replace('\r', ' ').strip()
                    # Collapse multiple spaces to single space
                    bill_to_name = ' '.join(bill_to_name.split())
                result['bill_to_name'] = bill_to_name
                
            if 'bill_to_address' in parsed_result:
                bill_to_address = parsed_result.get('bill_to_address')
                # Remove newlines and replace with commas or spaces
                if isinstance(bill_to_address, str):
                    # Replace newlines with commas and spaces
                    bill_to_address = bill_to_address.replace('\n', ', ').replace('\r', ', ')
                    # Remove any double commas or spaces
                    bill_to_address = re.sub(r',\s*,', ',', bill_to_address)
                    bill_to_address = bill_to_address.strip()
                    # Remove trailing comma if present
                    if bill_to_address.endswith(','):
                        bill_to_address = bill_to_address[:-1].strip()
                    # Collapse multiple spaces to single space
                    bill_to_address = ' '.join(bill_to_address.split())
                result['bill_to_address'] = bill_to_address
            
            logger.info(f"Successfully extracted BILL TO: {result.get('bill_to_name', 'N/A')}, Address: {result.get('bill_to_address', 'N/A')[:50]}...")
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from Claude response: {str(e)}")
            logger.error(f"Response text: {response_text}")
            logger.warning("JSON parsing failed, but cannot use text-based fallback with image input")
        
    except Exception as e:
        logger.error(f"Error extracting BILL TO address with Claude: {str(e)}")
        logger.warning("Cannot use text-based fallback when using image input")
    
    return result


# ---------- CARRIER-SPECIFIC SOURCE/DESTINATION EXTRACTION ----------
def extract_carrier_source_destination(
    bucket: str, 
    key: str, 
    carrier_name: str,
    start_header_keywords: List[str],
    end_header_keywords: List[str]
) -> Dict[str, Any]:
    """
    Extract source and destination details for carriers (Buddy Moore Trucking, M&M Cartage, etc.) by:
    1. Cropping the PDF section between start and end header keywords
    2. Re-extracting text from that cropped section
    3. Parsing SHIPPER and CONSIGNEE details
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        carrier_name: Name of the carrier (for logging)
        start_header_keywords: List of keywords to find the start header (e.g., ["Date", "DATE"])
        end_header_keywords: List of keywords to find the end header (e.g., ["SHIP DATE", "SHIP DATE:"])
    
    Returns:
        dict with source and destination details to override in extracted_info
    """
    if not PDF2IMAGE_AVAILABLE:
        logger.warning(f"pymupdf (fitz)/PIL not available, skipping {carrier_name} source/destination extraction")
        return {}
    
    logger.info(f"Starting {carrier_name} source/destination extraction for s3://{bucket}/{key}")
    
    START_HEADER_KEYWORDS = start_header_keywords
    END_HEADER_KEYWORDS = end_header_keywords
    
    def find_header_coordinates(blocks, header_keywords):
        """Find the y-coordinate of a header block that contains any of the keywords."""
        logger.info(f"Searching for header with keywords: {header_keywords}")
        line_blocks_checked = 0
        for block in blocks:
            if block.get('BlockType') == 'LINE':
                line_blocks_checked += 1
                text = block.get('Text', '').strip()
                # Check if any keyword is contained in the line text (not just exact match)
                for keyword in header_keywords:
                    if keyword.lower() in text.lower():
                        bbox = block.get('Geometry', {}).get('BoundingBox', {})
                        if bbox:
                            y_normalized = bbox.get('Top', 0)
                            logger.info(f"✓ Found header '{keyword}' (contained in text: '{text}') at normalized y={y_normalized:.4f}")
                            return y_normalized
        logger.info(f"Checked {line_blocks_checked} LINE blocks, header not found with keywords: {header_keywords}")
        return None
    
    def resize_image_for_textract(image, max_dimension=5000):
        """Resize image to fit within Textract limits."""
        width, height = image.size
        max_size = max(width, height)
        
        if max_size <= max_dimension:
            return image, 1.0
        
        scale = max_dimension / max_size
        new_width = int(width * scale)
        new_height = int(height * scale)
        
        logger.info(f"Resizing image from {width}x{height} to {new_width}x{new_height}")
        resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        return resized, scale
    
    try:
        # Download PDF from S3
        s3 = boto3.client('s3')
        pdf_obj = s3.get_object(Bucket=bucket, Key=key)
        pdf_bytes = pdf_obj['Body'].read()
        
        # Convert first page to image using fitz (PyMuPDF) - DPI 200 equivalent
        try:
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if len(pdf_doc) == 0:
                logger.error("PDF has no pages")
                return {}
            
            # Get first page (index 0)
            page = pdf_doc[0]
            
            # Convert to image with DPI 200 (zoom factor = 200/72 ≈ 2.78)
            zoom = 200 / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            
            # Convert to PIL Image
            img_data = pix.tobytes("png")
            page_pil_original = Image.open(BytesIO(img_data))
            
            pdf_doc.close()
            
        except Exception as e:
            logger.error(f"Could not convert PDF to image using fitz: {str(e)}")
            return {}
        page_height = page_pil_original.height
        page_width = page_pil_original.width
        logger.info(f"Processing page (size: {page_width}x{page_height})")
        
        # Resize for Textract
        page_pil_resized, scale_factor = resize_image_for_textract(page_pil_original, max_dimension=5000)
        
        # Convert to bytes for Textract
        img_byte_arr = BytesIO()
        page_pil_resized.save(img_byte_arr, format='PNG')
        img_bytes = img_byte_arr.getvalue()
        
        # Check file size (Textract has 10MB limit)
        if len(img_bytes) > 10 * 1024 * 1024:
            logger.warning(f"Image size ({len(img_bytes)/1024/1024:.2f}MB) exceeds 10MB, compressing...")
            img_byte_arr_jpeg = BytesIO()
            page_pil_resized.save(img_byte_arr_jpeg, format='JPEG', quality=85, optimize=True)
            img_bytes = img_byte_arr_jpeg.getvalue()
            logger.info(f"Compressed to {len(img_bytes)/1024/1024:.2f}MB")
        
        # Use Textract to detect text on full page first
        logger.info(f"=== TEXTRACT INPUT FOR HEADER DETECTION ===")
        logger.info(f"Image size: {len(img_bytes)} bytes ({len(img_bytes)/1024/1024:.2f} MB)")
        logger.info(f"Image format: {'JPEG' if len(img_bytes) > 10 * 1024 * 1024 else 'PNG'}")
        logger.info(f"Searching for START header keywords: {START_HEADER_KEYWORDS}")
        logger.info(f"Searching for END header keywords: {END_HEADER_KEYWORDS}")
        logger.info(f"Calling Textract analyze_document with FeatureTypes: ['FORMS', 'TABLES']")
        
        response = textract.analyze_document(
            Document={'Bytes': img_bytes},
            FeatureTypes=['FORMS', 'TABLES']
        )
        blocks = response.get('Blocks', [])
        logger.info(f"Textract found {len(blocks)} blocks")
        
        # Log sample of LINE blocks for debugging
        line_blocks = [block for block in blocks if block.get('BlockType') == 'LINE']
        logger.info(f"Found {len(line_blocks)} LINE blocks")
        if line_blocks:
            logger.info("Sample LINE blocks (first 10):")
            for i, block in enumerate(line_blocks[:10]):
                text = block.get('Text', '').strip()
                bbox = block.get('Geometry', {}).get('BoundingBox', {})
                y_normalized = bbox.get('Top', 0) if bbox else 0
                logger.info(f"  Line {i+1}: '{text}' at y={y_normalized:.4f}")
        
        logger.info(f"=== END TEXTRACT INPUT INFO ===")
        
        # Find start and end headers
        start_y_normalized = find_header_coordinates(blocks, START_HEADER_KEYWORDS)
        end_y_normalized = find_header_coordinates(blocks, END_HEADER_KEYWORDS)
        
        if start_y_normalized is None:
            logger.warning(f"Start header not found (keywords: {START_HEADER_KEYWORDS}), cannot crop")
            return {}
        
        # Convert normalized coordinates to pixels
        start_y_pixels = int(start_y_normalized * page_height)
        
        if end_y_normalized is not None and start_y_normalized < end_y_normalized:
            end_y_pixels = int(end_y_normalized * page_height)
            bottom = min(page_height, end_y_pixels)
        else:
            bottom = page_height
            logger.info("Using full page height as no valid end header found")
        
        # Crop the original full-size image
        left = 0
        top = max(0, start_y_pixels)
        right = page_width
        
        logger.info(f"Cropping: left={left}, top={top}, right={right}, bottom={bottom}")
        cropped_image = page_pil_original.crop((left, top, right, bottom))
        
        # Convert cropped image to bytes for Claude
        cropped_byte_arr = BytesIO()
        cropped_image.save(cropped_byte_arr, format='PNG')
        cropped_bytes = cropped_byte_arr.getvalue()
        logger.info(f"Cropped image size: {len(cropped_bytes)} bytes ({len(cropped_bytes)/1024:.2f} KB)")
        
        # Use Claude LLM to extract SHIPPER and CONSIGNEE details directly from image
        logger.info("Using Claude LLM to extract SHIPPER and CONSIGNEE information from cropped image...")
        result = extract_shipper_consignee_with_claude(cropped_bytes, carrier_name)
        
        # Clean up
        del page_pil_original
        del page_pil_resized
        del cropped_image
        
        return result
        
    except Exception as e:
        logger.error(f"Error in {carrier_name} source/destination extraction: {str(e)}")
        return {}

# ---------- CARRIER-SPECIFIC BILL TO ADDRESS EXTRACTION ----------
def extract_carrier_bill_to_address(
    bucket: str, 
    key: str, 
    carrier_name: str,
    start_header_keywords: List[str],
    end_header_keywords: List[str]
) -> Dict[str, Any]:
    """
    Extract bill to address details for carriers by:
    1. Cropping the PDF section between start and end header keywords
    2. Re-extracting text from that cropped section
    3. Parsing BILL TO address details
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        carrier_name: Name of the carrier (for logging)
        start_header_keywords: List of keywords to find the start header (e.g., ["SHIP DATE", "SHIP DATE:"])
        end_header_keywords: List of keywords to find the end header (e.g., ["Trailer No.", "Trailer No"])
    
    Returns:
        dict with bill to address details to override in extracted_info
    """
    if not PDF2IMAGE_AVAILABLE:
        logger.warning(f"pymupdf (fitz)/PIL not available, skipping {carrier_name} bill to address extraction")
        return {}
    
    logger.info(f"Starting {carrier_name} bill to address extraction for s3://{bucket}/{key}")
    
    START_HEADER_KEYWORDS = start_header_keywords
    END_HEADER_KEYWORDS = end_header_keywords
    
    def find_header_coordinates(blocks, header_keywords):
        """Find the y-coordinate of a header block that contains any of the keywords."""
        logger.info(f"Searching for header with keywords: {header_keywords}")
        line_blocks_checked = 0
        for block in blocks:
            if block.get('BlockType') == 'LINE':
                line_blocks_checked += 1
                text = block.get('Text', '').strip()
                # Check if any keyword is contained in the line text (not just exact match)
                for keyword in header_keywords:
                    if keyword.lower() in text.lower():
                        bbox = block.get('Geometry', {}).get('BoundingBox', {})
                        if bbox:
                            y_normalized = bbox.get('Top', 0)
                            logger.info(f"✓ Found header '{keyword}' (contained in text: '{text}') at normalized y={y_normalized:.4f}")
                            return y_normalized
        logger.info(f"Checked {line_blocks_checked} LINE blocks, header not found with keywords: {header_keywords}")
        return None
    
    def resize_image_for_textract(image, max_dimension=5000):
        """Resize image to fit within Textract limits."""
        width, height = image.size
        max_size = max(width, height)
        
        if max_size <= max_dimension:
            return image, 1.0
        
        scale = max_dimension / max_size
        new_width = int(width * scale)
        new_height = int(height * scale)
        
        logger.info(f"Resizing image from {width}x{height} to {new_width}x{new_height}")
        resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        return resized, scale
    
    try:
        # Download PDF from S3
        s3 = boto3.client('s3')
        pdf_obj = s3.get_object(Bucket=bucket, Key=key)
        pdf_bytes = pdf_obj['Body'].read()
        
        # Convert first page to image using fitz (PyMuPDF) - DPI 200 equivalent
        try:
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            if len(pdf_doc) == 0:
                logger.error("PDF has no pages")
                return {}
            
            # Get first page (index 0)
            page = pdf_doc[0]
            
            # Convert to image with DPI 200 (zoom factor = 200/72 ≈ 2.78)
            zoom = 200 / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            
            # Convert to PIL Image
            img_data = pix.tobytes("png")
            page_pil_original = Image.open(BytesIO(img_data))
            
            pdf_doc.close()
            
        except Exception as e:
            logger.error(f"Could not convert PDF to image using fitz: {str(e)}")
            return {}
        page_height = page_pil_original.height
        page_width = page_pil_original.width
        logger.info(f"Processing page (size: {page_width}x{page_height})")
        
        # Resize for Textract
        page_pil_resized, scale_factor = resize_image_for_textract(page_pil_original, max_dimension=5000)
        
        # Convert to bytes for Textract
        img_byte_arr = BytesIO()
        page_pil_resized.save(img_byte_arr, format='PNG')
        img_bytes = img_byte_arr.getvalue()
        
        # Check file size (Textract has 10MB limit)
        if len(img_bytes) > 10 * 1024 * 1024:
            logger.warning(f"Image size ({len(img_bytes)/1024/1024:.2f}MB) exceeds 10MB, compressing...")
            img_byte_arr_jpeg = BytesIO()
            page_pil_resized.save(img_byte_arr_jpeg, format='JPEG', quality=85, optimize=True)
            img_bytes = img_byte_arr_jpeg.getvalue()
            logger.info(f"Compressed to {len(img_bytes)/1024/1024:.2f}MB")
        
        # Use Textract to detect text on full page first
        logger.info(f"=== TEXTRACT INPUT FOR BILL TO ADDRESS HEADER DETECTION ===")
        logger.info(f"Image size: {len(img_bytes)} bytes ({len(img_bytes)/1024/1024:.2f} MB)")
        logger.info(f"Image format: {'JPEG' if len(img_bytes) > 10 * 1024 * 1024 else 'PNG'}")
        logger.info(f"Searching for START header keywords: {START_HEADER_KEYWORDS}")
        logger.info(f"Searching for END header keywords: {END_HEADER_KEYWORDS}")
        logger.info(f"Calling Textract analyze_document with FeatureTypes: ['FORMS', 'TABLES']")
        
        response = textract.analyze_document(
            Document={'Bytes': img_bytes},
            FeatureTypes=['FORMS', 'TABLES']
        )
        blocks = response.get('Blocks', [])
        logger.info(f"Textract found {len(blocks)} blocks")
        
        # Log sample of LINE blocks for debugging
        line_blocks = [block for block in blocks if block.get('BlockType') == 'LINE']
        logger.info(f"Found {len(line_blocks)} LINE blocks")
        if line_blocks:
            logger.info("Sample LINE blocks (first 10):")
            for i, block in enumerate(line_blocks[:10]):
                text = block.get('Text', '').strip()
                bbox = block.get('Geometry', {}).get('BoundingBox', {})
                y_normalized = bbox.get('Top', 0) if bbox else 0
                logger.info(f"  Line {i+1}: '{text}' at y={y_normalized:.4f}")
        
        logger.info(f"=== END TEXTRACT INPUT INFO ===")
        
        # Find start and end headers
        start_y_normalized = find_header_coordinates(blocks, START_HEADER_KEYWORDS)
        end_y_normalized = find_header_coordinates(blocks, END_HEADER_KEYWORDS)
        
        if start_y_normalized is None:
            logger.warning(f"Start header not found (keywords: {START_HEADER_KEYWORDS}), cannot crop")
            return {}
        
        # Convert normalized coordinates to pixels
        start_y_pixels = int(start_y_normalized * page_height)
        
        if end_y_normalized is not None and start_y_normalized < end_y_normalized:
            end_y_pixels = int(end_y_normalized * page_height)
            bottom = min(page_height, end_y_pixels)
        else:
            bottom = page_height
            logger.info("Using full page height as no valid end header found")
        
        # Crop the original full-size image
        left = 0
        top = max(0, start_y_pixels)
        right = page_width
        
        logger.info(f"Cropping: left={left}, top={top}, right={right}, bottom={bottom}")
        cropped_image = page_pil_original.crop((left, top, right, bottom))
        
        # Convert cropped image to bytes for Claude
        cropped_byte_arr = BytesIO()
        cropped_image.save(cropped_byte_arr, format='PNG')
        cropped_bytes = cropped_byte_arr.getvalue()
        logger.info(f"Cropped image size: {len(cropped_bytes)} bytes ({len(cropped_bytes)/1024:.2f} KB)")
        
        # Use Claude LLM to extract BILL TO address details directly from image
        logger.info("Using Claude LLM to extract BILL TO address information from cropped image...")
        result = extract_bill_to_address_with_claude(cropped_bytes, carrier_name)
        
        # Clean up
        del page_pil_original
        del page_pil_resized
        del cropped_image
        
        return result
        
    except Exception as e:
        logger.error(f"Error in {carrier_name} bill to address extraction: {str(e)}")
        return {}


# ---------- AVERITT EXPRESS SHIPPER/CONSIGNEE EXTRACTION ----------
def extract_averitt_shipper_consignee(
    bucket: str,
    key: str,
    transaction_id: str
) -> Dict[str, Any]:
    """
    Extract SHIPPER and CONSIGNEE details from Averitt Express invoice by:
    1. Converting first page of PDF to image
    2. Using Textract to find keyword positions
    3. Cropping between "AVERITT EXPRESS INC" (top) and "ORIGIN" (bottom)
    4. Storing cropped image in S3
    5. Using Claude vision to extract both shipper and consignee details in one call
    
    Returns dict with source and destination details.
    """
    if not PDF2IMAGE_AVAILABLE:
        logger.warning("pymupdf (fitz)/PIL not available, skipping Averitt shipper/consignee extraction")
        return {}
    
    result = {}
    
    try:
        # Download PDF from S3
        logger.info(f"Downloading PDF from s3://{bucket}/{key} for Averitt extraction")
        pdf_obj = s3.get_object(Bucket=bucket, Key=key)
        pdf_bytes = pdf_obj['Body'].read()
        
        # Open PDF with PyMuPDF
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if pdf_doc.page_count == 0:
            logger.warning("PDF has no pages")
            return {}
        
        # Helper function to resize image for Textract
        def resize_image_for_textract(image, max_dimension=5000):
            """Resize image to fit within Textract limits."""
            width, height = image.size
            if max(width, height) <= max_dimension:
                return image, 1.0
            scale = max_dimension / max(width, height)
            new_width = int(width * scale)
            new_height = int(height * scale)
            return image.resize((new_width, new_height), Image.Resampling.LANCZOS), scale
        
        # For Averitt, the freight bill details are typically on page 2 (index 1)
        # Try page 2 first, fall back to page 1 if start keyword not found
        page_indices_to_try = []
        if pdf_doc.page_count > 1:
            page_indices_to_try = [1, 0]  # Try page 2 first, then page 1
        else:
            page_indices_to_try = [0]  # Only page 1 available
        
        start_y_normalized = None
        end_y_normalized = None
        full_page_image = None
        page_width = 0
        page_height = 0
        
        # Try each page in order
        for page_index in page_indices_to_try:
            logger.info(f"Trying page {page_index + 1} of {pdf_doc.page_count} for Averitt extraction")
            
            page = pdf_doc[page_index]
            
            # Render page at high resolution
            mat = fitz.Matrix(3.0, 3.0)  # 3x zoom for better quality
            pix = page.get_pixmap(matrix=mat)
            
            # Convert to PIL Image
            img_data = pix.tobytes("png")
            full_page_image = Image.open(BytesIO(img_data))
            
            page_width, page_height = full_page_image.size
            logger.info(f"Averitt page size: {page_width}x{page_height}")
            
            # Resize for Textract
            page_pil_resized, scale_factor = resize_image_for_textract(full_page_image, max_dimension=5000)
            
            # Convert to bytes for Textract
            img_byte_arr = BytesIO()
            page_pil_resized.save(img_byte_arr, format='PNG')
            img_bytes = img_byte_arr.getvalue()
            
            # Compress if needed
            if len(img_bytes) > 10 * 1024 * 1024:
                logger.warning(f"Image size ({len(img_bytes)/1024/1024:.2f}MB) exceeds 10MB, compressing...")
                img_byte_arr_jpeg = BytesIO()
                page_pil_resized.save(img_byte_arr_jpeg, format='JPEG', quality=85, optimize=True)
                img_bytes = img_byte_arr_jpeg.getvalue()
                logger.info(f"Compressed to {len(img_bytes)/1024/1024:.2f}MB")
            
            # Call Textract to get text blocks with positions
            logger.info("Calling Textract to detect text positions for Averitt cropping...")
            textract_response = textract.detect_document_text(Document={'Bytes': img_bytes})
            
            # Find start and end keywords
            start_keywords = ["AVERITT EXPRESS INC"]
            end_keywords = ["ORIGIN"]
            
            start_y_normalized = None
            end_y_normalized = None
            
            # Search for keywords in Textract blocks
            for block in textract_response.get('Blocks', []):
                if block['BlockType'] == 'LINE':
                    text = block.get('Text', '').strip().upper()
                    geometry = block.get('Geometry', {})
                    bbox = geometry.get('BoundingBox', {})
                    
                    # Check for start keyword
                    if start_y_normalized is None:
                        for kw in start_keywords:
                            if kw.upper() in text:
                                start_y_normalized = bbox.get('Top', 0)
                                logger.info(f"Found start keyword '{kw}' at normalized Y={start_y_normalized}")
                                break
                    
                    # Check for end keyword
                    if end_y_normalized is None:
                        for kw in end_keywords:
                            if kw.upper() == text or text.startswith(kw.upper()):
                                end_y_normalized = bbox.get('Top', 0)
                                logger.info(f"Found end keyword '{kw}' at normalized Y={end_y_normalized}")
                                break
            
            # If start keyword found on this page, break and proceed with cropping
            if start_y_normalized is not None:
                logger.info(f"Start keyword found on page {page_index + 1}, proceeding with cropping")
                break
            else:
                logger.warning(f"Start keyword not found on page {page_index + 1}")
                # Continue to next page in the loop
        
        # Close PDF after trying all pages
        pdf_doc.close()
        
        # If start keyword not found on any page, return empty dict
        if start_y_normalized is None:
            logger.warning(f"Start header not found on any page (keywords: {start_keywords}), cannot crop")
            return {}
        
        # Convert normalized coordinates to actual pixels
        start_y_pixels = int(start_y_normalized * page_height)
        
        if end_y_normalized is None:
            logger.warning(f"End header not found (keywords: {end_keywords}), using full page height")
            end_y_pixels = page_height
        else:
            end_y_pixels = int(end_y_normalized * page_height)
        
        # Ensure valid crop region
        if end_y_pixels <= start_y_pixels:
            end_y_pixels = page_height
            logger.info("End Y is before or equal to start Y, using full page height")
        
        # Crop the image
        left = 0
        top = max(0, start_y_pixels)
        right = page_width
        bottom = min(page_height, end_y_pixels)
        
        logger.info(f"Cropping Averitt section: left={left}, top={top}, right={right}, bottom={bottom}")
        cropped_image = full_page_image.crop((left, top, right, bottom))
        
        # Convert cropped image to bytes
        cropped_byte_arr = BytesIO()
        cropped_image.save(cropped_byte_arr, format='PNG')
        cropped_bytes = cropped_byte_arr.getvalue()
        logger.info(f"Cropped image size: {len(cropped_bytes)} bytes ({len(cropped_bytes)/1024:.2f} KB)")
        
        # Store cropped image in S3
        cropped_s3_key = f"{CROPPED_IMAGES_S3_PREFIX}{transaction_id}_averitt_shipper_consignee.png"
        try:
            s3.put_object(
                Bucket=bucket,
                Key=cropped_s3_key,
                Body=cropped_bytes,
                ContentType='image/png'
            )
            logger.info(f"Stored cropped image at s3://{bucket}/{cropped_s3_key}")
        except Exception as s3_err:
            logger.warning(f"Failed to store cropped image in S3: {str(s3_err)}")
        
        # Call Claude vision model to extract shipper and consignee details
        prompt = """Extract SHIPPER and CONSIGNEE information from this Averitt Express freight bill image.

The image shows a cropped section of an Averitt invoice containing shipper and consignee information:

**CRITICAL - LEFT vs RIGHT IDENTIFICATION:**
- **LEFT SIDE** (left column): Contains CONSIGNEE information (destination/receiver)
- **RIGHT SIDE** (right column): Contains SHIPPER information (source/sender)
- Look for "CONSIGNEE ACCOUNT NO." label (on the LEFT) and "SHIPPER ACCOUNT NO." label (on the RIGHT)

**EXTRACTION RULES:**
For each side, extract line by line:
- Line 1: Company name (PRESERVE THE ENTIRE FIRST LINE including any location qualifiers)
  * Examples: "GE APPLIANCES - DECATUR", "GENERAL ELECTRIC APPLIANCE - DECATUR", "GE BLDG 3"
  * DO NOT remove or separate location qualifiers like "- DECATUR", "- MRO", "BLDG 3" from the company name
  * Extract the complete line 1 text as-is
- Line 2: Address or additional location code (could be street address or a short code like "AP1", "MRO")
- Line 3+: City, state, zip

**CRITICAL - PRESERVE FULL COMPANY NAME:**
- If line 1 contains "GE APPLIANCES - DECATUR", extract the FULL string "GE APPLIANCES - DECATUR" as the company name
- If line 1 contains "INDUSTRIAL WIRE - MRO", extract the FULL string "INDUSTRIAL WIRE - MRO" as the company name
- DO NOT split line 1 into company + location parts - keep it as one complete name

**OCR CORRECTION - READ CAREFULLY:**
When extracting short codes from line 2, pay special attention to:
- "AP1", "AP2", "AP3", "AP4", "AP5" (common location codes - starts with "A")
- "MRO", "CAM", "CMC", "KLC", "SLC", "RPF", "DPF", "APF", "APX" 
- DO NOT confuse "AP1" with "LP1" or "API" - if you see what looks like "LP1" or "LPI", it's likely "AP1"
- DO NOT confuse "AP" codes with other letters - always starts with "A" not "L"
- Read the text very carefully character by character

**IMPORTANT - DO NOT CONFUSE LEFT AND RIGHT:**

For SHIPPER (source - **RIGHT SIDE**, above "SHIPPER ACCOUNT NO."):
- Extract from the RIGHT column/side of the image
- source_name: FULL company name from line 1 (preserve entire line including location qualifiers)
- source_address: Line 2 content (could be street address, "AP1", "MRO", "CONTAINER MANAGEMENT CENTER", etc.)
  * If line 2 looks like a short code (2-4 characters), read it very carefully
  * Common codes: AP1, AP2, AP3, AP4, AP5, MRO, CAM, CMC, KLC
- source_city: City name from line 3+
- source_state: State abbreviation - 2 letters only

For CONSIGNEE (destination - **LEFT SIDE**, above "CONSIGNEE ACCOUNT NO."):
- Extract from the LEFT column/side of the image
- destination_name: FULL company name from line 1 (preserve entire line including location qualifiers)
- destination_address: Line 2 content (street address or location code)
- destination_city: City name from line 3+
- destination_state: State abbreviation - 2 letters only

**EXAMPLES:**
Example 1 - Location qualifier in name:
```
LEFT SIDE:                    RIGHT SIDE:
GE APPLIANCES - DECATUR       BARKSDALE AND ASSOCIATES INC
2328 POINT MALLARD DR         205 SWETT AVE WHSE 3
DECATUR, AL 35601             AMERICUS, GA 31709
CONSIGNEE ACCOUNT NO. XXX     SHIPPER ACCOUNT NO. YYY
```

Extract:
- destination_name: "GE APPLIANCES - DECATUR" (FULL line 1 from LEFT - do not remove "- DECATUR")
- destination_address: "2328 POINT MALLARD DR" (LEFT line 2)
- destination_city: "DECATUR" (LEFT line 3)
- destination_state: "AL"
- source_name: "BARKSDALE AND ASSOCIATES INC" (FULL line 1 from RIGHT)
- source_address: "205 SWETT AVE WHSE 3" (RIGHT line 2)

Example 2 - Short code in address:
```
LEFT SIDE:                    RIGHT SIDE:
INDUSTRIAL WIRE               GE APPLIANCE
1436 HIGGS RD                 AP1  (NOT "LP1" or "LPI" - carefully read as "AP1")
LEWISBURG, TN 37091           LOUISVILLE, KY 40225
CONSIGNEE ACCOUNT NO. XXX     SHIPPER ACCOUNT NO. YYY
```

Extract:
- destination_name: "INDUSTRIAL WIRE" (from LEFT line 1)
- destination_address: "1436 HIGGS RD" (from LEFT line 2)
- source_name: "GE APPLIANCE" (from RIGHT line 1)
- source_address: "AP1" (from RIGHT line 2 - read carefully, not "LP1")

Return ONLY valid JSON with these exact field names:
{
  "source_name": "FULL company name from RIGHT side line 1",
  "source_address": "RIGHT side line 2 (read carefully)",
  "source_city": "city from RIGHT side",
  "source_state": "ST",
  "destination_name": "FULL company name from LEFT side line 1",
  "destination_address": "LEFT side line 2",
  "destination_city": "city from LEFT side",
  "destination_state": "ST"
}"""

        model_id = BEDROCK_MODEL_ID
        logger.info("Calling Claude to extract SHIPPER/CONSIGNEE from Averitt cropped image...")
        
        response = bedrock.converse(
            modelId=model_id,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "image": {
                            "format": "png",
                            "source": {
                                "bytes": cropped_bytes
                            }
                        }
                    },
                    {"text": prompt}
                ]
            }],
            additionalModelRequestFields={
                "max_tokens": 2000,
            }
        )
        
        # Track cost
        try:
            usage = response.get('usage', {})
            input_tokens = usage.get('inputTokens', 0)
            output_tokens = usage.get('outputTokens', 0)
            cost_tracker.track_call(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                operation="claude_averitt_shipper_consignee_extraction"
            )
        except Exception as e:
            logger.warning(f"Failed to extract cost info: {e}")
        
        # Extract response text
        response_text = ""
        if 'content' in response:
            for msg in response.get("content", []):
                if isinstance(msg, dict) and "text" in msg:
                    response_text += msg["text"]
        
        if not response_text and 'output' in response:
            output = response.get('output', {})
            message = output.get('message', {})
            content_list = message.get('content', [])
            for msg in content_list:
                if isinstance(msg, dict) and "text" in msg:
                    response_text += msg["text"]
        
        if not response_text:
            logger.warning("No text response from Claude")
            return result
        
        logger.info(f"Claude response: {response_text[:300]}...")
        
        # Parse JSON from Claude response
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            json_str = response_text.strip()
        
        # Remove markdown code block markers if present
        json_str = re.sub(r'^```json\s*', '', json_str, flags=re.IGNORECASE)
        json_str = re.sub(r'^```\s*', '', json_str)
        json_str = re.sub(r'```\s*$', '', json_str)
        json_str = json_str.strip()
        
        try:
            parsed_result = json.loads(json_str)
            
            # Extract all fields
            for field in ['source_name', 'source_address', 'source_city', 'source_state',
                         'destination_name', 'destination_address', 'destination_city', 'destination_state']:
                if field in parsed_result and parsed_result[field]:
                    result[field] = parsed_result[field]
            
            logger.info(f"Successfully extracted Averitt shipper/consignee: {_format_json_for_log(result)}")
            
        except json.JSONDecodeError as json_err:
            logger.error(f"Failed to parse Claude JSON response: {str(json_err)}")
            logger.error(f"Response was: {json_str[:500]}")
        
        # Clean up
        del full_page_image
        del page_pil_resized
        del cropped_image
        
        return result
        
    except Exception as e:
        logger.error(f"Error in Averitt shipper/consignee extraction: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {}


def _extracted_vendor_reference_upper(extracted_info: Dict[str, Any]) -> str:
    """Root-level vendor_reference_id from structured extraction (string or value/explanation dict)."""
    v = extracted_info.get("vendor_reference_id")
    if isinstance(v, dict) and "value" in v:
        v = v.get("value", "")
    return str(v or "").upper().strip()


def _s3_safe_path_segment(value: Any, max_len: int = 160) -> str:
    """Sanitize job_id / attachment_id (and similar) for use in S3 object keys."""
    s = str(value).strip() if value is not None else ""
    if not s:
        return "unknown"
    out = re.sub(r"[^a-zA-Z0-9._-]+", "_", s)
    return (out[:max_len] if out else "unknown")


def _jr_schugel_image_extraction_eligible(carrier_name: Optional[str], extracted_info: Dict[str, Any]) -> bool:
    """Run cropped-image logistics extraction for J & R Schugel (SCAC SJRG or JR_SCHUGEL / name match)."""
    if _extracted_vendor_reference_upper(extracted_info) == "SJRG":
        return True
    if not carrier_name or not isinstance(carrier_name, str):
        return False
    c = carrier_name.strip()
    if "Schugel" in c:
        return True
    if c.upper().replace(" ", "_") == "JR_SCHUGEL":
        return True
    if c.replace(" ", "") == "J&RSchugel":
        return True
    return False


def extract_jr_schugel_logistics_with_claude(image_bytes: bytes) -> Dict[str, Any]:
    """
    Extract BILL TO, SHIPPER (source), and CONSIGNEE (destination) from a cropped J & R Schugel
    invoice image (region from TRUCK# row through the line-items table DESCRIPTION header).
    """
    result: Dict[str, Any] = {}
    if not image_bytes:
        return result
    model_id = BEDROCK_MODEL_ID
    if not model_id:
        logger.warning("BEDROCK_MODEL_ID not set, skipping J & R Schugel image extraction")
        return result
    prompt = """You are given a cropped image of page 1 of a J & R Schugel freight invoice.
The crop spans from the TRUCK # / TRAILER # / DRIVER header row down through the BILL TO, SHIPPER, and CONSIGNEE blocks and includes the top of the charges table (through the DESCRIPTION column header).

Extract exactly these fields and return ONE JSON object (no markdown, no commentary):
- bill_to_name: Top-left, BILL TO block. If the line with "BILL TO:" only has a short code after the colon (e.g. GELO) and the real customer name is on the next line (e.g. GENERAL ELECTRIC (60)), use that next line as bill_to_name. If the full name appears on the same line as BILL TO:, use the text after the label.
- bill_to_address: From the BILL TO section only: street, city, state ZIP as one line where possible. Omit asterisk/disclaimer lines (e.g. lines containing only * or "EDI" / "DO NOT MAIL"). Use null if no street/address lines exist.
- source_name: Center SHIPPER block: combine the first substantive line after "SHIPPER:" with the second line (e.g. account/plant code + company name) into one string; separate with ", " if helpful.
- source_address: Page 1, center-left of the image, **SHIPPER** block only. Omit the name / code lines (e.g. lines like "GELA1", "GE PLANT WHSE RPO", "ROPER CORPORATION"). **Output the street plus the city/state/ZIP line as one string** (comma-separated is fine). Example — SHIPPER block reads: `GE PLANT WHSE RPO`, `ROPER CORPORATION`, `1507 BROOMTOWN RD`, `LAFAYETTE, GA 30728` → **source_address** = **`1507 BROOMTOWN RD, LAFAYETTE, GA 30728`** (do not include the company name lines).
- source_city: From the last line of the SHIPPER address that looks like "CITY, ST ZIP", the word before the comma (city).
- source_state: Two-letter state abbreviation immediately after the comma on that same line.
- destination_name: Top-right CONSIGNEE block: combine the first line after "CONSIGNEE:" with the second line (e.g. code + company) like source_name.
- destination_address: Page 1, top-right of the image, **CONSIGNEE** block only. **Street line only** — the road line before the "CITY, ST ZIP" line (omit CONSIGNEE code and company name lines). Example — CONSIGNEE block: `GELO`, `GENERAL ELECTRIC (60)`, `4000 BUECHEL BANK RD`, `LOUISVILLE, KY 40225` → **destination_address** = **`4000 BUECHEL BANK RD`** only (not `LOUISVILLE, KY 40225`).
- destination_city: From the CONSIGNEE city/state/ZIP line, the text before the comma.
- destination_state: Two-letter state after the comma on that line.

Use null for any field not clearly visible. Use strings (not nested objects). Only valid JSON."""

    try:
        logger.info("Calling Claude for J & R Schugel cropped logistics (BILL TO + SHIPPER + CONSIGNEE)")
        response = bedrock.converse(
            modelId=model_id,
            messages=[{
                "role": "user",
                "content": [
                    {"image": {"format": "png", "source": {"bytes": image_bytes}}},
                    {"text": prompt},
                ],
            }],
            additionalModelRequestFields={"max_tokens": 2500},
        )
        try:
            usage = response.get("usage", {})
            cost_tracker.track_call(
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                operation="claude_jr_schugel_logistics_crop",
            )
        except Exception as e:
            logger.warning(f"Failed to track cost for JR Schugel image extraction: {e}")

        response_text = ""
        for msg in response.get("content", []):
            if isinstance(msg, dict) and "text" in msg:
                response_text += msg["text"]
        if not response_text and "output" in response:
            for msg in response.get("output", {}).get("message", {}).get("content", []):
                if isinstance(msg, dict) and "text" in msg:
                    response_text += msg["text"]
        if not response_text:
            logger.warning("J & R Schugel image extraction: empty model response")
            return result

        json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", response_text, re.DOTALL)
        json_str = json_match.group(0) if json_match else response_text.strip()
        json_str = re.sub(r"^```json\s*", "", json_str, flags=re.IGNORECASE)
        json_str = re.sub(r"^```\s*", "", json_str)
        json_str = re.sub(r"```\s*$", "", json_str).strip()
        parsed = json.loads(json_str)
        if not isinstance(parsed, dict):
            return result
        for key in (
            "bill_to_name", "bill_to_address",
            "source_name", "source_city", "source_state", "source_address",
            "destination_name", "destination_city", "destination_state", "destination_address",
        ):
            val = parsed.get(key)
            if val is None or val == "null":
                continue
            if isinstance(val, str):
                s = val.replace("\n", " ").strip()
                if s:
                    result[key] = " ".join(s.split())
            elif isinstance(val, (int, float)):
                result[key] = str(val)
        logger.info(f"J & R Schugel image extraction parsed keys: {list(result.keys())}")
    except json.JSONDecodeError as e:
        logger.error(f"J & R Schugel image extraction JSON parse error: {e}")
    except Exception as e:
        logger.error(f"J & R Schugel image extraction error: {e}", exc_info=True)
    return result


def extract_jr_schugel_cropped_logistics_from_pdf(
    bucket: str,
    key: str,
    *,
    archive_cropped_png_bucket: Optional[str] = None,
    archive_cropped_png_email_id: Optional[str] = None,
    archive_cropped_png_attachment_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Crop page 1 from the TRUCK # row to the charges table DESCRIPTION header (Textract-guided),
    then run Claude on the crop for BILL TO + SHIPPER + CONSIGNEE fields.

    If archive_cropped_png_bucket is set (with email and attachment ids), saves the PNG crop under
    ``{CROPPED_IMAGES_S3_PREFIX}<job_id>/<attachment_id>_jr_schugel_<timestamp>.png`` in that bucket.
    """
    if not PDF2IMAGE_AVAILABLE:
        logger.warning("pymupdf (fitz)/PIL not available, skipping J & R Schugel cropped extraction")
        return {}

    start_keywords = ["TRUCK #", "TRUCK#", "TRUCK NO", "TRUCK NO."]
    end_keywords = ["DESCRIPTION", "Description"]

    def find_start_y(blocks, header_keywords):
        for block in blocks:
            if block.get("BlockType") != "LINE":
                continue
            text = block.get("Text", "").strip()
            for keyword in header_keywords:
                if keyword.lower() in text.lower():
                    bbox = block.get("Geometry", {}).get("BoundingBox", {})
                    if bbox:
                        y_norm = bbox.get("Top", 0)
                        logger.info(f"JR Schugel crop start: matched '{keyword}' in '{text}' at y={y_norm:.4f}")
                        return y_norm
        return None

    def find_end_bottom_norm_below(blocks, header_keywords, min_y_norm, min_gap: float = 0.002):
        """
        Among LINE blocks matching end keywords with Top strictly below the start row,
        pick the topmost match (first table DESCRIPTION header below TRUCK row).
        Return normalized bottom = Top + Height so the crop includes that header line.
        """
        best_top = None
        best_h = None
        for block in blocks:
            if block.get("BlockType") != "LINE":
                continue
            text = (block.get("Text") or "").strip()
            if not text:
                continue
            tlow = text.lower()
            for kw in header_keywords:
                if kw.lower() in tlow:
                    bbox = block.get("Geometry", {}).get("BoundingBox", {}) or {}
                    y_top = float(bbox.get("Top", 0) or 0)
                    if y_top > min_y_norm + min_gap:
                        if best_top is None or y_top < best_top:
                            best_top = y_top
                            best_h = float(bbox.get("Height", 0.01) or 0.01)
                            logger.info(f"JR Schugel crop end candidate: '{text}' top={y_top:.4f} h={best_h:.4f}")
        if best_top is None:
            return None
        return best_top + best_h

    def resize_image_for_textract(image, max_dimension=5000):
        width, height = image.size
        max_size = max(width, height)
        if max_size <= max_dimension:
            return image, 1.0
        scale = max_dimension / max_size
        new_width = int(width * scale)
        new_height = int(height * scale)
        return image.resize((new_width, new_height), Image.Resampling.LANCZOS), scale

    try:
        s3 = boto3.client("s3")
        pdf_obj = s3.get_object(Bucket=bucket, Key=key)
        pdf_bytes = pdf_obj["Body"].read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if len(pdf_doc) == 0:
            logger.error("JR Schugel crop: PDF has no pages")
            return {}
        page = pdf_doc[0]
        zoom = 200 / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        img_data = pix.tobytes("png")
        page_pil_original = Image.open(BytesIO(img_data))
        pdf_doc.close()

        page_height = page_pil_original.height
        page_width = page_pil_original.width
        page_pil_resized, _ = resize_image_for_textract(page_pil_original, max_dimension=5000)
        img_byte_arr = BytesIO()
        page_pil_resized.save(img_byte_arr, format="PNG")
        img_bytes = img_byte_arr.getvalue()
        if len(img_bytes) > 10 * 1024 * 1024:
            jbuf = BytesIO()
            page_pil_resized.save(jbuf, format="JPEG", quality=85, optimize=True)
            img_bytes = jbuf.getvalue()

        response = textract.analyze_document(
            Document={"Bytes": img_bytes},
            FeatureTypes=["FORMS", "TABLES"],
        )
        blocks = response.get("Blocks", [])
        start_y_norm = find_start_y(blocks, start_keywords)
        if start_y_norm is None:
            logger.warning(f"JR Schugel crop: start header not found (keywords {start_keywords})")
            return {}

        end_bottom_norm = find_end_bottom_norm_below(blocks, end_keywords, start_y_norm)
        start_y_pixels = int(start_y_norm * page_height)
        if end_bottom_norm is not None and end_bottom_norm > start_y_norm:
            bottom = min(page_height, max(start_y_pixels + 1, int(end_bottom_norm * page_height)))
        else:
            bottom = page_height
            logger.info("JR Schugel crop: DESCRIPTION header not found below TRUCK row; cropping to page bottom")

        top = max(0, start_y_pixels)
        cropped_image = page_pil_original.crop((0, top, page_width, bottom))
        cropped_byte_arr = BytesIO()
        cropped_image.save(cropped_byte_arr, format="PNG")
        cropped_bytes = cropped_byte_arr.getvalue()
        logger.info(f"JR Schugel cropped region top={top} bottom={bottom} size={len(cropped_bytes)} bytes")

        if archive_cropped_png_bucket and archive_cropped_png_bucket.strip():
            try:
                safe_job = _s3_safe_path_segment(archive_cropped_png_email_id)
                safe_att = _s3_safe_path_segment(archive_cropped_png_attachment_id)
                ts_ms = int(time.time() * 1000)
                crop_key = f"{CROPPED_IMAGES_S3_PREFIX}{safe_job}/{safe_att}_jr_schugel_{ts_ms}.png"
                s3.put_object(
                    Bucket=archive_cropped_png_bucket.strip(),
                    Key=crop_key,
                    Body=cropped_bytes,
                    ContentType="image/png",
                )
                logger.info(
                    f"Saved JR Schugel cropped PNG to s3://{archive_cropped_png_bucket.strip()}/{crop_key}"
                )
            except Exception as upload_err:
                logger.warning(f"JR Schugel: failed to upload cropped image to S3: {upload_err}")

        out = extract_jr_schugel_logistics_with_claude(cropped_bytes)
        del page_pil_original
        del page_pil_resized
        del cropped_image
        return out
    except Exception as e:
        logger.error(f"JR Schugel cropped PDF extraction failed: {e}", exc_info=True)
        return {}


# ---------- LLM EXTRACTION ----------
def extract_information_with_claude(
    text: str,
    carrier_name: Optional[str] = None,
    charge_correction_hint: Optional[str] = None,
    date_retry_note: Optional[str] = None,
) -> dict:
    """
    Extract structured information using Claude with internal field validation.
    Retries once if Claude call fails or if expected fields are missing.
    Uses carrier-specific prompt if carrier_name is provided, otherwise uses generic prompt.
    
    Args:
        text: The document text to extract information from
        carrier_name: Optional carrier name to use carrier-specific prompt template
        charge_correction_hint: Optional hint appended when charge totals mismatch
        date_retry_note: Optional format-correction note appended when invoice_date is stale
    """
    # Define expected fields inside the function - updated to match the schema
    expected_fields = [
        "invoice_number", "invoice_date", "vendor_reference_id", "payment_due_date",
        "bill_of_lading_number", "shipments", "currency", "total_invoice_value", "payment_terms"
    ]

    def call_claude(prompt: str, model_id: str) -> dict:
        """Call Claude and return structured output or raise error."""
        logger.info("Calling Claude model...")
        enhanced_prompt = f"""
                    {prompt}

                    IMPORTANT FORMATTING INSTRUCTIONS:
                    1. Use the validate_invoice_data tool to structure your response.
                    2. For the "shipments" field, always provide a properly formatted JSON array, even if there's only one shipment.
                    3. For the "charges" field within each shipment, always provide a properly formatted JSON array.
                    4. Ensure all JSON is valid - no trailing commas, properly closed brackets, and proper nesting.
                    5. For numeric fields like "total_invoice_value" and "charge_gross_amount", provide numeric values without quotes.
                    6. For string fields, provide properly quoted string values.
                    7. Always include "confidence" and "explanation" fields for each value.
                    8. Never include <parameter> tags or other XML-like markup in your JSON.
                    9. Ensure all required fields are present in your response.

                    Remember, your response must be valid JSON that strictly follows the schema provided by the tool.
                    """
        try:
            response = bedrock.converse(
                modelId=model_id,
                messages=[{
                    "role": "user",
                    "content": [{"text": enhanced_prompt}]
                }],
                toolConfig={
                    "tools": [{
                        "toolSpec": {
                            "name": "validate_invoice_data",
                            "description": "Extract and validate invoice fields.",
                            "inputSchema": {"json": TOOL_SCHEMA}
                        }
                    }],
                },
                additionalModelRequestFields={
                    "reasoning_config": {
                        "type": "enabled",
                        "budget_tokens": 3000
                    },
                    "max_tokens": 100000, 
                }
            )
            logger.info(f"Claude response received:\n{_format_json_for_log(response)}")

            # Enhanced Cost Tracking
            try:
                usage = response.get('usage', {})
                input_tokens = usage.get('inputTokens', 0)
                output_tokens = usage.get('outputTokens', 0)
                
                # Use centralized cost tracker
                cost_tracker.track_call(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    operation="claude_extraction"
                )
                
            except Exception as e:
                logger.warning(f"Failed to extract cost info: {e}")
                logger.warning("Cost tracking will be incomplete for this call")

            # Extract structured_output from the tool use
            structured_output = None
            
            # First check if we have content in the response
            if 'content' in response:
                for msg in response.get("content", []):
                    if isinstance(msg, dict) and "toolUse" in msg:
                        tool_data = msg.get("toolUse", {})
                        if "input" in tool_data:
                            structured_output = tool_data["input"]
                            logger.info("Extracted tool response from input field")
                            break
            
            # If not found in content, try the output.message.content path
            if not structured_output and 'output' in response:
                output = response.get('output', {})
                message = output.get('message', {})
                content_list = message.get('content', [])
                
                for msg in content_list:
                    if isinstance(msg, dict) and "toolUse" in msg:
                        tool_data = msg.get("toolUse", {})
                        if "input" in tool_data:
                            structured_output = tool_data["input"]
                            logger.info("Extracted tool response from output.message.content path")
                            break
            
            if not structured_output:
                logger.error("Failed to extract structured output from Claude response")
                logger.error(f"Response structure:\n{_format_json_for_log(response)}")
                raise ValueError("No structured output found in Claude response")
                
            return structured_output
            
        except Exception as e:
            logger.error(f"Error calling Claude: {str(e)}")
            raise

    try:
        # Try to get carrier-specific prompt if carrier_name is provided
        prompt = None
        if carrier_name:
            logger.info(f"Attempting to use carrier-specific prompt for: {carrier_name}")
            prompt_template = get_prompt_template(carrier_name)
            if prompt_template:
                # Format the carrier-specific prompt with the document text
                try:
                    prompt = prompt_template.format(pdf_text=text)
                    logger.info(f"Using carrier-specific prompt for {carrier_name}")
                except Exception as e:
                    logger.warning(f"Error formatting carrier-specific prompt: {str(e)}, falling back to generic prompt")
                    prompt = None
        
        # Fallback to generic prompt if carrier-specific prompt is not available
        if not prompt:
            logger.info("Using generic extraction prompt")
            prompt = f"""Extract structured information from the following invoice document text.

Please extract all relevant invoice fields including:
- Invoice number, invoice date, payment due date
- Vendor information and reference IDs
- Bill of lading number
- Shipment details (shipment number, dates, mode, source/destination locations, weights, volumes)
- Charges (charge codes, names, amounts, currency)
- Total invoice value and payment terms
- Any other relevant invoice information

Document text:
{text}

Use the validate_invoice_data tool to structure your response with proper JSON format."""
        
        if charge_correction_hint:
            prompt = prompt + charge_correction_hint
            logger.info("CHARGE_CORRECTION | correction hint appended to prompt")

        if date_retry_note:
            prompt = prompt + date_retry_note
            logger.info("INVOICE_DATE_VALIDATION | date retry note appended to prompt")

        logger.info(f"Prompt preview (first 200 chars): {prompt[:200]}")

        model_id = BEDROCK_MODEL_ID

        # First attempt
        try:
            structured_output = call_claude(prompt, model_id)
        except (TimeoutError, EndpointConnectionError, ClientError, ReadTimeoutError) as net_err:
            logger.warning(f"Claude call timed out: {net_err}.")
            raise ValueError("Claude call failed due to timeout or connection issue.")

        # Validate fields
        if not structured_output:
            logger.warning("No structured output received from Claude. Retrying once...")
            structured_output = call_claude(prompt, model_id)

        if not structured_output:
            raise ValueError("Claude did not return structured output after retry.")

        # Check for missing top-level fields
        missing_fields = [field for field in expected_fields if field not in structured_output]
        if missing_fields:
            logger.warning(f"Structured output missing fields: {missing_fields}")
            logger.info("Retrying once again for missing field resolution...")

            # Retry for field issues
            structured_output = call_claude(prompt, model_id)
            logger.info(f"Retry attempt received structured output: {bool(structured_output)}")
            missing_fields = [field for field in expected_fields if field not in structured_output]

            if missing_fields:
                logger.error(f"Retry failed. Still missing fields: {missing_fields}")
                raise ValueError(f"Claude response missing required fields: {missing_fields}")
        
        # Process shipments field
        if "shipments" in structured_output:
            # Handle shipments as string
            if isinstance(structured_output["shipments"], str):
                try:
                    # Clean up the string for JSON parsing
                    shipments_str = structured_output["shipments"].strip()
                    
                    # Find the JSON array part
                    start_idx = shipments_str.find('[')
                    end_idx = shipments_str.rfind(']')
                    
                    if start_idx >= 0 and end_idx > start_idx:
                        shipments_str = shipments_str[start_idx:end_idx+1]
                        
                    # Remove trailing commas and other issues
                    shipments_str = re.sub(r',\s*]', ']', shipments_str)
                    shipments_str = re.sub(r',\s*}', '}', shipments_str)
                    
                    # Parse the JSON
                    shipments_array = json.loads(shipments_str)
                    structured_output["shipments"] = shipments_array
                    logger.info(f"Successfully parsed shipments string into array with {len(shipments_array)} items")
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse shipments string as JSON: {e}")
                    # Extract using regex as fallback
                    try:
                        # Find all JSON objects in the string
                        object_pattern = r'\{[^{}]*\}'
                        objects = re.findall(object_pattern, shipments_str)
                        
                        if objects:
                            shipments_array = []
                            for obj_str in objects:
                                try:
                                    obj = json.loads(obj_str)
                                    shipments_array.append(obj)
                                except json.JSONDecodeError:
                                    pass  # Skip invalid objects
                            
                            if shipments_array:
                                structured_output["shipments"] = shipments_array
                                logger.info(f"Extracted {len(shipments_array)} shipments using regex approach")
                            else:
                                structured_output["shipments"] = []
                                logger.warning("Could not extract any valid shipments, setting to empty list")
                        else:
                            structured_output["shipments"] = []
                            logger.warning("No JSON objects found in shipments string, setting to empty list")
                    except Exception as regex_err:
                        logger.error(f"Regex extraction failed: {regex_err}")
                        structured_output["shipments"] = []
                        logger.warning("Set shipments to empty list due to parsing error")
            elif not isinstance(structured_output["shipments"], list):
                # If it's not a list or string, convert to a single-item list
                structured_output["shipments"] = [structured_output["shipments"]]
                logger.info("Converted non-list shipments to array")
        else:
            # Ensure shipments exists
            structured_output["shipments"] = []
            logger.warning("No shipments field found, adding empty array")
        
        # Ensure each shipment has a charges array
        for i, shipment in enumerate(structured_output.get("shipments", [])):
            if isinstance(shipment, dict):
                if "charges" not in shipment:
                    shipment["charges"] = []
                    logger.info(f"Added empty charges array to shipment {i}")
                elif isinstance(shipment["charges"], str):
                    # Parse charges string to array
                    try:
                        charges_str = shipment["charges"].strip()
                        start_idx = charges_str.find('[')
                        end_idx = charges_str.rfind(']')
                        
                        if start_idx >= 0 and end_idx > start_idx:
                            charges_str = charges_str[start_idx:end_idx+1]
                            
                        charges_str = re.sub(r',\s*]', ']', charges_str)
                        charges_str = re.sub(r',\s*}', '}', charges_str)
                        
                        charges_array = json.loads(charges_str)
                        shipment["charges"] = charges_array
                        logger.info(f"Parsed charges string into array with {len(charges_array)} items")
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse charges string as JSON: {e}")
                        shipment["charges"] = []
                        logger.warning(f"Set charges to empty list in shipment {i} due to parsing error")
        
        # Ensure additional_info exists
        if "additional_info" not in structured_output:
            logger.info("Adding empty additional_info array")
            structured_output["additional_info"] = []

        logger.info(f"Final structured output received successfully")
        
        # Format dates to DD-MMM-YYYY format
        structured_output = format_dates_in_extracted_data(structured_output)
        logger.info("Applied date formatting to extracted data")
        
        return structured_output

    except Exception as e:
        logger.error(f"Final failure in Claude processing: {str(e)}")
        raise

# ---------- EMAIL FUNCTIONS ----------
from typing import Dict, Any

def format_invoice_data_as_html(data: Dict[str, Any]) -> str:
    """Format invoice data (shipment and charges detail) as HTML."""
    try:
        # Main invoice field order: adjust for your needs
        order = [
            'invoice_number', 'invoice_date', 'vendor_reference_id', 'payment_due_date',
            'bill_of_lading_number','total_invoice_value'
        ]
        html = """
        <div style="font-family: Arial, sans-serif; max-width: 900px; margin: 0 auto;">
            <h2>Invoice Details</h2>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <th style="text-align: left; padding: 8px; border: 1px solid #ddd;">Field</th>
                    <th style="text-align: left; padding: 8px; border: 1px solid #ddd;">Value</th>
                </tr>
        """
        # Invoice top-level fields
        for key in order:
            if key in data and key not in ['taxes','additional_info','shipments']:
                value = data[key]
                html += f"""
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;"><strong>{key}</strong></td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{value}</td>
                </tr>
                """
        # Add customer if present
        if 'customer' in data:
            html += f"""
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;"><strong>Customer</strong></td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{data['customer'].get('vendor_name', '')}</td>
                </tr>
            """
        html += "</table>"

        # --- Shipments Section ---
        shipment_fields = [
            "shipment_number","shipment_creation_date", "mode", "source_name", "source_city",
            "source_country", "source_state", "destination_name", "destination_city", "destination_country",
            "destination_state","shipment_weight", "shipment_volume","shipment_total_value"
        ]

        if 'shipments' in data and isinstance(data['shipments'], list) and data['shipments']:
            for shipment in data['shipments']:
                # Shipment details table
                html += """
                <hr>
                <h3>Shipment Details</h3>
                <table style="width: 100%; border-collapse: collapse;">
                  <tr>
                    <th style="padding: 8px; border: 1px solid #ddd;">Field</th>
                    <th style="text-align: left; padding: 8px; border: 1px solid #ddd;">Value</th>
                  </tr>
                """
                for field in shipment_fields:
                    value = shipment.get(field, "")
                    if value is None:
                        value = ""
                    html += f"""
                      <tr>
                        <td style="padding: 8px; border: 1px solid #ddd;"><strong>{field}</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{value}</td>
                      </tr>
                    """
                html += "</table>"

                # Charges table
                if "charges" in shipment and isinstance(shipment["charges"], list) and shipment["charges"]:
                    html += """
                    <h4>Charges</h4>
                    <table style="width: 100%; border-collapse: collapse;">
                      <tr>
                        <th style="padding: 8px; border: 1px solid #ddd;">Charge Name</th>
                        <th style="padding: 8px; border: 1px solid #ddd;">Amount</th>
                        <th style="padding: 8px; border: 1px solid #ddd;">Code</th>
                        <th style="padding: 8px; border: 1px solid #ddd;">Currency</th>
                      </tr>
                    """
                    for charge in shipment["charges"]:
                        html += f"""
                        <tr>
                            <td style="padding: 8px; border: 1px solid #ddd;">{charge.get('charge_name','')}</td>
                            <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{charge.get('charge_gross_amount','')}</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{charge.get('charge_code','')}</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{charge.get('currency','')}</td>
                        </tr>
                        """
                    html += "</table>"
        html += "</div>"
        return html
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error formatting invoice data as HTML: {str(e)}")
        raise

def send_email(
    smtp_server: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
    json_data: Dict[str, Any],
    message_id: Optional[str] = None,
    quoted_sender: Optional[str] = None,
    quoted_date: Optional[str] = None,
    quoted_subject: Optional[str] = None,
    quoted_body: Optional[str] = None,
    reply_recipients: Optional[List[str]] = None,
    attachments: Optional[List[tuple]] = None,
) -> tuple:
    """Send email with pre-formatted HTML content using SMTP, with optional quoted thread block and file attachments.
    attachments: list of (filename, bytes) tuples. Returns html_text, subject, sender, date, and Message-ID."""
    try:
        logger.info(f"Preparing email to {to_email}")
        
        # Check if sender and receiver are the same - skip sending if they match
        from_email_normalized = from_email.lower().strip() if from_email else ""
        
        # Check against to_email
        if to_email and from_email_normalized == to_email.lower().strip():
            logger.info(f"Sender and receiver are the same ({from_email}), skipping email send")
            return None, None, None, None, None
        
        # Check against reply_recipients if provided
        if reply_recipients:
            for recipient in reply_recipients:
                if recipient and from_email_normalized == recipient.lower().strip():
                    logger.info(f"Sender and receiver are the same ({from_email}), skipping email send")
                    return None, None, None, None, None
        
        # Validate from_email before proceeding
        if not validate_email_address(from_email):
            logger.error(f"Invalid from_email: {from_email}")
            return None, None, None, None, None
        
        # Create a multipart message and set headers.
        # Use "mixed" when attachments are present so files sit alongside the HTML body.
        if attachments:
            msg = MIMEMultipart("mixed")
        else:
            msg = MIMEMultipart("alternative")
        
        # Set subject with "Re:" prefix if not already present
        if not subject.lower().startswith("re:"):
            msg["Subject"] = f"Re: {subject}"
        else:
            msg["Subject"] = subject
            
        msg["From"] = from_email
        
        # Use reply_recipients if provided, otherwise use original to_email
        if reply_recipients:
            if len(reply_recipients) == 0:
                logger.info("No reply recipients specified - skipping email send")
                return
            
            # Validate recipient addresses
            valid_recipients = []
            for recipient in reply_recipients:
                if validate_email_address(recipient):
                    valid_recipients.append(recipient.strip())
                else:
                    logger.error(f"Invalid recipient email format: {recipient}")
            
            if not valid_recipients:
                logger.error("No valid recipients found - skipping email send")
                return
                
            msg["To"] = ", ".join(valid_recipients)
            logger.info(f"Using routing logic - replying to: {valid_recipients}")
        else:
            # Validate to_email
            if not validate_email_address(to_email):
                logger.error(f"Invalid to_email: {to_email}")
                return
                
            msg["To"] = to_email.strip()
        
        # Set proper threading headers
        if message_id:
            # Ensure message ID has angle brackets for proper threading
            if not message_id.startswith('<'):
                formatted_message_id = f"<{message_id}>"
            else:
                formatted_message_id = message_id
                
            # Set In-Reply-To to the immediate parent message
            msg["In-Reply-To"] = formatted_message_id
            
            # Set References to maintain the email chain
            if " " in formatted_message_id:
                msg["References"] = formatted_message_id
            else:
                msg["References"] = formatted_message_id
            
            # Add thread-topic for Outlook compatibility
            clean_subject = subject.replace("Re: ", "").replace("RE: ", "").strip()
            msg["Thread-Topic"] = clean_subject
            msg["Thread-Index"] = message_id.strip('<>')  # For Outlook
            
            logger.info(f"Setting invoice email threading headers - In-Reply-To: {formatted_message_id}")
            logger.info(f"Setting invoice email References: {msg['References']}")
            logger.info(f"Setting invoice email Thread-Topic: {clean_subject}")
        else:
            logger.warning("No message_id provided for invoice email threading")
        
        # Build quoted block if info is provided (shipment.py style)
        quoted_block = ""
        if quoted_sender and quoted_date and quoted_subject and quoted_body:
            quoted_block = f"""
            <div style='margin-top: 20px; border-top: 1px solid #ddd;'>
                <div style='color:gray; font-size:small; margin: 10px 0;'>
                    On {quoted_date}, {quoted_sender} wrote:<br>
                    <b>Subject:</b> {quoted_subject}
                </div>
                <div style='border-left:4px solid #ccc; padding-left:15px; margin:10px 0;'>
                    {quoted_body}
                </div>
            </div>
            """
        
        # Use the pre-formatted HTML body directly, quoted block after main content
        html_text = f"""
        <html>
        <head>
            <style>
                .disclaimer {{
                    font-size: 12px;
                    color: #666;
                    margin-bottom: 20px;
                    padding: 10px;
                    background-color: #f8f9fa;
                    border-left: 4px solid #007bff;
                }}
            </style>
        </head>
        <body>
            <div class=\"disclaimer\">
                This is an automated email from PiAgent. Please do not reply to this email.
            </div>
            <p>Dear Recipient,</p>
            {body}
            <p>Thank you,<br><strong>Freehand AI – Automated Invoice Processing</strong></p>
            {quoted_block}
        </body>
        </html>
        """
        
        # Attach HTML content.
        # When the outer container is "mixed" (attachments present), wrap HTML in an
        # "alternative" sub-part so mail clients render the body correctly.
        html_part = MIMEText(html_text, "html")
        if attachments:
            alt_part = MIMEMultipart("alternative")
            alt_part.attach(html_part)
            msg.attach(alt_part)
        else:
            msg.attach(html_part)

        # Attach files fetched from S3
        if attachments:
            for filename, file_bytes in attachments:
                attachment_part = MIMEApplication(file_bytes, Name=filename)
                attachment_part["Content-Disposition"] = f'attachment; filename="{filename}"'
                msg.attach(attachment_part)
                logger.info(f"Attached file '{filename}' ({len(file_bytes)} bytes) to email")

        logger.info(f"Preparing to send email with subject: {subject}")
        logger.info(f"Sending email to {to_email}")

        # Send the email via SMTP with SSL context
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=context)
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
            logger.info("Email sent successfully!")
        # Return html_text, subject, sender, date, and Message-ID for quoting in next email
        return html_text, msg["Subject"], msg["From"], datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S %z'), msg.get("Message-ID")
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")
        raise

def send_unclassified_notification(
    smtp_server: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    from_email: str,
    to_email: str,
    subject: str,
    original_body: str,
    filename: str,
    message_id: Optional[str] = None,
    quoted_sender: Optional[str] = None,
    quoted_date: Optional[str] = None,
    quoted_subject: Optional[str] = None,
    quoted_body: Optional[str] = None,
    reply_recipients: Optional[List[str]] = None
) -> None:
    """Send notification email for unclassified documents."""
    try:
        logger.info(f"Preparing unclassified document notification to {to_email}")
        
        # Check if sender and receiver are the same - skip sending if they match
        from_email_normalized = from_email.lower().strip() if from_email else ""
        
        # Check against to_email
        if to_email and from_email_normalized == to_email.lower().strip():
            logger.info(f"Sender and receiver are the same ({from_email}), skipping email send")
            return
        
        # Check against reply_recipients if provided
        if reply_recipients:
            for recipient in reply_recipients:
                if recipient and from_email_normalized == recipient.lower().strip():
                    logger.info(f"Sender and receiver are the same ({from_email}), skipping email send")
                    return
        
        # Validate from_email before proceeding
        if not validate_email_address(from_email):
            logger.error(f"Invalid from_email: {from_email}")
            return
        
        msg = MIMEMultipart("alternative")
        
        # Set subject with "Re:" prefix if not already present
        if not subject.lower().startswith("re:"):
            msg["Subject"] = f"Re: Unable to Process Invoice - {subject}"
        else:
            msg["Subject"] = f"Unable to Process Invoice - {subject}"
            
        msg["From"] = from_email
        
        # Use reply_recipients if provided, otherwise use original to_email
        if reply_recipients:
            if len(reply_recipients) == 0:
                logger.info("No reply recipients specified - skipping email send")
                return
            
            # Validate recipient addresses
            valid_recipients = []
            for recipient in reply_recipients:
                if validate_email_address(recipient):
                    valid_recipients.append(recipient.strip())
                else:
                    logger.error(f"Invalid recipient email format: {recipient}")
            
            if not valid_recipients:
                logger.error("No valid recipients found - skipping email send")
                return
                
            msg["To"] = ", ".join(valid_recipients)
            logger.info(f"Using routing logic - replying to: {valid_recipients}")
        else:
            # Validate to_email
            if not validate_email_address(to_email):
                logger.error(f"Invalid to_email: {to_email}")
                return
                
            msg["To"] = to_email.strip()
        
        # Set proper threading headers
        if message_id:
            # Ensure message ID has angle brackets for proper threading
            if not message_id.startswith('<'):
                formatted_message_id = f"<{message_id}>"
            else:
                formatted_message_id = message_id
                
            # Set In-Reply-To to the immediate parent message
            msg["In-Reply-To"] = formatted_message_id
            
            # Set References to maintain the email chain
            if " " in formatted_message_id:
                msg["References"] = formatted_message_id
            else:
                msg["References"] = formatted_message_id
            
            # Add thread-topic for Outlook compatibility
            clean_subject = subject.replace("Re: ", "").replace("RE: ", "").strip()
            msg["Thread-Topic"] = clean_subject
            msg["Thread-Index"] = message_id.strip('<>')  # For Outlook
            
            logger.info(f"Setting unclassified notification threading headers - In-Reply-To: {formatted_message_id}")
            logger.info(f"Setting unclassified notification References: {msg['References']}")
            logger.info(f"Setting unclassified notification Thread-Topic: {clean_subject}")
        else:
            logger.warning("No message_id provided for unclassified notification threading")
        
        # Build quoted block if info is provided (shipment.py style)
        quoted_block = ""
        if quoted_sender and quoted_date and quoted_subject and quoted_body:
            quoted_block = f"""
            <div style='margin-top: 20px; border-top: 1px solid #ddd;'>
                <div style='color:gray; font-size:small; margin: 10px 0;'>
                    On {quoted_date}, {quoted_sender} wrote:<br>
                    <b>Subject:</b> {quoted_subject}
                </div>
                <div style='border-left:4px solid #ccc; padding-left:15px; margin:10px 0;'>
                    {quoted_body}
                </div>
            </div>
            """
        
        html_text = f"""
        <html>
        <head>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                .error-box {{
                    background-color: #fff3f3;
                    border-left: 4px solid #dc3545;
                    padding: 20px;
                    margin: 20px 0;
                }}
                .disclaimer {{
                    font-size: 12px;
                    color: #666;
                    margin-top: 20px;
                    padding: 10px;
                    background-color: #f8f9fa;
                    border-left: 4px solid #007bff;
                }}
                .file-info {{
                    background-color: #f8f9fa;
                    padding: 15px;
                    margin: 15px 0;
                    border-radius: 4px;
                }}
            </style>
        </head>
        <body>
            <p>Dear Recipient,</p>
            <div class="error-box">
                <h2 style="color: #dc3545; margin-top: 0;">Unable to Process Invoice</h2>
                <p>We were unable to process the attached document as an invoice. This could be due to one of the following reasons:</p>
                <ul>
                    <li>The document is not a valid invoice</li>
                    <li>The document format is not supported</li>
                    <li>The document is not from a supported carrier</li>
                    <li>The document is unclear or unreadable</li>
                </ul>
            </div>
            
            <div class="file-info">
                <p><strong>File Name:</strong> {filename}</p>
                <p><strong>Original Subject:</strong> {subject}</p>
            </div>
            
            <p>Please ensure that:</p>
            <ul>
                <li>The document is a clear, readable invoice</li>
                <li>The invoice is from one of our supported carriers</li>
                <li>The document is in PDF/TIFF format</li>
            </ul>
            
            <p>Please send a new email with a valid invoice attachment.</p>
            <p>Thank you,<br><strong>Freehand AI – Automated Invoice Processing</strong></p>
            <div class="disclaimer">
                This is an automated email from PiAgent. Please do not reply to this email.
            </div>
            {quoted_block}
        </body>
        </html>
        """
        
        html_part = MIMEText(html_text, "html")
        msg.attach(html_part)
        
        logger.info(f"Preparing to send unclassified document notification to {to_email}")
        
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=context)
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        logger.info("Unclassified document notification sent successfully!")
            
    except Exception as e:
        logger.error(f"Error sending unclassified document notification: {str(e)}")
        raise

def send_error_notification(
    smtp_server: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    from_email: str,
    to_email: str,
    subject: str,
    original_body: str,
    filename: str,
    message_id: Optional[str] = None,
    validation_errors: Dict[str, List] = None,
    quoted_sender: Optional[str] = None,
    quoted_date: Optional[str] = None,
    quoted_subject: Optional[str] = None,
    quoted_body: Optional[str] = None,
    reply_recipients: Optional[List[str]] = None
) -> None:
    """Send notification email for unclassified documents with required field errors only."""
    try:
        logger.info(f"Preparing error notification to {to_email}")
        
        # Check if sender and receiver are the same - skip sending if they match
        from_email_normalized = from_email.lower().strip() if from_email else ""
        
        # Check against to_email
        if to_email and from_email_normalized == to_email.lower().strip():
            logger.info(f"Sender and receiver are the same ({from_email}), skipping email send")
            return
        
        # Check against reply_recipients if provided
        if reply_recipients:
            for recipient in reply_recipients:
                if recipient and from_email_normalized == recipient.lower().strip():
                    logger.info(f"Sender and receiver are the same ({from_email}), skipping email send")
                    return
        
        # Validate from_email before proceeding
        if not validate_email_address(from_email):
            logger.error(f"Invalid from_email: {from_email}")
            return
        
        # Check if there are required field errors
        required_errors = []
        if validation_errors:
            required_errors = validation_errors.get('required_field_errors', [])
        
        # Only send email if there are required field errors
        if not required_errors:
            logger.info("No required field errors present, skipping error notification email.")
            return
        
        msg = MIMEMultipart("alternative")
        
        # Set subject with "Re:" prefix if not already present
        if not subject.lower().startswith("re:"):
            msg["Subject"] = f"Re: {subject}"
        else:
            msg["Subject"] = subject
            
        msg["From"] = from_email
        
        # Use reply_recipients if provided, otherwise use original to_email
        if reply_recipients:
            if len(reply_recipients) == 0:
                logger.info("No reply recipients specified - skipping email send")
                return
            
            # Validate recipient addresses
            valid_recipients = []
            for recipient in reply_recipients:
                if validate_email_address(recipient):
                    valid_recipients.append(recipient.strip())
                else:
                    logger.error(f"Invalid recipient email format: {recipient}")
            
            if not valid_recipients:
                logger.error("No valid recipients found - skipping email send")
                return
                
            msg["To"] = ", ".join(valid_recipients)
            logger.info(f"Using routing logic - replying to: {valid_recipients}")
        else:
            # Validate to_email
            if not validate_email_address(to_email):
                logger.error(f"Invalid to_email: {to_email}")
                return
                
            msg["To"] = to_email.strip()
        
        # Set proper threading headers
        if message_id:
            # Ensure message ID has angle brackets for proper threading
            if not message_id.startswith('<'):
                formatted_message_id = f"<{message_id}>"
            else:
                formatted_message_id = message_id
                
            # Set In-Reply-To to the immediate parent message
            msg["In-Reply-To"] = formatted_message_id
            
            # Set References to maintain the email chain
            if " " in formatted_message_id:
                msg["References"] = formatted_message_id
            else:
                msg["References"] = formatted_message_id
            
            # Add thread-topic for Outlook compatibility
            clean_subject = subject.replace("Re: ", "").replace("RE: ", "").strip()
            msg["Thread-Topic"] = clean_subject
            msg["Thread-Index"] = message_id.strip('<>')  # For Outlook
            
            logger.info(f"Setting error notification threading headers - In-Reply-To: {formatted_message_id}")
            logger.info(f"Setting error notification References: {msg['References']}")
            logger.info(f"Setting error notification Thread-Topic: {clean_subject}")
        else:
            logger.warning("No message_id provided for error notification threading")
        
        # Prepare error HTML for required field errors only
        error_html = '''
        <div class="validation-errors" style="background-color: #fff3f3; padding: 20px; margin: 20px 0;">
            <h3 style="color: #dc3545;">Required Details Missing</h3>
            <p>The following required details are missing from the invoice, making it invalid:</p>
            <ul style="margin-left: 20px;">
        '''
        for err in required_errors:
            error_html += f'<li><strong>{err["field"]}</strong>: {err["message"]}</li>'
        error_html += '''
            </ul>
            <p>Please provide the missing information and resubmit the document.</p>
        </div>
        '''

        # Build quoted block if info is provided
        quoted_block = ""
        if quoted_sender and quoted_date and quoted_subject and quoted_body:
            quoted_block = f"""
            <div style='margin-top: 20px; border-top: 1px solid #ddd;'>
                <div style='color:gray; font-size:small; margin: 10px 0;'>
                    On {quoted_date}, {quoted_sender} wrote:<br>
                    <b>Subject:</b> {quoted_subject}
                </div>
                <div style='border-left:4px solid #ccc; padding-left:15px; margin:10px 0;'>
                    {quoted_body}
                </div>
            </div>
            """
        
        html_text = f"""
        <html>
        <head>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                .disclaimer {{
                    font-size: 12px;
                    color: #666;
                    margin-top: 20px;
                    padding: 10px;
                    background-color: #f8f9fa;
                    border-left: 4px solid #007bff;
                }}
                .file-info {{
                    background-color: #f8f9fa;
                    padding: 15px;
                    margin: 15px 0;
                    border-radius: 4px;
                }}
            </style>
        </head>
        <body>
            <div class="disclaimer">
                This is an automated email from PiAgent. Please do not reply to this email.
            </div>
            <p>Dear Recipient,</p>
            <div class="file-info">
                <p><strong>File Name:</strong> {filename}</p>
                <p><strong>Original Subject:</strong> {subject}</p>
            </div>
            
            {error_html}
            <p>Thank you,<br><strong>Freehand AI – Automated Invoice Processing</strong></p>
            
            {quoted_block}

        </body>
        </html>
        """
        
        html_part = MIMEText(html_text, "html")
        msg.attach(html_part)
        
        logger.info(f"Preparing to send error notification to {to_email}")
        
        # EMAIL SENDING CODE COMMENTED OUT
        # context = ssl.create_default_context()
        # with smtplib.SMTP(smtp_server, smtp_port) as server:
        #     server.starttls(context=context)
        #     server.login(smtp_username, smtp_password)
        #     server.send_message(msg)
        #     logger.info("Error notification sent successfully!")
        logger.info("Email sending is disabled - error notification would have been sent here")
            
    except Exception as e:
        logger.error(f"Error sending error notification: {str(e)}")
        raise

# ---------- VALIDATION HELPER FUNCTIONS ----------

def check_payment_terms_prepaid(extracted_data: Dict[str, Any]) -> bool:
    """
    Check if the payment terms in the extracted data are 'prepaid'.
    Returns True if payment terms are prepaid, False otherwise.
    """
    payment_terms = extracted_data.get('payment_terms', '').lower().strip()
    return payment_terms == 'prepaid'

def validate_payment_terms(payment_terms: str) -> str:
    """
    Validate and normalize payment terms.
    Payment terms should be either 'collect' or 'prepaid' or 'net 30'.
    If the value is anything other than these three, it will be changed to 'collect'.
    Terms like "INVOICE DATE + 45 DAYS" or similar patterns are mapped to 'collect'.
    
    Args:
        payment_terms: The payment terms value to validate
        
    Returns:
        str: Validated payment terms ('collect' or 'prepaid' or 'net 30')
    """
    if not payment_terms:
        return 'collect'
    
    # Normalize the input by converting to lowercase and stripping whitespace
    normalized_terms = str(payment_terms).lower().strip()
    
    # Check if it's one of the valid values (exact match)
    if normalized_terms in ['collect', 'prepaid', 'net 30']:
        return normalized_terms
    
    # Check for patterns that should be mapped to 'collect':
    # - Terms containing "invoice" and "+" and "day" (e.g., "invoice date + 45 days")
    # - Terms containing "day" or "days" with numbers
    # - Terms that are date-based payment terms
    if re.search(r'invoice.*\+.*day', normalized_terms, re.IGNORECASE) or \
       re.search(r'\d+\s*day', normalized_terms, re.IGNORECASE) or \
       re.search(r'invoice.*date', normalized_terms, re.IGNORECASE):
        logger.info(f"Payment terms '{payment_terms}' contains date-based pattern. Mapping to 'collect'.")
        return 'collect'
    
        # If it's anything else, default to 'collect'
        logger.warning(f"Invalid payment terms '{payment_terms}' detected. Defaulting to 'collect'.")
        return 'collect'

def get_vendor_name_from_reference_id(vendor_reference_id: str) -> str:
    """
    Map vendor reference ID (4 char code) to vendor name.
    
    Args:
        vendor_reference_id: The vendor reference ID (e.g., "MAEU", "COSU", etc.)
        
    Returns:
        str: The corresponding vendor name, or empty string if not found
    """
    if not vendor_reference_id:
        return ""
    
    # Normalize the input by converting to uppercase and stripping whitespace
    vendor_ref = str(vendor_reference_id).upper().strip()
    
    # Mapping dictionary for vendor reference ID (SCAC code) to vendor name
    vendor_mapping = {
        "MAEU": "MAERSK",
        "COSU": "COSCO SHIPPING Lines (North America) Inc.",
        "HLCU": "Hapag-Lloyd (America) LLC.",
        "MATS": "Matson",
        "ZIMU": "ZIM",
        "MEDU": "Mediterranean Shipping Company (USA) Inc.",
        "MXNG": "MaxTrans Logistics",
        "NFBR": "NFI LOGISTICS LLC",
        "CPGP": "Container Port Group",
        "PDCM": "Einride",
        "FZMK": "FITZMARK, LLC",
        "PLOK": "Point Logistics LLC",
        "GUCI": "Gulf Coast",
        "XPON": "RXO Logistics NLM LLC",
        "MBDY": "BUDDY MOORE TRUCKING INC",
        "MMCG": "M & M CARTAGE COMPANY INC",
        "BRJF": "Brown Trucking Company",
        "TTLQ": "TAYLOR TRUCK LINE, INC.",
        "HOAL": "Hot Shot Freight",
        "MSLV": "Mesilla Valley Transportation",
        "VTVN": "V3 Transportation",
        "WSXI": "Western Express Inc",
        "ESPQ": "Eagle Steel-Collins",
        "EXDK": "Expedited Trucking Inc",
        "AXLL": "Axle Logistics LLC",
        "DAFG": "Dayton",
        "AVRT": "Averitt",
        "CNWY": "XPO",
        "ABFS": "ABF Freight",
        "CCWO": "C & C Warehouse",
        "CRCR": "Crete Carrier Corporation",
        "GFRA": "Gulf Relay LLC",
        "SJRG": "J & R Schugel",
        "NUST": "Nussbaum Trucking INC",
        "ATVH": "ATS Transportation Services",
        "PFBH": "Private Fleet Backhaul LLC",
        "PRSP": "Precision Strip Transport",
        "MSGR": "M-S LOGISTICS LLC",
        "CMFH": "CHALLENGER MOTOR FREIGHT, INC.",
        "HOSD": "HOLLAND SPECIAL DELIVERY",
        "SMMT": "SUMMITT TRUCKING, LLC",
        "WISW": "WORLDWIDE LOGISTICS INC",
        "ARVY": "ARRIVE LOGISTICS",
        "LONE": "LOAD ONE LLC",
        "TLLN": "TRANSLOOP LOGISTICS LLC",
        "CHNS": "Christenson Transportation",
        "MAWS": "Mawson & Mawson Inc",
        "MOLP": "Madison Logistics",
        "TTHY": "Transit Solutions Inc",
        "CDM4": "CARDINAL MANUFACTURING COMPANY",
        "CLIM": "CIRCLE LOGISTICS INC",
        "TQYL": "Total Quality Logistics",
        "GPAB": "GP Transco",
        "CXTB": "TFA Logistics",
    }
    
    # Return the mapped vendor name, or empty string if not found
    vendor_name = vendor_mapping.get(vendor_ref, "")
    
    if vendor_name:
        logger.info(f"Mapped vendor_reference_id '{vendor_ref}' to vendor_name '{vendor_name}'")
    else:
        logger.warning(f"No vendor name mapping found for vendor_reference_id '{vendor_ref}'")
    
    return vendor_name

def get_default_mode_from_vendor_reference_id(vendor_reference_id: str) -> str:
    """
    Map vendor reference ID (SCAC code) to default mode.
    
    Args:
        vendor_reference_id: The vendor reference ID (e.g., "MAEU", "COSU", "MXNG", etc.)
        
    Returns:
        str: The default mode ("Road", "Ocean", or "Air"), or "Ocean" as default if not found
    """
    if not vendor_reference_id:
        return "Ocean"  # Default to Ocean if vendor_reference_id is not available
    
    # Normalize the input by converting to uppercase and stripping whitespace
    vendor_ref = str(vendor_reference_id).upper().strip()
    
    # Mapping dictionary for vendor reference ID (SCAC code) to default mode
    mode_mapping = {
        # Road carriers
        "MXNG": "Road",  # MaxTrans Logistics
        "NFBR": "Road",  # NFI LOGISTICS
        "PLOK": "Road",  # Point Logistics LLC
        "PDCM": "Road",  # Einride
        "FZMK": "Road",  # FitzMark LLC
        "CPGP": "Road",  # Container Port Group
        "GUCI": "Road",  # Gulf Coast
        "MBDY": "Road",  # Buddy Moore Trucking
        "MMCG": "Road",  # M&M Cartage Co. Inc.
        "BRJF": "Road",  # Brown Trucking
        "TTLQ": "Road",  # Taylor Truck Lines (alias when LLM returns TTLQ)
        "HOAL": "Road",  # Hot Shot Freight and Services
        "MSLV": "Road",  # Mesilla Valley Transportation
        "VTVN": "Road",  # V3 Transportation
        "WSXI": "Road",  # Western Express Inc
        "ESPQ": "Road",  # Eagle Steel-Collins
        "EXDK": "Road",  # Expedited Trucking Inc
        "FXLL": "Road",  # Axle Logistics LLC
        "AXLL": "Road",  # Axle Logistics LLC (alias seen in extracted docs)
        "ATVH": "Road",  # ATS Transportation Services
        "SJRG": "Road",  # J & R Schugel
        "PFBH": "Road",  # Private Fleet Backhaul LLC
        "PRSP": "Road",  # Precision Strip Transport
        "MSGR": "Road",  # M-S LOGISTICS LLC
        "TTHY": "Road",  # Transit Solutions Inc
        "CDM4": "Road",  # Cardinal Manufacturing Company
        "CLIM": "Road",  # Circle Logistics Inc
        "TQYL": "Road",  # Total Quality Logistics
        "GPAB": "Road",  # GP Transco
        
        # Ocean carriers
        "MAEU": "Ocean",  # MAERSK
        "COSU": "Ocean",  # COSCO SHIPPING Lines (North America) Inc.
        "MEDU": "Ocean",  # Mediterranean Shipping Company (USA) Inc.
        "HLCU": "Ocean",  # Hapag-Lloyd (America) LLC.
        "ZIM": "Ocean",   # ZIM
        "MATS": "Ocean",  # Matson
        
        # Air carriers
        "XPON": "Air",    # RXO Logistics NLM LLC
    }
    
    # Return the mapped mode, or "Ocean" as default if not found
    default_mode = mode_mapping.get(vendor_ref, "Ocean")
    
    logger.info(f"Mapped vendor_reference_id '{vendor_ref}' to default mode '{default_mode}'")
    
    return default_mode

def get_delivery_type_from_vendor_reference_id(vendor_reference_id: str) -> str:
    """
    Map vendor_reference_id (SCAC) to delivery_type based on mode/category.
    Values: Dedicated, LTL, TL, DRAYAGE, FCL, CARGO
    """
    scac = (vendor_reference_id or "").upper().strip()
    if not scac:
        return ""
    
    # ROAD - DRAYAGE
    drayage_carriers = {"MXNG", "PDCM", "CPGP", "PLOK", "GUCI", "NFBR"}
    if scac in drayage_carriers:
        return "DRAYAGE"
    
    # OCEAN - FCL
    ocean_fcl = {"MAEU", "COSU", "HLCU", "MSCU", "ZIMU", "MATS"}
    if scac in ocean_fcl:
        return "FCL"
    
    # AIR - CARGO
    air_cargo = {"XPON"}
    if scac in air_cargo:
        return "CARGO"
    
    # ROAD - TL (per provided TL column list; exclude XPON)
    # Note: DRAYAGE mapping (above) still takes precedence for the DRAYAGE SCACs.
    road_tl = {
        "MMCG", "MBDY", "CCWO", "HOAL", "WSXI", "EXDK",
        "FZMK", "AXLL", "MXNG", "TTLQ", "MSLV", "BRJF",
        "CLIM", "ESPQ", "VTVN", "NUST", "CRCR", "GFRA",
        "SJRG", "NFTR", "TXFV", "WISW", "HOSD", "ATVH",
        "PFBH", "PRSP", "MSGR", "SMMT", "ARVY", "XPOL", "AQSM", "LONE",
        "TLLN", "CMFH", "ECHS", "TTHY", "TQYL", "GPAB", "CXTB"
    }
    if scac in road_tl:
        return "TL"
    
    # ROAD - LTL (per image: AVRT, ABFS, CNWY, DAFG)
    road_ltl = {"AVRT", "ABFS", "CNWY", "DAFG","CDM4"}
    if scac in road_ltl:
        return "LTL"
    
    # ROAD Dedicated (placeholder, extend as needed)
    road_dedicated = set()
    if scac in road_dedicated:
        return "Dedicated"
    
    return ""
def generate_shipment_number_fallback(invoice_number):
    """
    Generate a fallback shipment number using 'P' + 9 characters of invoice number.
    - If invoice ID > 9 characters: Take last 9 characters
    - If invoice ID < 9 characters: Add leading zeros to make it 9 characters
    - If invoice_number is None or empty, returns 'P-UNKNOWN'.
    - Preserves ALL characters (letters, numbers, symbols, spaces, etc.)
    """
    if not invoice_number:
        return "P-UNKNOWN"
    
    # Convert to string and keep ALL characters
    invoice_str = str(invoice_number)
    
    if not invoice_str:
        return "P-UNKNOWN"
    
    # If more than 9 characters, take last 9
    if len(invoice_str) > 9:
        last_9_chars = invoice_str[-9:]
    # If less than 9 characters, pad with leading zeros
    elif len(invoice_str) < 9:
        last_9_chars = invoice_str.zfill(9)
    # If exactly 9 characters, use as is
    else:
        last_9_chars = invoice_str
    
    return f"P{last_9_chars}"

def is_valid_10_digit_integer(shipment_number):
    """
    Check if shipment_number is a valid 10-digit integer (only digits, no alphabets or symbols).
    
    Args:
        shipment_number: The shipment number to validate
        
    Returns:
        bool: True if shipment_number is exactly 10 digits (only numeric characters), False otherwise
    """
    if not shipment_number:
        return False
    
    # Convert to string and strip whitespace
    shipment_str = str(shipment_number).strip()
    
    # Check if it's exactly 10 characters and all are digits
    if len(shipment_str) == 10 and shipment_str.isdigit():
        return True
    
    return False

def consolidate_charges_in_shipment(shipment: Dict[str, Any]) -> Dict[str, Any]:
    """
    Consolidate charges within a shipment by summing amounts for charges with the same name.
    Handles negative charges correctly (subtracts them).
    
    Args:
        shipment: A shipment dictionary with a 'charges' list (flattened format)
        
    Returns:
        Dict: Updated shipment with consolidated charges
    """
    if not isinstance(shipment, dict) or "charges" not in shipment:
        return shipment
    
    charges = shipment.get("charges", [])
    if not isinstance(charges, list) or len(charges) == 0:
        return shipment
    
    # Dictionary to accumulate charges by name
    charge_accumulator = {}
    
    for charge in charges:
        if not isinstance(charge, dict):
            continue
        
        charge_name = charge.get("charge_name", "").strip()
        charge_amount = charge.get("charge_gross_amount", 0)
        
        # Handle different types for charge_amount
        try:
            if isinstance(charge_amount, str):
                charge_amount = float(charge_amount)
            elif charge_amount is None:
                charge_amount = 0.0
            else:
                charge_amount = float(charge_amount)
        except (ValueError, TypeError):
            logger.warning(f"Invalid charge_gross_amount '{charge_amount}' for charge '{charge_name}', treating as 0")
            charge_amount = 0.0
        
        # If charge_name is empty, skip consolidation
        if not charge_name:
            continue
        
        # Accumulate charges by name
        if charge_name in charge_accumulator:
            # Add to existing charge (handles negative values correctly)
            # Preserve tariff fields from first occurrence, only update amount
            old_amount = charge_accumulator[charge_name]["charge_gross_amount"]
            charge_accumulator[charge_name]["charge_gross_amount"] = round(old_amount + charge_amount, 2)
            new_amount = charge_accumulator[charge_name]["charge_gross_amount"]
            logger.info(f"Consolidating charge '{charge_name}': adding {charge_amount} to existing {old_amount} = {new_amount}")
        else:
            # Create new entry, preserving all fields from the first occurrence including tariff fields
            # Ensure all tariff fields are present with defaults if missing
            # Get charge_code, and if empty or missing, use charge_name
            charge_code = charge.get("charge_code", "").strip() if charge.get("charge_code") else ""
            if not charge_code:
                charge_code = charge_name
                logger.info(f"Charge code missing for charge '{charge_name}', setting charge_code to charge_name")
            
            # Get tariff_qty and ensure it's a number
            tariff_qty = charge.get("tariff_qty")
            if tariff_qty is None or tariff_qty == "":
                tariff_qty = 0
            else:
                try:
                    if isinstance(tariff_qty, str):
                        tariff_qty_str = tariff_qty.strip()
                        if tariff_qty_str == "" or tariff_qty_str.lower() in ["null", "none", "n/a"]:
                            tariff_qty = 0
                        else:
                            tariff_qty_float = float(tariff_qty_str)
                            tariff_qty = int(tariff_qty_float) if tariff_qty_float.is_integer() else tariff_qty_float
                except (ValueError, TypeError, AttributeError):
                    logger.warning(f"Failed to convert tariff_qty '{tariff_qty}' to number, setting to 0")
                    tariff_qty = 0
            
            charge_accumulator[charge_name] = {
                "charge_name": charge_name,
                "charge_gross_amount": charge_amount,
                "charge_code": charge_code,
                "currency": charge.get("currency", ""),
                "tariff_rate": charge.get("tariff_rate") if charge.get("tariff_rate") is not None else 0,
                "tariff_qty": tariff_qty,
                "tariff_uom": charge.get("tariff_uom") if charge.get("tariff_uom") is not None else "",
                "tariff_description": charge.get("tariff_description") if charge.get("tariff_description") is not None else ""
            }
            logger.info(f"Starting consolidation for charge '{charge_name}' with amount {charge_amount}")
    
    # Replace charges with consolidated list
    # Ensure all charges have all required fields including tariff fields
    final_charges = []
    for charge in charge_accumulator.values():
        if isinstance(charge, dict):
            # Ensure charge_code is set - if missing or empty, use charge_name
            charge_name = charge.get("charge_name", "").strip()
            charge_code = charge.get("charge_code", "").strip() if charge.get("charge_code") else ""
            if not charge_code and charge_name:
                charge["charge_code"] = charge_name
                logger.info(f"Charge code missing for charge '{charge_name}' in final_charges, setting charge_code to charge_name")
            
            # Ensure all tariff fields are present and properly typed
            if "tariff_rate" not in charge or charge["tariff_rate"] is None:
                charge["tariff_rate"] = 0
            if "tariff_qty" not in charge or charge["tariff_qty"] is None:
                charge["tariff_qty"] = 0
            
            # Convert tariff_qty to number if it's a string or empty
            try:
                if isinstance(charge["tariff_qty"], str):
                    # Remove whitespace and convert empty string to 0
                    tariff_qty_str = charge["tariff_qty"].strip()
                    if tariff_qty_str == "" or tariff_qty_str.lower() in ["null", "none", "n/a"]:
                        charge["tariff_qty"] = 0
                    else:
                        # Try to convert to float first, then int if it's a whole number
                        tariff_qty_float = float(tariff_qty_str)
                        charge["tariff_qty"] = int(tariff_qty_float) if tariff_qty_float.is_integer() else tariff_qty_float
                elif charge["tariff_qty"] == "":
                    charge["tariff_qty"] = 0
            except (ValueError, TypeError, AttributeError):
                logger.warning(f"Failed to convert tariff_qty '{charge.get('tariff_qty')}' to number, setting to 0")
                charge["tariff_qty"] = 0
            
            if "tariff_uom" not in charge or charge["tariff_uom"] is None:
                charge["tariff_uom"] = ""
            if "tariff_description" not in charge or charge["tariff_description"] is None:
                charge["tariff_description"] = ""
            final_charges.append(charge)
    
    shipment["charges"] = final_charges
    logger.info(f"Consolidated {len(charges)} charges into {len(shipment['charges'])} unique charges")
    
    return shipment

def consolidate_charges_in_shipment_structured(shipment: Dict[str, Any]) -> Dict[str, Any]:
    """
    Consolidate charges within a shipment by summing amounts for charges with the same name.
    Handles negative charges correctly (subtracts them).
    Works with structured format (value/confidence/explanation).
    
    Args:
        shipment: A shipment dictionary with a 'charges' list (structured format)
        
    Returns:
        Dict: Updated shipment with consolidated charges
    """
    if not isinstance(shipment, dict) or "charges" not in shipment:
        return shipment
    
    charges = shipment.get("charges", [])
    if not isinstance(charges, list) or len(charges) == 0:
        return shipment
    
    # Dictionary to accumulate charges by name
    charge_accumulator = {}
    
    for charge in charges:
        if not isinstance(charge, dict):
            continue
        
        # Extract charge_name from structured format
        charge_name_field = charge.get("charge_name", {})
        if isinstance(charge_name_field, dict) and "value" in charge_name_field:
            charge_name = str(charge_name_field.get("value", "")).strip()
        else:
            charge_name = str(charge_name_field).strip()
        
        # Extract charge_gross_amount from structured format
        charge_amount_field = charge.get("charge_gross_amount", {})
        if isinstance(charge_amount_field, dict) and "value" in charge_amount_field:
            charge_amount = charge_amount_field.get("value")
        else:
            charge_amount = charge_amount_field
        
        # Handle different types for charge_amount
        try:
            if isinstance(charge_amount, str):
                charge_amount = float(charge_amount)
            elif charge_amount is None:
                charge_amount = 0.0
            else:
                charge_amount = float(charge_amount)
        except (ValueError, TypeError):
            logger.warning(f"Invalid charge_gross_amount '{charge_amount}' for charge '{charge_name}', treating as 0")
            charge_amount = 0.0
        
        # If charge_name is empty, skip consolidation
        if not charge_name:
            continue
        
        # Extract other fields for preservation
        charge_code_field = charge.get("charge_code", {})
        if isinstance(charge_code_field, dict) and "value" in charge_code_field:
            charge_code = str(charge_code_field.get("value", "")).strip()
        else:
            charge_code = str(charge_code_field).strip() if charge_code_field else ""
        
        # If charge_code is empty or missing, use charge_name
        if not charge_code:
            charge_code = charge_name
            logger.info(f"Charge code missing for charge '{charge_name}', setting charge_code to charge_name")
        
        currency_field = charge.get("currency", {})
        if isinstance(currency_field, dict) and "value" in currency_field:
            currency = currency_field.get("value", "")
        else:
            currency = str(currency_field) if currency_field else ""
        
        # Accumulate charges by name
        if charge_name in charge_accumulator:
            # Add to existing charge (handles negative values correctly)
            current_amount = charge_accumulator[charge_name]["charge_gross_amount"]["value"]
            new_amount = current_amount + charge_amount
            charge_accumulator[charge_name]["charge_gross_amount"]["value"] = new_amount
            logger.info(f"Consolidating charge '{charge_name}': adding {charge_amount} to existing {current_amount} = {new_amount}")
        else:
            # Create new entry in structured format
            charge_accumulator[charge_name] = {
                "charge_name": {"value": charge_name, "explanation": "Consolidated charge", "confidence": 1.0},
                "charge_gross_amount": {"value": charge_amount, "explanation": "Consolidated amount", "confidence": 1.0},
                "charge_code": {"value": charge_code, "explanation": "From first occurrence or set to charge_name if missing", "confidence": 1.0},
                "currency": {"value": currency, "explanation": "From first occurrence", "confidence": 1.0} if currency else {}
            }
            logger.info(f"Starting consolidation for charge '{charge_name}' with amount {charge_amount}")
    
    # Replace charges with consolidated list
    shipment["charges"] = list(charge_accumulator.values())
    logger.info(f"Consolidated {len(charges)} charges into {len(shipment['charges'])} unique charges (structured format)")
    
    return shipment

def validate_payment_due_date(extracted_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate payment due date against invoice date.
    Simplified version - only checks that payment due date is after invoice date.
    
    Args:
        extracted_info: The extracted invoice information
        
    Returns:
        Dict containing validation results with 'is_valid' boolean and 'errors' list
    """
    validation_result = {
        'is_valid': True,
        'errors': []
    }
    
    try:
        # Extract dates from extracted_info
        invoice_date_str = None
        payment_due_date_str = None
        
        # Get invoice_date
        if "invoice_date" in extracted_info:
            invoice_date_field = extracted_info["invoice_date"]
            if isinstance(invoice_date_field, dict) and "value" in invoice_date_field:
                invoice_date_str = invoice_date_field["value"]
            elif isinstance(invoice_date_field, str):
                invoice_date_str = invoice_date_field
            elif isinstance(invoice_date_field, (int, float)):
                invoice_date_str = invoice_date_field
        
        # Get payment_due_date
        if "payment_due_date" in extracted_info:
            payment_due_date_field = extracted_info["payment_due_date"]
            if isinstance(payment_due_date_field, dict) and "value" in payment_due_date_field:
                payment_due_date_str = payment_due_date_field["value"]
            elif isinstance(payment_due_date_field, str):
                payment_due_date_str = payment_due_date_field
            elif isinstance(payment_due_date_field, (int, float)):
                payment_due_date_str = payment_due_date_field
        
        # Check if both dates are available
        if not invoice_date_str or not payment_due_date_str:
            validation_result['is_valid'] = False
            validation_result['errors'].append({
                'type': 'missing_dates',
                'message': 'Invoice date or payment due date is missing',
                'invoice_date': invoice_date_str,
                'payment_due_date': payment_due_date_str
            })
            return validation_result
        
        # Parse dates - handle multiple formats
        from datetime import datetime, timedelta
        
        def parse_date(date_str):
            """Parse date string in various formats or integer timestamp"""
            if not date_str:
                return None
            
            # Handle integer timestamps (Unix timestamp)
            if isinstance(date_str, (int, float)):
                try:
                    # Check if it's milliseconds (13 digits) or seconds (10 digits)
                    if date_str > 1e12:  # Likely milliseconds
                        return datetime.fromtimestamp(date_str / 1000)
                    else:  # Likely seconds
                        return datetime.fromtimestamp(date_str)
                except (ValueError, OSError) as e:
                    logger.warning(f"Error converting timestamp {date_str} to date in validation: {e}")
                    return None
            
            # Convert to string for parsing
            date_str = str(date_str).strip()
            
            # Common date formats to try
            date_formats = [
                '%d-%b-%Y',  # DD-MMM-YYYY (e.g., 15-Jan-2024)
                '%d-%m-%Y',  # DD-MM-YYYY
                '%Y-%m-%d',  # YYYY-MM-DD
                '%m/%d/%Y',  # MM/DD/YYYY
                '%d/%m/%Y',  # DD/MM/YYYY
                '%Y/%m/%d',  # YYYY/MM/DD
            ]
            
            for fmt in date_formats:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
            
            # If no format matches, try to parse with dateutil
            try:
                from dateutil import parser
                return parser.parse(date_str)
            except:
                return None
        
        invoice_date = parse_date(invoice_date_str)
        payment_due_date = parse_date(payment_due_date_str)
        
        if not invoice_date or not payment_due_date:
            validation_result['is_valid'] = False
            validation_result['errors'].append({
                'type': 'date_parsing_error',
                'message': 'Unable to parse invoice date or payment due date',
                'invoice_date': invoice_date_str,
                'payment_due_date': payment_due_date_str
            })
            return validation_result
        
        # Rule: Payment due date should be greater than invoice date
        if payment_due_date <= invoice_date:
                validation_result['is_valid'] = False
                validation_result['errors'].append({
                'type': 'due_date_before_invoice_date',
                'message': 'Payment due date should be after invoice date',
                    'invoice_date': invoice_date_str,
                    'payment_due_date': payment_due_date_str
                })
                return validation_result
        
        logger.info("Payment due date validation passed")
        return validation_result
        
    except Exception as e:
        logger.error(f"Error in payment due date validation: {str(e)}")
        validation_result['is_valid'] = False
        validation_result['errors'].append({
            'type': 'validation_error',
            'message': f'Error during payment due date validation: {str(e)}',
            'invoice_date': invoice_date_str if 'invoice_date_str' in locals() else None,
            'payment_due_date': payment_due_date_str if 'payment_due_date_str' in locals() else None
        })
        return validation_result

# ---------- DATABASE FUNCTIONS ----------
def update_attachment_status(
    dynamodb_client,
    table_name,
    email_id,
    attachment_id,
    status,
    error=None,
    output_path=None,
    is_create=False,
    mode=None,
    invoice_number=None,
    missing_critical_field=None,
    textract_failed=None,
    classification_failed=None,
    extraction_failed=None,
    format_failed=None,
    timeout_occurred=None,
    missing_fields=None,  # Array of missing field names
    confidence_score=None,  # Overall confidence score
    extracted_fields=None,  # Parameter to store extracted field values (including confidence)
    api_response=None,  # Parameter to store API response details
    api_payload=None,  # Parameter to store API request payload
    payment_terms=None,  # Parameter to store payment terms
    sender_email=None,  # Parameter to store sender email address
    textract_model_result=None,  # Carrier result from Textract model
    image_model_result=None,  # Carrier result from image model
):
    """Update/create the status of an attachment in DynamoDB, setting correct timestamps and failure flags."""
    try:
        now = datetime.utcnow()
        now_ms = int(now.timestamp() * 1000)
        now_iso = now.isoformat()  # No trailing Z; always UTC

        # If sender_email not provided, try to get it from metadata
        if sender_email is None:
            try:
                response = dynamodb_client.get_item(
                    TableName=table_name,
                    Key={
                        'pk': {'S': f"EMAIL#{email_id}"},
                        'sk': {'S': 'METADATA'}
                    }
                )
                if 'Item' in response:
                    metadata = response['Item'].get('metadata', {}).get('M', {})
                    sender_email = metadata.get('sender', {}).get('S', '')
            except Exception as e:
                logger.warning(f"Failed to retrieve sender email from metadata: {str(e)}")
                sender_email = ''

        # Extract sender email name (part before @)
        sender_email_name = ''
        if sender_email and '@' in sender_email:
            sender_email_name = sender_email.split('@')[0]

        update_expr = 'SET #status = :status, updated_at = :updated_at, updated_at_iso = :updated_at_iso'
        expr_attrs = {'#status': 'status'}
        expr_values = {
            ':status': {'S': status},
            ':updated_at': {'N': str(now_ms)},
            ':updated_at_iso': {'S': now_iso}
        }
        
        # Add sender_email_name (name part before @) and full sender_email if available
        if sender_email_name:
            update_expr += ', sender_email_name = :sender_email_name'
            expr_values[':sender_email_name'] = {'S': sender_email_name}
        if sender_email:
            update_expr += ', sender_email = :sender_email'
            expr_values[':sender_email'] = {'S': sender_email}

        # Only set created_at if is_create is True (i.e., this is a new item)
        if is_create:
            update_expr += ', created_at = :created_at, created_at_iso = :created_at_iso'
            expr_values[':created_at'] = {'N': str(now_ms)}
            expr_values[':created_at_iso'] = {'S': now_iso}

        # Removed carrier_name - no longer used

        # Add overall confidence score if provided
        if confidence_score is not None:
            update_expr += ', confidence_score = :confidence_score'
            expr_values[':confidence_score'] = {'N': str(confidence_score)}
            
        # Add extracted fields with their values including confidence and explanation
        if extracted_fields is not None and isinstance(extracted_fields, list):
            update_expr += ', extracted_fields = :extracted_fields'
            # Convert list to DynamoDB list format
            extracted_fields_list = {'L': []}
            for field in extracted_fields:
                field_map = {'M': {}}
                for key, value in field.items():
                    if key == "confidence":
                        field_map['M'][key] = {'N': str(value)}
                    else:
                        field_map['M'][key] = {'S': str(value)}
                
                # Add explanation if it exists
                if "explanation" in field:
                    field_map['M']["explanation"] = {'S': str(field["explanation"])}
                
                extracted_fields_list['L'].append(field_map)
            expr_values[':extracted_fields'] = extracted_fields_list
            
        # Add API response details if provided
        if api_response is not None:
            update_expr += ', api_response = :api_response'
            api_response_map = {'M': {}}
            
            # Add status code
            if 'status_code' in api_response:
                api_response_map['M']['status_code'] = {'N': str(api_response['status_code'])}
                
            # Add success flag
            if 'success' in api_response:
                api_response_map['M']['success'] = {'BOOL': api_response['success']}
                
            # Add timestamp
            if 'timestamp' in api_response:
                api_response_map['M']['timestamp'] = {'S': api_response['timestamp']}
                
            # Add response body if available (as a string)
            if 'body' in api_response:
                api_response_map['M']['body'] = {'S': str(api_response['body'])}
                
            expr_values[':api_response'] = api_response_map
        
        # Add API payload details if provided
        if api_payload is not None:
            update_expr += ', api_payload = :api_payload'
            # Store as JSON string for easy retrieval
            expr_values[':api_payload'] = {'S': json.dumps(api_payload)}

        if textract_model_result is not None:
            update_expr += ', textract_model_result = :textract_model_result'
            expr_values[':textract_model_result'] = {'S': json.dumps(textract_model_result)}

        if image_model_result is not None:
            update_expr += ', image_model_result = :image_model_result'
            expr_values[':image_model_result'] = {'S': json.dumps(image_model_result)}

        # Add mode if provided - use expression attribute name since "mode" is a reserved keyword
        if mode:
            update_expr += ', #trans_mode = :trans_mode'
            expr_attrs['#trans_mode'] = 'mode'
            expr_values[':trans_mode'] = {'S': mode}

        # Add invoice_number if provided
        if invoice_number:
            update_expr += ', invoice_number = :invoice_number'
            expr_values[':invoice_number'] = {'S': invoice_number}

        # Add specific failure flags if provided
        if missing_critical_field is not None:
            update_expr += ', missing_critical_field = :missing_critical_field'
            expr_values[':missing_critical_field'] = {'N': str(missing_critical_field)}

        # Add array of missing fields
        if missing_fields is not None:
            update_expr += ', missing_fields = :missing_fields'
            # Convert list to DynamoDB list format
            if isinstance(missing_fields, list):
                expr_values[':missing_fields'] = {'L': [{'S': str(field)} for field in missing_fields]}
            else:
                # If missing_fields is not a list, default to empty list
                expr_values[':missing_fields'] = {'L': []}

        if textract_failed is not None:
            update_expr += ', textract_failed = :textract_failed'
            expr_values[':textract_failed'] = {'N': str(textract_failed)}

        if classification_failed is not None:
            update_expr += ', classification_failed = :classification_failed'
            expr_values[':classification_failed'] = {'N': str(classification_failed)}

        if extraction_failed is not None:
            update_expr += ', extraction_failed = :extraction_failed'
            expr_values[':extraction_failed'] = {'N': str(extraction_failed)}

        if format_failed is not None:
            update_expr += ', format_failed = :format_failed'
            expr_values[':format_failed'] = {'N': str(format_failed)}
            
        if timeout_occurred is not None:
            update_expr += ', timeout_occurred = :timeout_occurred'
            expr_values[':timeout_occurred'] = {'N': str(timeout_occurred)}

        # Add payment_terms if provided
        if payment_terms is not None:
            update_expr += ', payment_terms = :payment_terms'
            expr_values[':payment_terms'] = {'S': str(payment_terms)}

        if error:
            update_expr += ', #err = :error'
            expr_attrs['#err'] = 'error'
            expr_values[':error'] = {'M': {
                'message': {'S': error.get('message', '')},
                'error_code': {'S': error.get('error_code', '')},
                'timestamp': {'S': now_iso}
            }}

        if output_path:
            update_expr += ', output_path = :output_path, completed_at = :completed_at, completed_at_iso = :completed_at_iso'
            expr_values[':output_path'] = {'S': output_path}
            expr_values[':completed_at'] = {'N': str(now_ms)}
            expr_values[':completed_at_iso'] = {'S': now_iso}

        logger.info(f"Updating DynamoDB item for email_id: {email_id}, attachment_id: {attachment_id}, status: {status}, is_create: {is_create}")
        response = dynamodb_client.update_item(
            TableName=table_name,
            Key={
                'pk': {'S': f"EMAIL#{email_id}"},
                'sk': {'S': f"ATTACHMENT#{attachment_id}"}
            },
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attrs,
            ExpressionAttributeValues=expr_values,
            ReturnValues='ALL_NEW'
        )
        logger.info(f"DynamoDB update successful for email_id: {email_id}, attachment_id: {attachment_id}.")

        # Update the parent email job counters (only on state transitions)
        counter_update = {
            'PROCESSING': 'processing_attachments',
            'COMPLETED': 'completed_attachments',
            'FAILED': 'failed_attachments',
            'UNCLASSIFIED': 'failed_attachments'
        }

        if status in counter_update:
            counter_field = counter_update[status]
            counter_response = dynamodb_client.update_item(
                TableName=table_name,
                Key={
                    'pk': {'S': f"EMAIL#{email_id}"},
                    'sk': {'S': 'METADATA'}
                },
                UpdateExpression=f'ADD {counter_field} :inc',
                ExpressionAttributeValues={
                    ':inc': {'N': '1'}
                },
                ReturnValues='ALL_NEW'
            )
            logger.info(f"Counter update successful for email_id: {email_id}.")

        return True
    except Exception as e:
        logger.error(f"Failed to update attachment status: {str(e)}", exc_info=True)
        return False

def invoke_validator_lambda(lambda_arn, extracted_info, validation_method, input_type):
    """Invoke a validator Lambda function to validate the extracted information."""
    lambda_client = boto3.client('lambda')
    payload = {
        "input_type": input_type,
        "validation_method": validation_method,
        "data": {
            "Invoice": extracted_info
        }
    }
    response = lambda_client.invoke(
        FunctionName=lambda_arn,
        InvocationType='RequestResponse',
        Payload=json.dumps(payload)
    )
    # Read and decode the response payload
    response_payload = response['Payload'].read().decode('utf-8')
    # Parse the JSON response
    result = json.loads(response_payload)
    return result

def extract_validation_errors(response_json, validation_method, input_type):
    """
    Extracts errors from the Lambda response for both 'llm' and 'generic' validation.
    Handles both dict and list responses.
    Only proceeds with validation if input_type is 'invoice'.
    """
    logger.debug(f"Extracting validation errors. Input type: {input_type}, Validation method: {validation_method}")
    logger.debug(f"Response JSON type: {type(response_json)}")
    
    # If input_type is not 'invoice', do not proceed
    if input_type != 'invoice':
        logger.debug("Input type is not 'invoice', returning empty dict")
        return {"required_field_errors": [], "other_field_errors": []}
    
    # Initialize the result structure
    result = {"required_field_errors": [], "other_field_errors": []}
    
    try:
        # If the response is a dict with a 'body' key (Lambda proxy integration format)
        if isinstance(response_json, dict) and 'body' in response_json:
            body_str = response_json.get('body', '')
            if isinstance(body_str, str):
                body_json = json.loads(body_str)
            else:
                body_json = body_str  # Already parsed JSON
                
            # Extract errors based on validation method
            if validation_method == "llm":
                error_str = body_json.get('error', '[]')
                try:
                    if isinstance(error_str, str):
                        errors = json.loads(error_str)
                    else:
                        errors = error_str  # Already parsed JSON
                except Exception as e:
                    logger.warning(f"Failed to parse error string: {e}")
                    errors = []
                    
                # Assign errors to the appropriate categories
                if isinstance(errors, dict):
                    result["required_field_errors"] = errors.get("required_field_errors", [])
                    result["other_field_errors"] = errors.get("other_field_errors", [])
                elif isinstance(errors, list):
                    # Assume all are required field errors if not categorized
                    result["required_field_errors"] = errors
            else:  # generic validation
                # Extract errors from the body
                if "errors" in body_json and isinstance(body_json["errors"], dict):
                    result["required_field_errors"] = body_json["errors"].get("required_field_errors", [])
                    result["other_field_errors"] = body_json["errors"].get("other_field_errors", [])
                else:
                    # If errors is not a dict with categories, check if it's a list
                    errors = body_json.get("errors", [])
                    if isinstance(errors, list):
                        result["required_field_errors"] = errors
        
        # If the response is already a dict with the expected structure
        elif isinstance(response_json, dict) and ("required_field_errors" in response_json or "other_field_errors" in response_json):
            result["required_field_errors"] = response_json.get("required_field_errors", [])
            result["other_field_errors"] = response_json.get("other_field_errors", [])
            
        # If the response is a list, assume they are all required field errors
        elif isinstance(response_json, list):
            result["required_field_errors"] = response_json
            
        logger.debug(f"Extracted validation errors:\n{_format_json_for_log(result)}")
        return result
        
    except Exception as e:
        logger.error(f"Error extracting validation errors: {e}", exc_info=True)
        return {"required_field_errors": [], "other_field_errors": []}

# Format output JSON exactly as required
def format_output_json(extracted_info, email_details, confidence_score=None):
    """
    Format the output JSON to match the required structure with clean values.
    Includes average confidence score if provided.
    
    Args:
        extracted_info: The structured output from Claude
        email_details: Details about the original email
        confidence_score: Optional average confidence score
        
    Returns:
        dict: Properly formatted output JSON
    """
    # First flatten the structured output to get simple values
    flattened_info = flatten_structured_output(extracted_info)
    
    # Keep only the essential email details
    email_output = {
        "to": email_details.get("to", ""),
        "subject": email_details.get("subject", ""),
        "message_id": email_details.get("message_id", ""),
        "original_body": email_details.get("original_body", ""),
        "filename": email_details.get("filename", "")
    }
    
    output = {
        "extracted_info": flattened_info,
        "email_details": email_output,
        "processing_timestamp": time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    }
    
    # Add confidence score if available
    if confidence_score is not None:
        output["average_confidence"] = confidence_score
        
    return output

# ---------- API HANDLER CLASS ----------
class APIHandler:
    def create_invoice_payload(self, extracted_info: Dict[str, Any], email_details: Dict[str, Any],
                              original_input_key: str, original_input_bucket: str,
                              validation_errors: Dict[str, Any],
                              transaction_id: str = "") -> Dict[str, Any]:
        """Creates a structured payload for invoice data submission to an external API."""
        # Extract custom fields from extracted_info
        custom_additional_info = extracted_info.pop("additional_info", None)
        custom_customer = extracted_info.pop("customer", None)
        
        # Get vendor name from extracted info if available
        # Note: extracted_info is actually flattened_data at this point
        vendor_reference_id = extracted_info.get("vendor_reference_id", "")
        if not vendor_reference_id or (isinstance(vendor_reference_id, str) and vendor_reference_id.strip() == ""):
            vendor_name = ""
        else:
            # Map vendor_reference_id to vendor name using the mapping function
            vendor_name = get_vendor_name_from_reference_id(vendor_reference_id)
        
        # Get existing custom fields from LLM extraction (if any)
        existing_custom = extracted_info.get("custom", {})
        if not isinstance(existing_custom, dict):
            existing_custom = {}
        # SCAC belongs in vendor_reference_id only; strip if model echoed it into custom
        existing_custom.pop("vendor_id", None)
        
        # Build custom object with only allowed fields
        # Use existing values if present and not empty, otherwise use defaults (client_id always defaults to 23, not from environment or LLM extraction)
        # IMPORTANT: vendor_name is always determined by the mapping function based on vendor_reference_id, not from LLM extraction
        
        # Extract invoice_source_name and invoice_destination_name from LLM-extracted custom_fields (raw values as per prompt, no canonical processing)
        invoice_source_name = ""
        invoice_destination_name = ""
        if "custom_fields" in extracted_info and isinstance(extracted_info["custom_fields"], dict):
            custom_fields = extracted_info["custom_fields"]
            
            # Extract invoice_source_name
            if "invoice_source_name" in custom_fields:
                field = custom_fields["invoice_source_name"]
                if isinstance(field, dict) and "value" in field:
                    invoice_source_name = str(field.get("value", "")).strip()
                else:
                    invoice_source_name = str(field).strip() if field else ""
            
            # Extract invoice_destination_name
            if "invoice_destination_name" in custom_fields:
                field = custom_fields["invoice_destination_name"]
                if isinstance(field, dict) and "value" in field:
                    invoice_destination_name = str(field.get("value", "")).strip()
                else:
                    invoice_destination_name = str(field).strip() if field else ""
        
        extracted_info["custom"] = {
            "source_type": existing_custom.get("source_type") if existing_custom.get("source_type") else "email",
            "shipper_email": existing_custom.get("shipper_email") if existing_custom.get("shipper_email") else FROM_EMAIL,
            "sender_email": existing_custom.get("sender_email") if existing_custom.get("sender_email") else email_details.get('to', ''),
            "vendor_name": vendor_name,  # Always use mapped vendor_name from vendor_reference_id, ignore LLM-extracted value
            "client_id": CLIENT_ID,  # Always set from CLIENT_ID env var, ignore any LLM-extracted value
            # Always use system-derived original S3 path for attachments to avoid LLM hallucinations
            "attachment_key": original_input_key,
            "attachment_bucket": original_input_bucket,
            "transaction_id": transaction_id,
            # New fields: raw source and destination names as extracted from invoice by LLM (no canonical processing, from custom_fields)
            "invoice_source_name": invoice_source_name,
            "invoice_destination_name": invoice_destination_name,
        }
        
        # Add assessable_value at root level if not present (handle both string and structured format)
        # IMPORTANT: Do not derive assessable_value from invoice totals/net values in processor fallback.
        # If not extracted, keep it as mandatory default 0.0.
        assessable_value = extracted_info.get("assessable_value", "")
        if isinstance(assessable_value, dict) and "value" in assessable_value:
            assessable_value = assessable_value["value"]

        if not assessable_value or (isinstance(assessable_value, str) and assessable_value.strip() == ""):
            extracted_info["assessable_value"] = 0.0
        else:
            # Convert assessable_value to number (not string)
            try:
                if isinstance(assessable_value, str):
                    extracted_info["assessable_value"] = float(assessable_value.replace(",", ""))
                else:
                    extracted_info["assessable_value"] = float(assessable_value)
            except (ValueError, TypeError):
                extracted_info["assessable_value"] = 0.0
        
        # Process shipments to add required fields
        if "shipments" in extracted_info and isinstance(extracted_info["shipments"], list):
            # Get vendor_reference_id to determine SCAC code for ocean carriers
            vendor_ref_id = extracted_info.get("vendor_reference_id", "")
            vendor_ref_upper = str(vendor_ref_id).upper().strip() if vendor_ref_id else ""
            
            # Define ocean carrier SCAC codes
            ocean_carrier_scac = {
                "MAEU": "MAEU",  # Maersk
                "COSU": "COSU",  # Cosco
                "MEDU": "MEDU",  # MSC
                "HLCU": "HLCU",  # Hapag-Lloyd
                "ZIM": "ZIMU",   # ZIM
                "MATS": "MATS"   # Matson
            }
            
            # Get SCAC code if this is an ocean carrier
            scac_code = ocean_carrier_scac.get(vendor_ref_upper, None)
            
            for shipment in extracted_info["shipments"]:
                if isinstance(shipment, dict):
                    # Add SCAC prefix to shipment_number for ocean carriers if not already present
                    if scac_code:
                        shipment_number = shipment.get("shipment_number", "")
                        # Handle both string and structured format
                        if isinstance(shipment_number, dict) and "value" in shipment_number:
                            shipment_number_value = shipment_number.get("value", "")
                        else:
                            shipment_number_value = str(shipment_number) if shipment_number else ""
                        
                        # Check if shipment_number already starts with SCAC code
                        shipment_number_upper = shipment_number_value.upper().strip()
                        if shipment_number_value and not shipment_number_upper.startswith(scac_code):
                            # Add SCAC prefix
                            shipment_number_with_prefix = f"{scac_code}{shipment_number_value}"
                            # Update shipment_number (handle both formats)
                            if isinstance(shipment_number, dict):
                                shipment["shipment_number"]["value"] = shipment_number_with_prefix
                            else:
                                shipment["shipment_number"] = shipment_number_with_prefix
                            logger.info(f"Added SCAC prefix '{scac_code}' to shipment_number: '{shipment_number_value}' -> '{shipment_number_with_prefix}'")
                    
                    # Add shipment_tracking_number if not present (handle both string and structured format)
                    shipment_tracking_number = shipment.get("shipment_tracking_number", "")
                    if not shipment_tracking_number or (isinstance(shipment_tracking_number, str) and shipment_tracking_number.strip() == ""):
                        shipment["shipment_tracking_number"] = "0"
                    
                    # Process containers in shipment - handle based on mode
                    shipment_mode = shipment.get("mode", "").upper()
                    is_air_or_drayage = shipment_mode in ["AIR", "DRAYAGE"]
                    
                    if "container" in shipment and isinstance(shipment["container"], list):
                        for container in shipment["container"]:
                            if isinstance(container, dict):
                                # Handle container_weight based on mode
                                container_weight = container.get("container_weight")
                                if container_weight is None or (isinstance(container_weight, (int, float)) and container_weight == 0 and is_air_or_drayage):
                                    container["container_weight"] = 0  # Default to 0 for Air/drayage
                                
                                # Handle container_weight_uom based on mode and carrier
                                container_weight_uom = container.get("container_weight_uom", "")
                                if not container_weight_uom or (isinstance(container_weight_uom, str) and container_weight_uom.strip() == ""):
                                    # Get vendor_reference_id to determine default
                                    vendor_ref_id = extracted_info.get("vendor_reference_id", "")
                                    vendor_ref_upper = str(vendor_ref_id).upper().strip() if vendor_ref_id else ""
                                    is_drayage_carrier = vendor_ref_upper in ["MXNG", "NFBR", "PLOK", "CPGP", "GUCI", "PDCM", "BRJF", "FZMK", "TTLQ", "HOAL"]
                                    if is_air_or_drayage:
                                        if is_drayage_carrier:
                                            container["container_weight_uom"] = "KG"  # Default to "KG" for drayage carriers
                                        else:
                                            container["container_weight_uom"] = "Lb"  # Default to "Lb" for Air
                                    else:
                                        container["container_weight_uom"] = ""  # Default to empty for other modes
                                
                                # Add container_type if not present
                                container_type = container.get("container_type", "")
                                if not container_type or (isinstance(container_type, str) and container_type.strip() == ""):
                                    container["container_type"] = None
                    
                    # If payload has container number(s), send shipment_number as "bill_of_lading_number-container_number"
                    # Only apply this logic for drayage carriers when mode is "ROAD"
                    drayage_carriers = ["MXNG", "NFBR", "PLOK", "PDCM", "CPGP", "GUCI", "BRJF", "BMT", "FZMK", "TTLQ", "HOAL"]
                    is_drayage_carrier = vendor_ref_upper in drayage_carriers
                    is_road_mode = shipment_mode == "ROAD"
                    
                    if "container" in shipment and isinstance(shipment["container"], list) and len(shipment["container"]) > 0 and is_drayage_carrier and is_road_mode:
                        # Get bill_of_lading_number from root level (handle both string and structured format)
                        _bol = extracted_info.get("bill_of_lading_number", "")
                        if isinstance(_bol, dict) and "value" in _bol:
                            _bol_val = str(_bol.get("value", "")).strip()
                        else:
                            _bol_val = str(_bol).strip() if _bol else ""
                        # Use first container that has a valid container_number
                        for _cnt in shipment["container"]:
                            if not isinstance(_cnt, dict):
                                continue
                            _cn = _cnt.get("container_number")
                            if _cn is None or _cn == 0 or _cn == "":
                                continue
                            if isinstance(_cn, dict) and "value" in _cn:
                                _cn_val = str(_cn.get("value", "")).strip()
                            else:
                                _cn_val = str(_cn).strip() if _cn else ""
                            if _cn_val and _bol_val:
                                if vendor_ref_upper == "MXNG":
                                    # VAN or REEFER keyword present (LLM set TL-STANDARD) → BOL only
                                    # VAN matches: DRYVAN, DRY VAN, CARGO VAN, or any text containing "VAN"
                                    # REEFER matches: REEFER, 6Y REEFER, or any text containing "REEFER"
                                    # Neither present → BOL-container_number
                                    _llm_sl = str(shipment.get("service_level", "")).strip()
                                    if _llm_sl == "TL-STANDARD":
                                        if isinstance(shipment.get("shipment_number"), dict):
                                            shipment["shipment_number"]["value"] = _bol_val
                                        else:
                                            shipment["shipment_number"] = _bol_val
                                        logger.info(f"Set shipment_number to BOL only for MXNG (VAN or REEFER keyword present): '{_bol_val}'")
                                    else:
                                        _combined = f"{_bol_val}-{_cn_val}"
                                        if isinstance(shipment.get("shipment_number"), dict):
                                            shipment["shipment_number"]["value"] = _combined
                                        else:
                                            shipment["shipment_number"] = _combined
                                        logger.info(f"Set shipment_number to 'BOL-container_number' for MXNG (no VAN/REEFER keywords): '{_combined}'")
                                else:
                                    _combined = f"{_bol_val}-{_cn_val}"
                                    if isinstance(shipment.get("shipment_number"), dict):
                                        shipment["shipment_number"]["value"] = _combined
                                    else:
                                        shipment["shipment_number"] = _combined
                                    logger.info(f"Set shipment_number to 'bill_of_lading_number-container_number' for drayage carrier ({vendor_ref_upper}) with ROAD mode: '{_combined}'")
                            break  # use first container only
                    
                    # Set delivery_type on shipment using vendor_reference_id mapping; fallback using mode
                    try:
                        # ABFS: delivery_type is driven by the "Service:" field extracted by LLM into service_level (TL/LTL/Expedited Truck Load→TL); fallback LTL
                        if vendor_ref_upper == "ABFS":
                            llm_dt = str(shipment.get("service_level", "")).strip().upper()
                            shipment["delivery_type"] = llm_dt if llm_dt in ("TL", "LTL") else "LTL"
                            logger.info(f"Set delivery_type='{shipment['delivery_type']}' for ABFS from LLM-extracted Service field (service_level='{llm_dt}')")
                        else:
                            pass  # fall through to standard mapping below
                        delivery_type = get_delivery_type_from_vendor_reference_id(vendor_ref_upper) if vendor_ref_upper != "ABFS" else shipment["delivery_type"]
                        # Fallback by mode when mapping not found
                        shipment_mode_val = (shipment.get("mode") or "").upper()
                        if not delivery_type and vendor_ref_upper != "ABFS":
                            if shipment_mode_val == "OCEAN":
                                delivery_type = "FCL"
                            elif shipment_mode_val == "AIR":
                                delivery_type = "CARGO"
                            elif shipment_mode_val in ("ROAD", "DRAYAGE"):
                                # ROAD fallback: map to DRAYAGE/LTL/TL per image; default TL if unknown
                                _drayage = {"MXNG","PDCM","CPGP","PLOK","GUCI","NFBR"}
                                _ltl = {"AVRT","ABFS","CNWY","DAFG","CDM4"}
                                _tl = {
                                    "MMCG","MBDY","CCWO","HOAL","WSXI","EXDK",
                                    "FZMK","AXLL","MXNG","TTLQ","MSLV","BRJF",
                                    "CLIM","ESPQ","VTVN","NUST","CRCR","GFRA",
                                    "SJRG","NFTR","TXFV","WISW","HOSD","ATVH",
                                    "PFBH","PRSP","MSGR","SMMT","ARVY","XPOL","AQSM","LONE",
                                    "TLLN","CMFH","ECHS","TTHY"
                                }
                                if vendor_ref_upper in _drayage:
                                    delivery_type = "DRAYAGE"
                                elif vendor_ref_upper in _ltl:
                                    delivery_type = "LTL"
                                elif vendor_ref_upper in _tl:
                                    delivery_type = "TL"
                                else:
                                    delivery_type = "TL"
                        if vendor_ref_upper != "ABFS":
                            shipment["delivery_type"] = delivery_type or ""
                        logger.info(f"Set delivery_type='{shipment.get('delivery_type', '')}' for vendor '{vendor_ref_upper}' and mode '{shipment_mode_val}'")
                    except Exception as _e:
                        logger.warning(f"Unable to set delivery_type: {str(_e)}")
        
        # Construct the final payload structure
        payload = {
            "data": [extracted_info]
        }
        
        return payload

    def sending_json_to_external_api(self, payload):
        """Send the payload to the external API endpoint."""
        url = API_ENDPOINT
        headers = {
            "Content-Type": "application/json;  charset=utf-8",
            "internal-token": INTERNAL_TOKEN,
            "authorization": AUTHORIZATION_TOKEN
        }
        try:
            logger.info("Sending payload to external API")
            logger.info("Headers prepared")
            # Payload already logged by caller as "Final payload being sent to API"
            response = requests.post(url, headers=headers, data=json.dumps(payload, indent=2))
            logger.info(f"External API response code: {response.status_code}")
            return response
        except Exception as api_err:
            logger.error("Failed to send payload to external API", exc_info=True)
            response = None
        return response

# ---------- TRANSACTION PAYLOAD HELPER ----------
def _extract_email_address(addr: str) -> str:
    """Extract bare email address from display-name format like 'Name <email@domain>'."""
    if not addr:
        return ""
    match = re.search(r'<([^>]+)>', addr)
    if match:
        return match.group(1).strip()
    return addr.strip().strip('"')


def _get_source_channel(transaction_id: str) -> str:
    """Return 'RETRIGGER' if the transaction_id contains a retrigger suffix, else 'EMAIL'."""
    return "RETRIGGER" if re.search(r're.?trigger|retrigger', transaction_id, re.IGNORECASE) else "EMAIL"


def _get_source_transaction_id(transaction_id: str) -> str:
    """Return the base email ID (before the first '-') when a retrigger suffix is present, else empty string."""
    if '-' in transaction_id and re.search(r're.?trigger|retrigger', transaction_id, re.IGNORECASE):
        return transaction_id.split('-')[0]
    return ""


def _build_transaction_payload(
    *,
    transaction_id: str,
    step_key: str,
    transaction_status: str,
    step_status: str,
    email_details: dict,
    attachment_id: str = "",
    entity_uri: str = "",
    entity_type: str = "freight_invoice",
    step_error_code: str = "",
    step_error_message: str = "",
    step_started_at: str = "",
    step_completed_at: str = "",
    ingested_data_uri: str = "",
    invoice_number: str = "",
) -> dict:
    """Build the standard transaction status payload for Invoice Processor stages."""
    sender = _extract_email_address(email_details.get("from", ""))
    _raw_receptions = email_details.get("receptions") or (
        [email_details["to"]] if email_details.get("to") else []
    )
    raw_receptions = [addr.strip() for item in _raw_receptions for addr in str(item).split(",") if addr.strip()]
    receptions = [addr for addr in (_extract_email_address(r) for r in raw_receptions) if addr]
    received_at = email_details.get("date", "") or datetime.now().isoformat()

    return {
        "transaction_id": transaction_id,
        "source_transaction_id": _get_source_transaction_id(transaction_id),
        "client_id": CLIENT_ID,
        "ingestion_type": "invoice",
        "workflow_key": "invoice_email_pdf",
        "transaction_status": transaction_status,
        "source_channel": _get_source_channel(transaction_id),
        "received_at": received_at,
        "source_metadata": {
            "email_id": transaction_id,
            "sender_mail": sender,
            "thread_id": "",
            "receptions": receptions,
            "file_uri": email_details.get("email_s3_uri", ""),
            "attachment_count": 1,
            "subject": email_details.get("subject", ""),
        },
        "entity_id": f"ATTACHMENT#{attachment_id}" if attachment_id else "",
        "entity_uri": entity_uri,
        "entity_type": entity_type,
        "step_key": step_key,
        "step_status": step_status,
        "step_error_code": step_error_code or None,
        "step_error_message": step_error_message or None,
        "step_started_at": step_started_at or datetime.utcnow().isoformat(),
        "step_completed_at": step_completed_at or datetime.utcnow().isoformat(),
        "ingested_data_uri": ingested_data_uri,
        "invoice_number": invoice_number,
    }


def _post_transaction_payload(payload: dict, label: str = "") -> None:
    """POST the transaction status payload to the configured endpoint and log the response."""
    if not TRANSACTION_STATUS_PAYLOAD_URL:
        logger.info("TRANSACTION_STATUS_API_RESPONSE | %s | SKIPPED — TRANSACTION_STATUS_PAYLOAD env var not set", label)
        return
    logger.info("TRANSACTION_STATUS_API_RESPONSE | %s | posting to %s", label, TRANSACTION_STATUS_PAYLOAD_URL)
    try:
        headers = {
            "Content-Type": "application/json",
            "internal-token": INTERNAL_TOKEN,
        }
        resp = requests.post(
            TRANSACTION_STATUS_PAYLOAD_URL,
            headers=headers,
            data=json.dumps(payload),
            timeout=10,
        )
        logger.info(
            "TRANSACTION_STATUS_API_RESPONSE | %s | %s",
            label,
            json.dumps({
                "status_code": resp.status_code,
                "response": resp.text,
            }),
        )
    except Exception as exc:
        logger.info(
            "TRANSACTION_STATUS_API_RESPONSE | %s | %s",
            label,
            json.dumps({"status_code": None, "error": str(exc)}),
        )
# ─────────────────────────────────────────────────────────────────────────────


# ---------- STATUS TRACKER HELPERS ----------
def _copy_to_tracker(src_uri: str, dest_key: str) -> str:
    """Copy an S3 object to the status tracker bucket. Returns the new S3 URI."""
    if not STATUS_TRACKER_BUCKET or not src_uri or not src_uri.startswith("s3://"):
        return ""
    try:
        uri_path = src_uri[5:]
        slash_idx = uri_path.index("/")
        src_bucket, src_key = uri_path[:slash_idx], uri_path[slash_idx + 1:]
        s3.copy_object(
            CopySource={"Bucket": src_bucket, "Key": src_key},
            Bucket=STATUS_TRACKER_BUCKET,
            Key=dest_key,
        )
        logger.info("Status tracker: copied %s -> s3://%s/%s", src_uri, STATUS_TRACKER_BUCKET, dest_key)
        return f"s3://{STATUS_TRACKER_BUCKET}/{dest_key}"
    except Exception as exc:
        logger.warning("Status tracker: copy failed for %s: %s", src_uri, exc)
        return ""


def _save_payload_to_tracker(transaction_id: str, step_key: str, payload: dict) -> str:
    """Save a transaction payload JSON to the status tracker bucket. Returns the S3 URI."""
    if not STATUS_TRACKER_BUCKET:
        return ""
    try:
        dest_key = f"{STATUS_TRACKER_PREFIX}/{transaction_id}/{step_key}_payload.json"
        s3.put_object(
            Bucket=STATUS_TRACKER_BUCKET,
            Key=dest_key,
            Body=json.dumps(payload, indent=2),
            ContentType="application/json",
        )
        logger.info("Status tracker: saved payload s3://%s/%s", STATUS_TRACKER_BUCKET, dest_key)
        return f"s3://{STATUS_TRACKER_BUCKET}/{dest_key}"
    except Exception as exc:
        logger.warning("Status tracker: payload save failed (%s/%s): %s", transaction_id, step_key, exc)
        return ""


# ---------- MAIN LAMBDA HANDLER ----------
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler function for processing invoices."""
    try:
        # Initialize handlers
        api_handler = APIHandler()
        
        # Parse event - handle SQS, S3, or direct invocation
        try:
            # Check if this is an SQS event
            logger.info(f"Event:\n{_format_json_for_log(event)}")
            if 'Records' in event and len(event['Records']) > 0:
                record = event['Records'][0]
                
                # Check if it's an SQS event
                if 'eventSource' in record and record['eventSource'] == 'aws:sqs':
                    # Parse SQS message body
                    message_body = json.loads(record['body'])
                    input_bucket = message_body['input_bucket']
                    input_key = message_body['input_key']
                    output_bucket = message_body['output_bucket']
                    output_prefix = message_body['output_prefix']
                    email_details = message_body['email_details']
                    email_id = message_body['job_id']
                    attachment_id = message_body['attachment_id']
                    
                    # Extract original PDF path if available (from PDF Splitter)
                    original_input_bucket = message_body.get('original_input_bucket', input_bucket)
                    original_input_key = message_body.get('original_input_key', input_key)
                    
                    logger.info(f"Triggered by SQS message: s3://{input_bucket}/{input_key}")
                    logger.info(f"Original PDF path: s3://{original_input_bucket}/{original_input_key}")
                
                # Check if it's an S3 event
                elif 's3' in record:
                    s3_info = record['s3']
                    input_bucket = s3_info['bucket']['name']
                    input_key = unquote_plus(s3_info["object"]["key"])
                    output_bucket = S3_BUCKET
                    output_prefix = "output/"
                    
                    # For S3 events, original path is the same as input path
                    original_input_bucket = input_bucket
                    original_input_key = input_key
                    
                    # For direct S3 trigger, we don't have email_details
                    email_details = event.get('email_details', {
                        'to': 'default@example.com',
                        'from': 'system@example.com',
                        'subject': f'S3 Triggered Invoice: {input_key}',
                        'original_body': '',
                        'filename': input_key,
                        'message_id': None
                    })
                    email_id = event.get('job_id', f'S3-{int(time.time())}')
                    attachment_id = event.get('attachment_id', f'ATT-{int(time.time())}')
                    logger.info(f"Triggered by S3 upload: s3://{input_bucket}/{input_key}")
                    logger.info(f"Original PDF path: s3://{original_input_bucket}/{original_input_key}")
                else:
                    raise KeyError("Unknown event source")
            else:
                # Direct invocation with parameters
                input_bucket = event['input_bucket']
                input_key = event['input_key']
                output_bucket = event['output_bucket']
                output_prefix = event['output_prefix']
                email_details = event['email_details']
                email_id = event['job_id']
                attachment_id = event['attachment_id']
                
                # Extract original PDF path if available (from PDF Splitter)
                original_input_bucket = event.get('original_input_bucket', input_bucket)
                original_input_key = event.get('original_input_key', input_key)
                
                logger.info(f"Triggered by direct invocation: s3://{input_bucket}/{input_key}")
                logger.info(f"Original PDF path: s3://{original_input_bucket}/{original_input_key}")
                
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.error(f"Error parsing event: {str(e)}")
            logger.error(f"Event structure:\n{_format_json_for_log(event)}")
            raise
        
        logger.info(f"Processing invoice from bucket: {input_bucket}, key: {input_key}")
        logger.info(f"Email details processed")
        
        # Check if this is a "no freight invoice" case from Pdf-Splitter
        no_freight_invoice = event.get('no_freight_invoice', False)
        if no_freight_invoice:
            logger.info("Processing document marked as 'no freight invoice' from Pdf-Splitter")
        
        # Check if this is a BOL document
        is_bol = False
        bol_page_index = None
        bol_total_pages = None
        
        # Check in SQS message body first
        if 'Records' in event and len(event['Records']) > 0:
            record = event['Records'][0]
            if 'body' in record:
                try:
                    message_body = json.loads(record['body'])
                    is_bol = message_body.get('is_bol', False)
                    bol_page_index = message_body.get('bol_page_index')
                    bol_total_pages = message_body.get('bol_total_pages')
                except (json.JSONDecodeError, KeyError):
                    pass
        
        # Check in direct event
        if not is_bol:
            is_bol = event.get('is_bol', False)
            bol_page_index = event.get('bol_page_index')
            bol_total_pages = event.get('bol_total_pages')
        
        if is_bol:
            logger.info(f"Processing BOL document (page index: {bol_page_index}, total pages: {bol_total_pages})")
        
        # Step 1: Extract text from PDF using Textract (or use pre-extracted results from splitter)
        logger.info("Step 1: Extracting text from PDF")
        
        # Initialize container_numbers for BOL processing
        container_numbers = []
        
        # Check if Textract results are already available from splitter
        pre_extracted_kvs = None
        pre_extracted_tables = None
        pre_extracted_lines = None
        bol_page_textract_kvs = {}
        bol_page_textract_lines = {}
        canonical_bol_analysis = {}

        # Check in SQS message body first
        if 'Records' in event and len(event['Records']) > 0:
            record = event['Records'][0]
            if 'body' in record:
                try:
                    message_body = json.loads(record['body'])
                    pre_extracted_kvs = message_body.get('textract_kvs')
                    pre_extracted_tables = message_body.get('textract_tables')
                    pre_extracted_lines = message_body.get('textract_lines')
                    bol_page_textract_kvs = message_body.get('bol_page_textract_kvs', {})
                    bol_page_textract_lines = message_body.get('bol_page_textract_lines', {})
                    canonical_bol_analysis = message_body.get('canonical_bol_analysis', {})
                except (json.JSONDecodeError, KeyError):
                    pass
        
        # Check in direct event
        if not pre_extracted_kvs:
            pre_extracted_kvs = event.get('textract_kvs')
            pre_extracted_tables = event.get('textract_tables')
            pre_extracted_lines = event.get('textract_lines')
            bol_page_textract_kvs = event.get('bol_page_textract_kvs', {})
            bol_page_textract_lines = event.get('bol_page_textract_lines', {})
            canonical_bol_analysis = event.get('canonical_bol_analysis', {})
        
        try:
            if pre_extracted_kvs is not None and pre_extracted_tables is not None and pre_extracted_lines is not None:
                logger.info("Using pre-extracted Textract results from splitter")
                # Convert page-level dictionaries to flat structures for compatibility
                # Merge all pages' KVs into a single dict
                kvs = {}
                for page_num, page_kvs in pre_extracted_kvs.items():
                    kvs.update(page_kvs)
                
                # Flatten tables into a single list
                tables = []
                for page_num, page_tables in pre_extracted_tables.items():
                    tables.extend(page_tables)
                
                # Flatten lines into a single list
                lines = []
                for page_num, page_lines in pre_extracted_lines.items():
                    lines.extend(page_lines)
                
                page_count = len(pre_extracted_lines)
                logger.info(f"Using pre-extracted results: {len(kvs)} KVs, {len(tables)} tables, {len(lines)} lines, {page_count} pages")
            else:
                logger.info("No pre-extracted Textract results found, calling extract_text_from_pdf()")
                kvs, tables, lines, page_count = extract_text_from_pdf(input_bucket, input_key)

            
            # Format extracted data
            def table_to_markdown(table):
                if not table or not table[0]:
                    return ""
                col_count = len(table[0])
                cleaned_table = []
                for row in table:
                    cleaned_row = [(cell if cell is not None else "").strip() for cell in row]
                    if len(cleaned_row) < col_count:
                        cleaned_row += [""] * (col_count - len(cleaned_row))
                    cleaned_table.append(cleaned_row)
                header = "| " + " | ".join(cleaned_table[0]) + " |"
                separator = "| " + " | ".join(["---"] * col_count) + " |"
                rows = ["| " + " | ".join(row) + " |" for row in cleaned_table[1:]]
                return "\n".join([header, separator] + rows)
                
            formatted_kvs = "\n".join(f"{key}: {value}" for key, value in kvs.items())
            formatted_tables = "\n\n".join(table_to_markdown(tbl) for tbl in tables)
            formatted_lines = "\n".join(lines)

            logger.info("Extracted key value pairs:")
            for key, value in kvs.items():
                logger.info(f"  {key}: {value}")
            logger.info("Extracted tables:")
            for i, tbl in enumerate(tables):
                md = table_to_markdown(tbl)
                if not md:
                    logger.info(f"  Table {i + 1}: (empty)")
                    continue
                logger.info(f"  Table {i + 1}:")
                for row in md.split("\n"):
                    logger.info(f"    {row}")
            logger.info("Extracted lines:")
            for j, line in enumerate(lines):
                logger.info(f"  [{j + 1}] {line}")

            raw_text = "\n\n".join(filter(None, [
                "Extracted Key-Value Pairs:\n" + formatted_kvs if formatted_kvs else None,
                "Extracted Tables:\n" + formatted_tables if formatted_tables else None,
                "OCR Text Lines:\n" + formatted_lines if formatted_lines else None
            ]))

            # Append BOL page textract lines so Claude can apply canonical location mapping
            if bol_page_textract_lines:
                bol_sections = []
                for pg_key in sorted(bol_page_textract_lines.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
                    pg_lines = bol_page_textract_lines[pg_key]
                    if pg_lines:
                        joined = "\n".join(pg_lines)
                        bol_sections.append(f"BOL Page {pg_key} Text Lines:\n{joined}")
                # Also include BOL KV pairs for richer context
                if bol_page_textract_kvs:
                    for pg_key in sorted(bol_page_textract_kvs.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
                        pg_kvs = bol_page_textract_kvs[pg_key]
                        if pg_kvs:
                            kv_text = "\n".join(f"{k}: {v}" for k, v in pg_kvs.items() if v)
                            bol_sections.append(f"BOL Page {pg_key} Key-Value Pairs:\n{kv_text}")
                if bol_sections:
                    bol_text_block = "=== BILL OF LADING PAGE DATA (use for canonical source/destination mapping) ===\n" + "\n\n".join(bol_sections)
                    raw_text = raw_text + "\n\n" + bol_text_block
                    logger.info(f"Appended BOL page textract data from {len(bol_sections)} section(s) to raw_text")

            logger.info(f"Raw text extracted from PDF: {len(raw_text)} characters")
            
        except Exception as extract_error:
            error_msg = f"Failed to extract text from PDF: {str(extract_error)}"
            update_attachment_status(
                dynamodb_client,
                DYNAMODB_TABLE,
                email_id,
                attachment_id,
                'FAILED',
                error={
                    'message': error_msg,
                    'error_code': 'TEXTRACT_FAILED'
                },
                textract_failed=1,     # Add textract failure flag
                missing_fields=[],      # Empty array for missing fields
                confidence_score=None,   # No confidence score available
                payment_terms=None     # No payment terms available at this stage
            )
            raise ValueError(error_msg)

        if not lines:
            error_msg = "No text extracted from PDF"
            update_attachment_status(
                dynamodb_client,
                DYNAMODB_TABLE,
                email_id,
                attachment_id,
                'FAILED',
                error={
                    'message': error_msg,
                    'error_code': 'TEXTRACT_FAILED'
                },
                textract_failed=1,     # Add textract failure flag
                missing_fields=[],      # Empty array for missing fields
                confidence_score=None,   # No confidence score available
                payment_terms=None     # No payment terms available at this stage
            )
            raise ValueError(error_msg)
        
        # Step 1.5: Get carrier name (pre-classified from splitter or classify if not available)
        logger.info("Step 1.5: Getting carrier name")
        carrier_name = None
        
        # Check if carrier name is already provided from splitter
        textract_model_result = {}
        image_model_result = {}
        if 'Records' in event and len(event['Records']) > 0:
            record = event['Records'][0]
            if 'body' in record:
                try:
                    message_body = json.loads(record['body'])
                    carrier_name = message_body.get('carrier_name')
                    textract_model_result = message_body.get('textract_model_result', {})
                    image_model_result = message_body.get('image_model_result', {})
                except (json.JSONDecodeError, KeyError):
                    pass

        # Check in direct event
        if not carrier_name:
            carrier_name = event.get('carrier_name')
        
        if carrier_name:
            logger.info(f"Using pre-classified carrier from splitter: {carrier_name}")
        else:
            # Fallback: Classify carrier if not provided (for direct S3 triggers or manual invocations)
            logger.info("No pre-classified carrier found, classifying carrier using CarrierClassifier")
            try:
                classifier = CarrierClassifier()
                carrier_name = classifier.classify_document(raw_text)
                if carrier_name:
                    logger.info(f"Document classified as carrier: {carrier_name}")
                else:
                    logger.warning("Carrier classification returned None, will use generic extraction")
            except Exception as e:
                logger.warning(f"Carrier classification failed: {str(e)}, will use generic extraction")
                # Continue with generic extraction if classification fails
        
        # Do not process invoice if carrier is unknown, unsupported, or GENERIC
        _is_generic_carrier = isinstance(carrier_name, str) and carrier_name.strip().upper() == "GENERIC"
        if not carrier_name or (isinstance(carrier_name, str) and not carrier_name.strip()) or _is_generic_carrier:
            if _is_generic_carrier:
                error_msg = "Carrier classified as GENERIC — carrier not available in the system; invoice will not be processed"
                _carrier_error_code = "GENERIC_CARRIER"
            else:
                error_msg = "Unclassified document - unsupported or unknown carrier; invoice will not be processed"
                _carrier_error_code = "CARRIER_NOT_FOUND"
            logger.warning(error_msg)

            # ── TRANSACTION PAYLOAD: invoice_processing (carrier not found / GENERIC) ──
            _carrier_fail_raw_uri = f"s3://{original_input_bucket}/{original_input_key}"
            _carrier_fail_pdf_filename = email_details.get("filename") or original_input_key.split("/")[-1]
            _carrier_fail_entity_uri = _copy_to_tracker(
                _carrier_fail_raw_uri,
                f"{STATUS_TRACKER_PREFIX}/{email_id}/{_carrier_fail_pdf_filename}"
            ) or _carrier_fail_raw_uri
            _carrier_fail_payload = _build_transaction_payload(
                transaction_id=email_id,
                step_key="INVOICE_PROCESSING",
                transaction_status="FAILED",
                step_status="FAILED",
                email_details=email_details,
                attachment_id=attachment_id,
                entity_uri=_carrier_fail_entity_uri,
                entity_type="freight_invoice",
                step_error_code=_carrier_error_code,
                step_error_message=error_msg,
                step_started_at=datetime.now().isoformat(),
                step_completed_at=datetime.now().isoformat(),
            )
            _save_payload_to_tracker(email_id, "INVOICE_PROCESSING_carrier_error", _carrier_fail_payload)
            logger.info("TRANSACTION_PAYLOAD | invoice_processing (carrier error) | %s", json.dumps(_carrier_fail_payload))
            _post_transaction_payload(_carrier_fail_payload, "invoice_processing (carrier_error)")
            # ─────────────────────────────────────────────────────────────────────

            update_attachment_status(
                dynamodb_client,
                DYNAMODB_TABLE,
                email_id,
                attachment_id,
                'UNCLASSIFIED',
                error={
                    'message': error_msg,
                    'error_code': _carrier_error_code
                },
                classification_failed=1,
                textract_failed=0,
                missing_fields=[],
                confidence_score=None,
                payment_terms=None
            )
            # Send unclassified document notification email to the replier (to_email only, no routing logic)
            try:
                to_email = email_details.get('to', '')
                send_unclassified_notification(
                    smtp_server=SMTP_SERVER,
                    smtp_port=SMTP_PORT,
                    smtp_username=SMTP_USERNAME,
                    smtp_password=SMTP_PASSWORD,
                    from_email=FROM_EMAIL,
                    to_email=to_email,
                    subject=email_details.get('subject', ''),
                    original_body=email_details.get('original_body', ''),
                    filename=email_details.get('filename', ''),
                    message_id=email_details.get('status_message_id') or email_details.get('message_id'),
                    quoted_sender=email_details.get('status_email_sender'),
                    quoted_date=email_details.get('status_email_date'),
                    quoted_subject=email_details.get('status_email_subject') or email_details.get('subject'),
                    quoted_body=email_details.get('status_email_body') or email_details.get('original_body'),
                    reply_recipients=None
                )
                logger.info("Unclassified document notification sent successfully")
            except Exception as notify_err:
                logger.error(f"Failed to send unclassified document notification: {str(notify_err)}")
                # Continue to raise so processing is still aborted
            raise ValueError(error_msg)
        
        # Step 1.75: Get container numbers from splitter (extracted from BOL pages)
        container_numbers = []
        
        # Check if container numbers are provided from splitter
        if 'Records' in event and len(event['Records']) > 0:
            record = event['Records'][0]
            if 'body' in record:
                try:
                    message_body = json.loads(record['body'])
                    container_numbers = message_body.get('container_numbers', [])
                    if container_numbers:
                        logger.info(f"Step 1.75: Using container numbers from splitter: {container_numbers}")
                except (json.JSONDecodeError, KeyError):
                    pass
        
        # Check in direct event
        if not container_numbers:
            container_numbers = event.get('container_numbers', [])
            if container_numbers:
                logger.info(f"Step 1.75: Using container numbers from event: {container_numbers}")
        
        # Step 2: Extract structured information using Claude
        logger.info("Step 2: Extracting structured information")
        average_confidence = None  # Initialize confidence score variable
        extracted_fields_array = None  # Initialize extracted fields array
        _processing_started_at = datetime.utcnow().isoformat()
        _entity_uri = f"s3://{original_input_bucket}/{original_input_key}"

        # ── Archive email, PDF, and all payloads to status tracker ───────────
        _pdf_filename = original_input_key.split("/")[-1]
        # Use the original filename from email_details to match Orchestrator's tracker path (no UUID prefix)
        _tracker_pdf_filename = email_details.get("filename") or _pdf_filename
        _tracker_entity_uri = _copy_to_tracker(
            _entity_uri,
            f"{STATUS_TRACKER_PREFIX}/{email_id}/{_tracker_pdf_filename}"
        )
        _email_src_uri = email_details.get("email_s3_uri", "")
        _tracker_email_uri = _copy_to_tracker(
            _email_src_uri, f"{STATUS_TRACKER_PREFIX}/{email_id}/email"
        )
        email_details = {**email_details, "email_s3_uri": _tracker_email_uri or _email_src_uri}
        # ─────────────────────────────────────────────────────────────────────

        try:
            # Always use Claude extraction for invoice documents (not BOL documents)
            if is_bol:
                logger.warning("Received BOL document in invoice processor - this should not happen. Skipping processing.")
                raise ValueError("BOL documents should not be sent to invoice processor")
            else:
                # Extract invoice information using Claude
                extracted_info = extract_information_with_claude(raw_text, carrier_name=carrier_name)
                # If invoice_date is >6 months old, retry once with format-only correction note
                extracted_info = _maybe_retry_stale_invoice_date(
                    extracted_info, raw_text, carrier_name=carrier_name
                )

                # Add container numbers from splitter to extracted info if available
                if container_numbers and "shipments" in extracted_info:
                    logger.info(f"Adding {len(container_numbers)} container number(s) from splitter to extracted info")
                    # Normalize vendor ref id from either plain string or structured {value, explanation, confidence}
                    vendor_ref_id_val = extracted_info.get("vendor_reference_id", "")
                    if isinstance(vendor_ref_id_val, dict):
                        vendor_ref_id_val = vendor_ref_id_val.get("value", "")
                    vendor_ref_upper = str(vendor_ref_id_val).upper().strip() if vendor_ref_id_val else ""
                    # Normalize carrier name as fallback for splitter-only behavior
                    carrier_name_upper = str(carrier_name).upper().strip() if carrier_name else ""
                    # Carriers where splitter containers should replace any prompt-extracted containers
                    splitter_only_vendor_refs = {"NFBR", "MXNG", "PLOK"}
                    splitter_only_carrier_names = {"NFI", "MAXTRANS", "POINT LOGISTICS", "POINTLOGISTICS"}
                    splitter_only_mode = (
                        vendor_ref_upper in splitter_only_vendor_refs
                        or carrier_name_upper in splitter_only_carrier_names
                    )
                    # Build normalized set of splitter container numbers for duplicate checks
                    def _norm_cn(v):
                        if v is None:
                            return ""
                        return str(v).upper().replace(" ", "").replace("-", "").strip()
                    splitter_cn_set = {_norm_cn(cn) for cn in container_numbers if _norm_cn(cn)}
                    for shipment in extracted_info["shipments"]:
                        if isinstance(shipment, dict):
                            # Initialize container array if not present
                            if "container" not in shipment:
                                shipment["container"] = []
                            
                            # For NFBR/MXNG/PLOK, when splitter container_numbers exist, drop any prompt-extracted containers
                            if splitter_only_mode and splitter_cn_set:
                                logger.info(
                                    f"Splitter-only container mode enabled for carrier='{carrier_name_upper}', "
                                    f"vendor_ref='{vendor_ref_upper}'. Replacing prompt-extracted containers."
                                )
                                shipment["container"] = []
                            
                            # Add container numbers from splitter
                            for cn in container_numbers:
                                # Check if container number already exists
                                existing_container = False
                                if isinstance(shipment["container"], list):
                                    for existing_cn_obj in shipment["container"]:
                                        if isinstance(existing_cn_obj, dict):
                                            cn_value = existing_cn_obj.get("container_number", {})
                                            if isinstance(cn_value, dict):
                                                cn_value_str = cn_value.get("value", "")
                                            else:
                                                cn_value_str = str(cn_value)
                                            if _norm_cn(cn) == _norm_cn(cn_value_str):
                                                existing_container = True
                                                break
                                
                                if not existing_container:
                                    # Auto-generate next sequential container_id based on current container array
                                    next_container_id = 1
                                    if isinstance(shipment["container"], list):
                                        max_existing_id = 0
                                        for existing_cn_obj in shipment["container"]:
                                            if not isinstance(existing_cn_obj, dict):
                                                continue
                                            existing_id = existing_cn_obj.get("container_id")
                                            if isinstance(existing_id, dict):
                                                existing_id = existing_id.get("value")
                                            try:
                                                existing_id_int = int(existing_id)
                                                if existing_id_int > max_existing_id:
                                                    max_existing_id = existing_id_int
                                            except (TypeError, ValueError):
                                                continue
                                        next_container_id = max_existing_id + 1

                                    container_obj = {
                                        "container_id": {
                                            "value": next_container_id,
                                            "explanation": "Auto-generated sequential ID based on container order",
                                            "confidence": 1.0
                                        },
                                        "container_number": {
                                            "value": cn,
                                            "explanation": "Extracted from BOL pages by splitter using Textract + LLM",
                                            "confidence": 0.95
                                        },
                                        "container_type": {
                                            "value": "NONE",
                                            "explanation": "Default value for container type",
                                            "confidence": 1.0
                                        },
                                        "no_of_containers": {
                                            "value": 0,
                                            "explanation": "Default value for number of containers",
                                            "confidence": 1.0
                                        },
                                        "container_weight": {
                                            "value": 0,
                                            "explanation": "Default value for container weight",
                                            "confidence": 1.0
                                        },
                                        "container_weight_uom": {
                                            "value": "KG",
                                            "explanation": "Default unit of measure for container weight",
                                            "confidence": 1.0
                                        }
                                    }
                                    shipment["container"].append(container_obj)
                                    logger.info(f"Added container number {cn} to shipment with all required fields")
            if not extracted_info:
                error_msg = "No information extracted from document"
                update_attachment_status(
                    dynamodb_client,
                    DYNAMODB_TABLE,
                    email_id,
                    attachment_id,
                    'FAILED',
                    error={
                        'message': error_msg,
                        'error_code': 'EXTRACTION_FAILED'
                    },
                    extraction_failed=1,  # Add extraction failure flag
                    missing_fields=[],     # Empty array for missing fields
                    confidence_score=None,  # No confidence score since extraction failed
                    payment_terms=None   # No payment terms available at this stage
                )
                raise ValueError(error_msg)
            
            # Step 2.5: J & R Schugel (SCAC SJRG / JR_SCHUGEL) — cropped image + Claude for BILL TO + SHIPPER + CONSIGNEE
            if _jr_schugel_image_extraction_eligible(carrier_name, extracted_info):
                logger.info("Step 2.5 JR: J & R Schugel eligible — cropped logistics extraction (TRUCK# through DESCRIPTION)")
                try:
                    _crop_bucket = (S3_BUCKET or output_bucket or "").strip() or None
                    jr_block = extract_jr_schugel_cropped_logistics_from_pdf(
                        input_bucket,
                        input_key,
                        archive_cropped_png_bucket=_crop_bucket,
                        archive_cropped_png_email_id=email_id,
                        archive_cropped_png_attachment_id=attachment_id,
                    )
                    if jr_block:
                        def _jr_set_structured(root: Dict[str, Any], field: str, val: Any) -> None:
                            if val is None:
                                return
                            s = str(val).strip() if not isinstance(val, str) else val.strip()
                            if not s or s.lower() == "null":
                                return
                            root[field] = {
                                "value": s,
                                "explanation": "J & R Schugel cropped image (TRUCK#–DESCRIPTION) + vision model",
                                "confidence": 1.0,
                            }

                        for f in ("bill_to_name", "bill_to_address"):
                            if f in jr_block:
                                _jr_set_structured(extracted_info, f, jr_block.get(f))
                        if "shipments" in extracted_info and isinstance(extracted_info["shipments"], list):
                            for shipment in extracted_info["shipments"]:
                                if not isinstance(shipment, dict):
                                    continue
                                # SELECTIVE OVERRIDE RULE (Priority 1 preservation): if source_name /
                                # destination_name were already resolved to a canonical code by the
                                # LLM (BOL-textract Priority 1), keep that name and only sync the
                                # address/city/state fields from the cropped-image extraction.
                                source_already_canonical = _value_is_already_canonical(shipment.get("source_name"))
                                dest_already_canonical = _value_is_already_canonical(shipment.get("destination_name"))
                                for f in (
                                    "source_name", "source_city", "source_state", "source_address",
                                    "destination_name", "destination_city", "destination_state", "destination_address",
                                ):
                                    if f == "source_name" and source_already_canonical:
                                        logger.info(
                                            f"  source_name already canonical ('{shipment.get('source_name')}') — "
                                            "keeping it, only syncing address/city/state from J&R cropped extraction"
                                        )
                                        continue
                                    if f == "destination_name" and dest_already_canonical:
                                        logger.info(
                                            f"  destination_name already canonical ('{shipment.get('destination_name')}') — "
                                            "keeping it, only syncing address/city/state from J&R cropped extraction"
                                        )
                                        continue
                                    if f in jr_block:
                                        _jr_set_structured(shipment, f, jr_block.get(f))

                                # Attempt canonical matching on the image-extracted values, but only
                                # for names that were NOT already canonical (preserved above).
                                if not source_already_canonical:
                                    src_canon, src_var = _canonical_match_with_fields(
                                        name=jr_block.get("source_name", ""),
                                        address=jr_block.get("source_address", ""),
                                        city=jr_block.get("source_city", ""),
                                        state=jr_block.get("source_state", ""),
                                    )
                                    if src_canon:
                                        logger.info(f"Step 2.5 JR: canonical mapping source '{jr_block.get('source_name')}' → '{src_canon}' (matched '{src_var}')")
                                        shipment["source_name"] = {
                                            "value": src_canon,
                                            "explanation": f"Canonical location (matched '{src_var}')",
                                            "confidence": 1.0,
                                        }
                                if not dest_already_canonical:
                                    dst_canon, dst_var = _canonical_match_with_fields(
                                        name=jr_block.get("destination_name", ""),
                                        address=jr_block.get("destination_address", ""),
                                        city=jr_block.get("destination_city", ""),
                                        state=jr_block.get("destination_state", ""),
                                    )
                                    if dst_canon:
                                        logger.info(f"Step 2.5 JR: canonical mapping destination '{jr_block.get('destination_name')}' → '{dst_canon}' (matched '{dst_var}')")
                                        shipment["destination_name"] = {
                                            "value": dst_canon,
                                            "explanation": f"Canonical location (matched '{dst_var}')",
                                            "confidence": 1.0,
                                        }

                        # invoice_source_name / invoice_destination_name ALWAYS reflect the RAW
                        # J&R Schugel cropped-image extraction result (audit trail), regardless of
                        # whether source_name/destination_name were preserved as canonical above.
                        if "custom_fields" not in extracted_info:
                            extracted_info["custom_fields"] = {}
                        if jr_block.get("source_name"):
                            extracted_info["custom_fields"]["invoice_source_name"] = {
                                "value": jr_block["source_name"],
                                "explanation": "Raw shipper name extracted from J & R Schugel cropped image without any canonical mapping applied",
                                "confidence": 1.0,
                            }
                        if jr_block.get("destination_name"):
                            extracted_info["custom_fields"]["invoice_destination_name"] = {
                                "value": jr_block["destination_name"],
                                "explanation": "Raw consignee name extracted from J & R Schugel cropped image without any canonical mapping applied",
                                "confidence": 1.0,
                            }
                        logger.info("Step 2.5 JR: Applied cropped image logistics overrides to extracted_info")
                    else:
                        logger.info("Step 2.5 JR: Cropped extraction returned no fields; keeping text extraction")
                except Exception as jr_err:
                    logger.warning(
                        f"Step 2.5 JR: J & R Schugel image extraction failed ({jr_err}); continuing with text extraction",
                        exc_info=True,
                    )

            # Step 2.5: Override source/destination for carriers that need cropped extraction
            logger.info(f"Step 2.5: Checking if carrier '{carrier_name}' needs source/destination extraction")
            # Define carrier-specific header keywords
            # Note: Handle both "M&M Cartage" (from classifier) and "M&M Cartage Co. Inc." (from prompt template)
            carrier_extraction_config = {
                "Buddy Moore Trucking": {
                    "start_keywords": ["Date"],
                    "end_keywords": ["SHIP DATE"]
                },
                "M&M Cartage": {
                    "start_keywords": ["DATE", "Date"],
                    "end_keywords": ["SHIP DATE", "SHIP DATE:"]
                },
                "M&M Cartage Co. Inc.": {
                    "start_keywords": ["DATE", "Date"],
                    "end_keywords": ["SHIP DATE", "SHIP DATE:"]
                },
                "Averitt Express": {
                    "method": "averitt_top_crop"  # Special flag for Averitt's top-crop method
                },
                "Dayton Freight Lines Inc": {
                    "start_keywords": ["Dayton Freight"],
                    "end_keywords": ["Orig. Term", "Orig Term", "Dest. Term", "Dest Term"]
                }
            }
            
            # Normalize carrier name for matching (handle variations)
            normalized_carrier_name = carrier_name
            if carrier_name:
                logger.info(f"Checking carrier name for extraction: '{carrier_name}'")
                if "M&M Cartage" in carrier_name or "M&M Cartage Co. Inc." in carrier_name:
                    normalized_carrier_name = "M&M Cartage"  # Use the classifier's format
                    logger.info(f"Normalized to: '{normalized_carrier_name}'")
                elif "Buddy Moore" in carrier_name:
                    normalized_carrier_name = "Buddy Moore Trucking"
                    logger.info(f"Normalized to: '{normalized_carrier_name}'")
                elif "AVERITT" in carrier_name.upper():
                    normalized_carrier_name = "Averitt Express"
                    logger.info(f"Normalized to: '{normalized_carrier_name}'")
                elif "Brown Trucking" in carrier_name or "Brown Trucking Company" in carrier_name:
                    normalized_carrier_name = "Brown Trucking" if "Brown Trucking" in carrier_name and carrier_name.strip() == "Brown Trucking" else "Brown Trucking Company"
                    if "Brown Trucking Company" in carrier_name:
                        normalized_carrier_name = "Brown Trucking Company"
                    elif "Brown Trucking" in carrier_name:
                        normalized_carrier_name = "Brown Trucking"
                    logger.info(f"Normalized to: '{normalized_carrier_name}'")
                elif "DAYTON" in carrier_name.upper():
                    normalized_carrier_name = "Dayton Freight Lines Inc"
                    logger.info(f"Normalized to: '{normalized_carrier_name}'")
            
            logger.info(f"Final normalized carrier name: '{normalized_carrier_name}', in config: {normalized_carrier_name in carrier_extraction_config if normalized_carrier_name else False}")
            logger.info(f"Available config keys: {list(carrier_extraction_config.keys())}")
            if normalized_carrier_name and normalized_carrier_name in carrier_extraction_config:
                config = carrier_extraction_config[normalized_carrier_name]
                
                # Handle Averitt Express separately (uses top-crop method instead of keyword-based crop)
                if config.get("method") == "averitt_top_crop":
                    logger.info(f"Step 2.5: Extracting Averitt Express shipper/consignee using top-crop method")
                    try:
                        carrier_details = extract_averitt_shipper_consignee(
                            input_bucket,
                            input_key,
                            email_id  # transaction_id
                        )
                        if carrier_details:
                            logger.info(f"Overriding source/destination with Averitt extraction:\n{_format_json_for_log(carrier_details)}")
                            # Override shipment details with extracted values.
                            #
                            # SELECTIVE OVERRIDE RULE (Priority 1 preservation):
                            # If the LLM already resolved source_name / destination_name to a
                            # canonical code (via the BOL-textract Priority 1 instructions in the
                            # prompt), do NOT overwrite that name with the image-extraction result.
                            # Only fill in address/city/state from the image extraction in that case.
                            # If the LLM did NOT find a canonical match, override the name too (and
                            # canonical matching is attempted again below on the image-extracted name).
                            if "shipments" in extracted_info and extracted_info["shipments"]:
                                for shipment in extracted_info["shipments"]:
                                    if isinstance(shipment, dict):
                                        source_already_canonical = _value_is_already_canonical(shipment.get("source_name"))
                                        dest_already_canonical = _value_is_already_canonical(shipment.get("destination_name"))

                                        # Source fields
                                        if source_already_canonical:
                                            logger.info(
                                                f"  source_name already canonical ('{shipment.get('source_name')}') — "
                                                "keeping it, only syncing address/city/state from image extraction"
                                            )
                                        elif "source_name" in carrier_details:
                                            shipment["source_name"] = {"value": carrier_details["source_name"], "explanation": "Extracted from Averitt top-crop image", "confidence": 1.0}
                                        if "source_city" in carrier_details:
                                            shipment["source_city"] = {"value": carrier_details["source_city"], "explanation": "Extracted from Averitt top-crop image", "confidence": 1.0}
                                        if "source_state" in carrier_details:
                                            shipment["source_state"] = {"value": carrier_details["source_state"], "explanation": "Extracted from Averitt top-crop image", "confidence": 1.0}
                                        if "source_address" in carrier_details:
                                            shipment["source_address"] = {"value": carrier_details["source_address"], "explanation": "Extracted from Averitt top-crop image", "confidence": 1.0}

                                        # Destination fields
                                        if dest_already_canonical:
                                            logger.info(
                                                f"  destination_name already canonical ('{shipment.get('destination_name')}') — "
                                                "keeping it, only syncing address/city/state from image extraction"
                                            )
                                        elif "destination_name" in carrier_details:
                                            shipment["destination_name"] = {"value": carrier_details["destination_name"], "explanation": "Extracted from Averitt top-crop image", "confidence": 1.0}
                                        if "destination_city" in carrier_details:
                                            shipment["destination_city"] = {"value": carrier_details["destination_city"], "explanation": "Extracted from Averitt top-crop image", "confidence": 1.0}
                                        if "destination_state" in carrier_details:
                                            shipment["destination_state"] = {"value": carrier_details["destination_state"], "explanation": "Extracted from Averitt top-crop image", "confidence": 1.0}
                                        if "destination_address" in carrier_details:
                                            shipment["destination_address"] = {"value": carrier_details["destination_address"], "explanation": "Extracted from Averitt top-crop image", "confidence": 1.0}
                                        
                                        logger.info(f"Successfully overridden source/destination details for Averitt Express")
                            
                            # Apply canonical mapping to Averitt-extracted values — but ONLY for
                            # shipments where source_name / destination_name was NOT already
                            # canonical (those were preserved above and must not be re-matched
                            # against the image-extraction values, which may belong to a different
                            # page/section than the BOL that produced the original canonical match).
                            source_name_raw = carrier_details.get("source_name", "")
                            dest_name_raw = carrier_details.get("destination_name", "")
                            source_address_raw = carrier_details.get("source_address", "")
                            source_city_raw = carrier_details.get("source_city", "")
                            source_state_raw = carrier_details.get("source_state", "")
                            dest_address_raw = carrier_details.get("destination_address", "")
                            dest_city_raw = carrier_details.get("destination_city", "")
                            dest_state_raw = carrier_details.get("destination_state", "")
                            
                            # Check for canonical match in source using field-separated logic.
                            # City is now used ONLY paired with an address_city_pattern (address
                            # substring AND city exact match together) — city alone never matches.
                            source_canonical, source_variation = _canonical_match_with_fields(
                                name=source_name_raw,
                                address=source_address_raw,
                                city=source_city_raw,
                                state=source_state_raw
                            )
                            
                            # Check for canonical match in destination using field-separated logic.
                            dest_canonical, dest_variation = _canonical_match_with_fields(
                                name=dest_name_raw,
                                address=dest_address_raw,
                                city=dest_city_raw,
                                state=dest_state_raw
                            )
                            
                            if source_canonical or dest_canonical:
                                logger.info(f"Applying canonical mapping to Averitt-extracted values:")
                                if source_canonical:
                                    logger.info(f"  Source '{source_name_raw}' → canonical '{source_canonical}' (matched '{source_variation}')")
                                if dest_canonical:
                                    logger.info(f"  Destination '{dest_name_raw}' → canonical '{dest_canonical}' (matched '{dest_variation}')")
                                
                                # Apply canonical mapping to shipments — skip any shipment whose
                                # name was already canonical from Priority 1 (preserved above).
                                if "shipments" in extracted_info and extracted_info["shipments"]:
                                    for shipment in extracted_info["shipments"]:
                                        if isinstance(shipment, dict):
                                            if source_canonical and not _value_is_already_canonical(shipment.get("source_name")):
                                                shipment["source_name"] = {
                                                    "value": source_canonical,
                                                    "explanation": f"Canonical mapping applied to Averitt extraction: '{source_name_raw}' → '{source_canonical}' (matched '{source_variation}')",
                                                    "confidence": 1.0
                                                }
                                                shipment["source_code"] = {"value": source_canonical, "explanation": "Set from canonical source_name", "confidence": 1.0}
                                            if dest_canonical and not _value_is_already_canonical(shipment.get("destination_name")):
                                                shipment["destination_name"] = {
                                                    "value": dest_canonical,
                                                    "explanation": f"Canonical mapping applied to Averitt extraction: '{dest_name_raw}' → '{dest_canonical}' (matched '{dest_variation}')",
                                                    "confidence": 1.0
                                                }
                                                shipment["destination_code"] = {"value": dest_canonical, "explanation": "Set from canonical destination_name", "confidence": 1.0}
                            
                            # invoice_source_name / invoice_destination_name ALWAYS reflect the
                            # RAW image-extraction result (Priority 2 / audit trail), regardless
                            # of whether source_name/destination_name were preserved as canonical.
                            if "custom_fields" not in extracted_info:
                                extracted_info["custom_fields"] = {}
                            if "source_name" in carrier_details:
                                extracted_info["custom_fields"]["invoice_source_name"] = {
                                    "value": carrier_details["source_name"],
                                    "explanation": "Raw shipper name extracted from Averitt cropped image without any canonical mapping applied",
                                    "confidence": 1.0
                                }
                            if "destination_name" in carrier_details:
                                extracted_info["custom_fields"]["invoice_destination_name"] = {
                                    "value": carrier_details["destination_name"],
                                    "explanation": "Raw consignee name extracted from Averitt cropped image without any canonical mapping applied",
                                    "confidence": 1.0
                                }
                            logger.info(f"Updated invoice_source_name='{carrier_details.get('source_name')}' and invoice_destination_name='{carrier_details.get('destination_name')}' from Averitt extraction")
                    except Exception as carrier_error:
                        logger.warning(f"Failed to extract Averitt Express shipper/consignee: {str(carrier_error)}, continuing with Claude extraction")
                else:
                    # Standard keyword-based extraction for other carriers
                    logger.info(f"Step 2.5: Extracting enhanced source/destination details for {carrier_name} (normalized: {normalized_carrier_name})")
                    try:
                        carrier_details = extract_carrier_source_destination(
                            input_bucket, 
                            input_key,
                            carrier_name,
                            config["start_keywords"],
                            config["end_keywords"]
                        )
                        if carrier_details:
                            logger.info(f"Overriding source/destination with {carrier_name} extraction:\n{_format_json_for_log(carrier_details)}")
                            
                            # Override shipment details with extracted values.
                            #
                            # SELECTIVE OVERRIDE RULE (Priority 1 preservation):
                            # If the LLM already resolved source_name / destination_name to a
                            # canonical code (via the BOL-textract Priority 1 instructions in the
                            # prompt), do NOT overwrite that name with the image-extraction result
                            # or re-run canonical matching against it. Only fill in address/city/
                            # state from the image extraction. If the LLM did NOT find a canonical
                            # match (Priority 2 fallback), override the name and attempt canonical
                            # matching on the image-extracted value as before.
                            if "shipments" in extracted_info and extracted_info["shipments"]:
                                for shipment in extracted_info["shipments"]:
                                    if isinstance(shipment, dict):
                                        source_already_canonical = _value_is_already_canonical(shipment.get("source_name"))
                                        dest_already_canonical = _value_is_already_canonical(shipment.get("destination_name"))

                                        # Source fields
                                        if source_already_canonical:
                                            logger.info(
                                                f"  source_name already canonical ('{shipment.get('source_name')}') — "
                                                "keeping it, only syncing address/city/state from cropped-section extraction"
                                            )
                                        elif "source_name" in carrier_details:
                                            shipment["source_name"] = {"value": carrier_details["source_name"], "explanation": "Extracted from cropped section", "confidence": 1.0}
                                        if "source_city" in carrier_details:
                                            shipment["source_city"] = {"value": carrier_details["source_city"], "explanation": "Extracted from cropped section", "confidence": 1.0}
                                        if "source_state" in carrier_details:
                                            shipment["source_state"] = {"value": carrier_details["source_state"], "explanation": "Extracted from cropped section", "confidence": 1.0}
                                        if "source_address" in carrier_details:
                                            shipment["source_address"] = {"value": carrier_details["source_address"], "explanation": "Extracted from cropped section", "confidence": 1.0}
                                        
                                        # Destination fields
                                        if dest_already_canonical:
                                            logger.info(
                                                f"  destination_name already canonical ('{shipment.get('destination_name')}') — "
                                                "keeping it, only syncing address/city/state from cropped-section extraction"
                                            )
                                        elif "destination_name" in carrier_details:
                                            shipment["destination_name"] = {"value": carrier_details["destination_name"], "explanation": "Extracted from cropped section", "confidence": 1.0}
                                        if "destination_city" in carrier_details:
                                            shipment["destination_city"] = {"value": carrier_details["destination_city"], "explanation": "Extracted from cropped section", "confidence": 1.0}
                                        if "destination_state" in carrier_details:
                                            shipment["destination_state"] = {"value": carrier_details["destination_state"], "explanation": "Extracted from cropped section", "confidence": 1.0}
                                        if "destination_address" in carrier_details:
                                            shipment["destination_address"] = {"value": carrier_details["destination_address"], "explanation": "Extracted from cropped section", "confidence": 1.0}
                                        
                                        # Apply canonical mapping to the extracted source/destination using NEW field-separated logic —
                                        # but ONLY when the name was NOT already canonical (Priority 1 preserved above).
                                        source_name_val = carrier_details.get("source_name", "")
                                        source_address_val = carrier_details.get("source_address", "")
                                        source_city_val = carrier_details.get("source_city", "")
                                        source_state_val = carrier_details.get("source_state", "")
                                        
                                        dest_name_val = carrier_details.get("destination_name", "")
                                        dest_address_val = carrier_details.get("destination_address", "")
                                        dest_city_val = carrier_details.get("destination_city", "")
                                        dest_state_val = carrier_details.get("destination_state", "")
                                        
                                        if not source_already_canonical and (source_name_val or source_address_val):
                                            canonical_source, source_variation = _canonical_match_with_fields(
                                                name=source_name_val,
                                                address=source_address_val,
                                                city=source_city_val,
                                                state=source_state_val
                                            )
                                            if canonical_source:
                                                logger.info(f"Canonical mapping: source '{source_name_val}' → '{canonical_source}' (matched '{source_variation}')")
                                                shipment["source_name"] = {
                                                    "value": canonical_source,
                                                    "explanation": f"Canonical location (matched '{source_variation}')",
                                                    "confidence": 1.0
                                                }
                                        
                                        if not dest_already_canonical and (dest_name_val or dest_address_val):
                                            canonical_dest, dest_variation = _canonical_match_with_fields(
                                                name=dest_name_val,
                                                address=dest_address_val,
                                                city=dest_city_val,
                                                state=dest_state_val
                                            )
                                            if canonical_dest:
                                                logger.info(f"Canonical mapping: destination '{dest_name_val}' → '{canonical_dest}' (matched '{dest_variation}')")
                                                shipment["destination_name"] = {
                                                    "value": canonical_dest,
                                                    "explanation": f"Canonical location (matched '{dest_variation}')",
                                                    "confidence": 1.0
                                                }
                                        
                                        logger.info(f"Successfully overridden source/destination details for {carrier_name}")
                            
                            # Also update invoice_source_name and invoice_destination_name in custom_fields
                            if "custom_fields" not in extracted_info:
                                extracted_info["custom_fields"] = {}
                            if "source_name" in carrier_details:
                                extracted_info["custom_fields"]["invoice_source_name"] = {
                                    "value": carrier_details["source_name"],
                                    "explanation": f"Raw shipper name extracted from {carrier_name} cropped image without any canonical mapping applied",
                                    "confidence": 1.0
                                }
                            if "destination_name" in carrier_details:
                                extracted_info["custom_fields"]["invoice_destination_name"] = {
                                    "value": carrier_details["destination_name"],
                                    "explanation": f"Raw consignee name extracted from {carrier_name} cropped image without any canonical mapping applied",
                                    "confidence": 1.0
                                }
                            logger.info(f"Updated invoice_source_name='{carrier_details.get('source_name')}' and invoice_destination_name='{carrier_details.get('destination_name')}' from {carrier_name} extraction")
                    except Exception as carrier_error:
                        logger.warning(f"Failed to extract {carrier_name} source/destination: {str(carrier_error)}, continuing with Claude extraction")
            else:
                if carrier_name:
                    logger.info(f"Carrier '{carrier_name}' (normalized: '{normalized_carrier_name}') not in extraction config or pymupdf (fitz) not available")
                else:
                    logger.info("No carrier name available for source/destination extraction")
            
            # Step 2.6: Override bill to address for carriers that need cropped extraction
            logger.info(f"Step 2.6: Checking if carrier '{carrier_name}' needs bill to address extraction")
            # Define carrier-specific header keywords for bill to address
            # Start header: "SHIP DATE", End header: "Trailer No."
            bill_to_extraction_config = {
                "Buddy Moore Trucking": {
                    "start_keywords": ["SHIP DATE", "SHIP DATE:"],
                    "end_keywords": ["Trailer No.", "Trailer No", "TRAILER NO.", "TRAILER NO"]
                },
                "M&M Cartage": {
                    "start_keywords": ["SHIP DATE", "SHIP DATE:"],
                    "end_keywords": ["Trailer No.", "Trailer No", "TRAILER NO.", "TRAILER NO"]
                },
                "M&M Cartage Co. Inc.": {
                    "start_keywords": ["SHIP DATE", "SHIP DATE:"],
                    "end_keywords": ["Trailer No.", "Trailer No", "TRAILER NO.", "TRAILER NO"]
                }
            }
            
            # Use the same normalized carrier name from source/destination extraction
            if normalized_carrier_name and normalized_carrier_name in bill_to_extraction_config:
                config = bill_to_extraction_config[normalized_carrier_name]
                logger.info(f"Step 2.6: Extracting enhanced bill to address details for {carrier_name} (normalized: {normalized_carrier_name})")
                try:
                    bill_to_details = extract_carrier_bill_to_address(
                        input_bucket, 
                        input_key,
                        carrier_name,
                        config["start_keywords"],
                        config["end_keywords"]
                    )
                    if bill_to_details:
                        logger.info(f"Overriding bill to address with {carrier_name} extraction:\n{_format_json_for_log(bill_to_details)}")
                        # Override bill to fields in extracted_info
                        if "bill_to_name" in bill_to_details:
                            extracted_info["bill_to_name"] = {"value": bill_to_details["bill_to_name"], "explanation": "Extracted from cropped section", "confidence": 1.0}
                        if "bill_to_address" in bill_to_details:
                            extracted_info["bill_to_address"] = {"value": bill_to_details["bill_to_address"], "explanation": "Extracted from cropped section", "confidence": 1.0}
                        
                        logger.info(f"Successfully overridden bill to address details for {carrier_name}")
                except Exception as bill_to_error:
                    logger.warning(f"Failed to extract {carrier_name} bill to address: {str(bill_to_error)}, continuing with Claude extraction")
            else:
                if carrier_name:
                    logger.info(f"Carrier '{carrier_name}' (normalized: '{normalized_carrier_name}') not in bill to address extraction config or pymupdf (fitz) not available")
                else:
                    logger.info("No carrier name available for bill to address extraction")
                
            # Post-process: apply canonical location mapping for Road carriers.
            # Runs AFTER Step 2.5/2.6 carrier-specific overrides so canonical values
            # always take final precedence over any vision-model extraction.
            try:
                freight_kvs_flat = {}
                if pre_extracted_kvs:
                    for pg_kvs in (pre_extracted_kvs or {}).values():
                        if isinstance(pg_kvs, dict):
                            freight_kvs_flat.update(pg_kvs)
                extracted_info = _apply_canonical_location_mapping(
                    extracted_info,
                    freight_kvs=freight_kvs_flat,
                    bol_lines_by_page=bol_page_textract_lines or {},
                    bol_kvs_by_page=bol_page_textract_kvs or {},
                )
            except Exception as _clm_err:
                logger.warning(f"Canonical location mapping post-processing failed: {_clm_err}")

            # Calculate average confidence score
            average_confidence = calculate_weighted_confidence(extracted_info)
            logger.info(f"Average confidence score: {average_confidence:.4f}")
            
            # Prepare extracted fields array (includes field confidence scores)
            extracted_fields_array = prepare_extracted_fields_array(extracted_info)
            logger.info(f"Prepared {len(extracted_fields_array)} extracted fields with values")
            
            # Check for low confidence fields (70-89%) and send Slack notification
            try:
                from slack_notifier import notify_low_confidence_invoice
                
                low_confidence_fields = []
                
                # Check each extracted field for confidence between 70-89%
                for field_entry in extracted_fields_array:
                    field_confidence = field_entry.get("confidence")
                    if field_confidence is not None and 70 <= field_confidence < 90:
                        low_confidence_fields.append({
                            "field_name": field_entry.get("field_name", "unknown"),
                            "value": field_entry.get("value", "N/A"),
                            "confidence": field_confidence,
                            "explanation": field_entry.get("explanation", "No explanation provided")
                        })
                
                # If any fields have 70-89% confidence, send Slack notification
                if low_confidence_fields:
                    logger.info(f"⚠️ Found {len(low_confidence_fields)} field(s) with confidence 70-89%, sending Slack notification")
                    
                    _inv_num = extracted_info.get("invoiceNumber", {})
                    if isinstance(_inv_num, dict):
                        _inv_num = _inv_num.get("value", "N/A")
                    
                    _sender_email = email_details.get("to", "") or email_details.get("from", "")
                    _s3_path = f"s3://{original_input_bucket}/{original_input_key}" if original_input_bucket and original_input_key else None
                    _filename = email_details.get("filename", "")
                    
                    notify_low_confidence_invoice(
                        invoice_number=str(_inv_num),
                        email_id=email_id,
                        attachment_id=attachment_id,
                        filename=_filename,
                        sender_email=_sender_email,
                        low_confidence_fields=low_confidence_fields,
                        s3_path=_s3_path
                    )
                    logger.info("✅ Low confidence notification sent to Slack")
                else:
                    logger.info(f"✓ All fields have confidence ≥90% or <70%, no Slack notification needed")
            
            except ImportError:
                logger.warning("⚠️ slack_notifier module not found, skipping low confidence check")
            except Exception as slack_err:
                logger.error(f"❌ Failed to send Slack low confidence notification: {slack_err}", exc_info=True)

            # ── TRANSACTION PAYLOAD: invoice_processing (extraction success) ──────
            _proc_payload = _build_transaction_payload(
                transaction_id=email_id,
                step_key="INVOICE_PROCESSING",
                transaction_status="PROCESSING",
                step_status="SUCCESS",
                email_details=email_details,
                attachment_id=attachment_id,
                entity_uri=_tracker_entity_uri,
                entity_type="freight_invoice",
                step_started_at=_processing_started_at,
                step_completed_at=datetime.now().isoformat(),
            )
            _save_payload_to_tracker(email_id, "INVOICE_PROCESSING", _proc_payload)
            logger.info("TRANSACTION_PAYLOAD | invoice_processing | %s", json.dumps(_proc_payload))
            _post_transaction_payload(_proc_payload, "invoice_processing")
            # ─────────────────────────────────────────────────────────────────────

        except Exception as e:
            # Pass any confidence score we managed to calculate before the error
            confidence_to_store = average_confidence if average_confidence is not None else None

            # ── TRANSACTION PAYLOAD: invoice_processing (extraction failure) ──────
            _proc_err_payload = _build_transaction_payload(
                transaction_id=email_id,
                step_key="INVOICE_PROCESSING",
                transaction_status="FAILED",
                step_status="FAILED",
                email_details=email_details,
                attachment_id=attachment_id,
                entity_uri=_tracker_entity_uri,
                entity_type="freight_invoice",
                step_error_code="EXTRACTION_ERROR",
                step_error_message=str(e),
                step_started_at=_processing_started_at,
                step_completed_at=datetime.now().isoformat(),
            )
            _save_payload_to_tracker(email_id, "INVOICE_PROCESSING_error", _proc_err_payload)
            logger.info("TRANSACTION_PAYLOAD | invoice_processing (error) | %s", json.dumps(_proc_err_payload))
            _post_transaction_payload(_proc_err_payload, "invoice_processing (error)")
            # ─────────────────────────────────────────────────────────────────────
            
            update_attachment_status(
                dynamodb_client,
                DYNAMODB_TABLE,
                email_id,
                attachment_id,
                'FAILED',
                error={
                    'message': str(e),
                    'error_code': 'EXTRACTION_ERROR'
                },
                extraction_failed=1,  # Add extraction failure flag
                missing_fields=[],     # Empty array for missing fields
                confidence_score=confidence_to_store,  # Store confidence if available
                extracted_fields=extracted_fields_array,  # Store extracted fields array if available
                payment_terms=None   # No payment terms available at this stage
            )
            raise
        
        # Log the extracted information for review
        logger.info("Extracted Information from LLM successfully")
        custom_customer = extracted_info.pop("customer", None)
        
        # Step 3.5: Validate payment due date
        logger.info("Step 3.5: Validating payment due date")
        payment_due_date_validation = validate_payment_due_date(extracted_info)
        
        if not payment_due_date_validation['is_valid']:
            logger.warning(f"Payment due date validation failed: {payment_due_date_validation['errors']}")
            # Log the validation errors for debugging
            for error in payment_due_date_validation['errors']:
                logger.warning(f"Payment due date validation error: {error['type']} - {error['message']}")
        else:
            logger.info("Payment due date validation passed")
        
        # Log auto-correction if it occurred
        if payment_due_date_validation.get('auto_corrected', False):
            correction_info = payment_due_date_validation.get('correction_info', {})
            logger.info(f"Payment due date auto-corrected: {correction_info.get('original_due_date')} -> {correction_info.get('corrected_due_date')} (+{correction_info.get('days_added')} days for {correction_info.get('carrier')})")
        
        # Ensure shipments array exists and has at least one item
        if "shipments" not in extracted_info or not extracted_info["shipments"]:
            extracted_info["shipments"] = [{
                "shipment_number": {"value": "", "explanation": "No shipment number found", "confidence": 0.0},
                "charges": []
            }]
        
        # Add default country values if missing
        for shipment in extracted_info["shipments"][0]:
            if isinstance(shipment, dict):
                if "source_country" not in shipment:
                    shipment["source_country"] = "USA"
                if "destination_country" not in shipment:
                    shipment["destination_country"] = "USA"
                
                # Remove optional fields that might cause issues
                for field in ["source_zip_code", "destination_zip_code"]:
                    if field in shipment:
                        shipment.pop(field, None)
        
        # Define schema for field type validation
        schema = {
            # Top-level fields
            "invoice_number": "string",
            "invoice_date": "string",
            "payment_due_date": "string",
            "vendor_reference_id": "string",
            "bill_of_lading_number": "string",
            "bill_of_entry_number": "string",
            "currency": "string",
            "total_invoice_value": "number",
            "po_number": "string",
            "assessable_value": "double",
            "proforma_invoice_creation_date": "string",
            "net_invoice_value": "number",
            "round_off": "number",
            "rate_applicability_date": "string",
            "arrival_date": "string",
            "departure_date": "string",
            "airway_bill_number": "string",
            "hsn_number": "string",
            "documents_attachment": "array",

            # Shipments level
            "shipment_number": "string",
            "consignee_name": "string",
            "shipment_tracking_number": "string",
            "stopover_location": "string",
            "dangerous_goods_indicator": "boolean",
            "thu_type": "string",
            "thu_name": "string",
            "service_level": "string",
            "delivery_type": "string",
            "number_of_pallets": "number",
            "pro_number": "string",
            "shipment_creation_date": "string",
            "mode": "string",
            "source_name": "string",
            "source_city": "string",
            "source_country": "string",
            "source_state": "string",
            "source_country_code": "string",
            "source_code": "string",
            "source_address": "string",
            "source_province": "string",
            "source_region": "string",
            "port_of_loading": "string",
            "destination_name": "string",
            "destination_code": "string",
            "destination_city": "string",
            "destination_country": "string",
            "destination_state": "string",
            "destination_country_code": "string",
            "destination_address": "string",
            "destination_province": "string",
            "destination_region": "string",
            "port_of_discharge": "string",
            "shipment_weight": "number",
            "shipment_volume": "number",
            "shipment_weight_uom": "string",
            "shipment_volume_uom": "string",
            "shipment_total_value": "number",

            # Container level
            "container_id": "number",
            "container_number": "string",
            "container_type": "string",
            "no_of_containers": "number",
            "container_weight": "number",
            "container_weight_uom": "string",

            # Charges level
            "charge_code": "string",
            "charge_name": "string",
            "charge_gross_amount": "number",
            "currency": "string",  # Inside charges
            "tariff_rate": "number",
            "tariff_qty": "number",
            "tariff_uom": "string",
            "tariff_description": "string",
        }

        
        
        # Step 3.5: Business Rules Validation (AP 10, Forms, Location Restrictions)
        logger.info("Step 3.5: Running business rules validation")
        
        # Initialize pay_as_present flag (default False for all carriers)
        pay_as_present = False
        should_reject = False
        rejection_reason = []
        
        # Get raw text for pattern matching (raw_text already includes BOL page data if available)
        raw_text_combined = raw_text or ""
        raw_text_upper = raw_text_combined.upper()
        
        # Get BOL status (check if bill_of_lading_number is empty)
        bol_number = extracted_info.get("bill_of_lading_number", {})
        if isinstance(bol_number, dict):
            bol_number = bol_number.get("value", "")
        bol_missing = not bool(str(bol_number or "").strip())
        logger.info(f"BOL status: {'missing' if bol_missing else 'present'} (bill_of_lading_number='{bol_number}')")
        
        # ===== TASK 1: Reject if AP 10 or Bldg 10 found =====
        # Search case-insensitively with or without spaces
        ap10_patterns = ["AP 10", "AP10", "BLDG 10", "BLDG10", "BUILDING 10", "BUILDING10"]
        ap10_found = any(pattern in raw_text_upper for pattern in ap10_patterns)
        
        if ap10_found:
            should_reject = True
            rejection_reason.append("Submit Service Parts invoices to ITS")
            pay_as_present = False
            logger.warning("VALIDATION | Task 1: AP 10/Bldg 10 found - rejecting invoice")
        
        # ===== TASK 2: AP 1 + Form 1 (BOL missing) =====
        if not should_reject:
            ap1_patterns = ["AP 1", "AP1"]
            ap1_found = any(pattern in raw_text_upper for pattern in ap1_patterns)
            form1_patterns = ["FORM 1", "FORM1"]
            form1_found = any(pattern in raw_text_upper for pattern in form1_patterns)
            
            if ap1_found and bol_missing and form1_found:
                pay_as_present = True
                logger.info("VALIDATION | Task 2: AP 1 + Form 1 found with BOL missing - setting pay_as_present=True")
            elif ap1_found and bol_missing and not form1_found:
                pay_as_present = False
                logger.info("VALIDATION | Task 2: AP 1 found with BOL missing but Form 1 NOT found - setting pay_as_present=False")
        
        # ===== TASK 3: AP5/MRO/RPF/AP3 + Form (BOL missing) =====
        if not should_reject and not pay_as_present:
            location_patterns = {
                "AP5": ["AP 5", "AP5"],
                "MRO": ["MRO"],
                "RPF": ["RPF"],
                "AP3": ["AP 3", "AP3"]
            }
            
            for location_name, patterns in location_patterns.items():
                location_found = any(pattern in raw_text_upper for pattern in patterns)
                if location_found:
                    # Check for form with this location name
                    form_patterns = [f"{location_name} FORM", f"{location_name}FORM", f"FORM {location_name}", f"FORM{location_name}"]
                    form_found = any(pattern in raw_text_upper for pattern in form_patterns)
                    
                    if bol_missing and form_found:
                        pay_as_present = True
                        logger.info(f"VALIDATION | Task 3: {location_name} + Form found with BOL missing - setting pay_as_present=True")
                        break
                    elif bol_missing and not form_found:
                        pay_as_present = False
                        logger.info(f"VALIDATION | Task 3: {location_name} found with BOL missing but Form NOT found - setting pay_as_present=False")
        
        # ===== TASK 4: Reject LTL with restricted Jeffersonville/Charlestown locations =====
        if not should_reject:
            # Task 4: Check for restricted locations (applies to ALL service levels: LTL, TL, etc.)
            logger.info("VALIDATION | Task 4: Checking for restricted locations (all service levels)")
            
            shipments = extracted_info.get("shipments", [])
            
            # Define restricted locations with exact match criteria
            restricted_locations = [
                {
                    "name": ["GE APPLIANCES", "GE APPLIANCE"],
                    "address": "201 PAUL GARRETT AVE",
                    "city": "JEFFERSONVILLE",
                    "state": "IN",
                    "zip": "47130"
                },
                {
                    "name": ["GENERAL ELECTRIC", "PDC", "GE PARTS", "GEA PARTS", "GE PART"],  # Expanded name variations
                    "address": "1251 PORT RD",
                    "city": "JEFFERSONVILLE",
                    "state": "IN",
                    "zip": "47130"
                },
                {
                    "name": ["RIVER RIDGE PDC", "RIVER RIDGE"],
                    "address": "201 PAUL GARRETT AVE",
                    "city": "CHARLESTOWN",
                    "state": "IN",
                    "zip": "47111"
                }
            ]
            
            # Helper function to check if location matches
            def location_matches(loc_name, loc_address, loc_city, loc_state, restricted):
                    # Normalize for comparison
                    loc_name_upper = str(loc_name or "").upper().strip()
                    loc_address_upper = str(loc_address or "").upper().strip()
                    loc_city_upper = str(loc_city or "").upper().strip()
                    loc_state_upper = str(loc_state or "").upper().strip()
                    
                    # Additional normalization: remove trailing periods and common punctuation
                    # This handles cases like "1251 Port Rd." vs "1251 PORT RD"
                    loc_address_upper = loc_address_upper.rstrip('.')
                    restricted_address_upper = restricted["address"].upper().strip().rstrip('.')
                    
                    # Prepare restricted name variations for matching
                    restricted_names = restricted["name"] if isinstance(restricted["name"], list) else [restricted["name"]]
                    
                    # STEP 1: Exact Match - Complete address including name + street + city + state
                    # Name must be EXACT match (not substring)
                    exact_name_match = any(rname.upper() == loc_name_upper for rname in restricted_names)
                    exact_address_match = restricted_address_upper == loc_address_upper
                    exact_city_match = restricted["city"].upper() == loc_city_upper
                    exact_state_match = restricted["state"].upper() == loc_state_upper
                    
                    step1_match = exact_name_match and exact_address_match and exact_city_match and exact_state_match
                    
                    if step1_match:
                        logger.info(f"VALIDATION | Task 4: STEP 1 (Exact Match) - Name '{loc_name_upper}' + Address '{loc_address_upper}, {loc_city_upper}, {loc_state_upper}'")
                        return True
                    
                    # STEP 2: Partial Match (MAIN LOGIC) - Lines 2-3 only (street + city + state)
                    # Match address + city + state even if name is different
                    # This catches cases like "GE Port" vs "GE APPLIANCES" where physical location is the same
                    step2_address_match = restricted_address_upper == loc_address_upper
                    step2_city_match = restricted["city"].upper() == loc_city_upper
                    step2_state_match = restricted["state"].upper() == loc_state_upper
                    
                    step2_match = step2_address_match and step2_city_match and step2_state_match
                    
                    if step2_match:
                        logger.info(f"VALIDATION | Task 4: STEP 2 (Partial Match - Lines 2-3) - Name '{loc_name_upper}' at restricted address '{loc_address_upper}, {loc_city_upper}, {loc_state_upper}'")
                        return True
                    
                    return False
            
            # Check each shipment's source and destination
            if shipments and isinstance(shipments, list):
                for shipment in shipments:
                    if not isinstance(shipment, dict):
                        continue
                    
                    # Extract source fields
                    source_name = shipment.get("source_name", {})
                    if isinstance(source_name, dict):
                        source_name = source_name.get("value", "")
                    source_address = shipment.get("source_address", {})
                    if isinstance(source_address, dict):
                        source_address = source_address.get("value", "")
                    source_city = shipment.get("source_city", {})
                    if isinstance(source_city, dict):
                        source_city = source_city.get("value", "")
                    source_state = shipment.get("source_state", {})
                    if isinstance(source_state, dict):
                        source_state = source_state.get("value", "")
                    
                    # Extract destination fields
                    dest_name = shipment.get("destination_name", {})
                    if isinstance(dest_name, dict):
                        dest_name = dest_name.get("value", "")
                    dest_address = shipment.get("destination_address", {})
                    if isinstance(dest_address, dict):
                        dest_address = dest_address.get("value", "")
                    dest_city = shipment.get("destination_city", {})
                    if isinstance(dest_city, dict):
                        dest_city = dest_city.get("value", "")
                    dest_state = shipment.get("destination_state", {})
                    if isinstance(dest_state, dict):
                        dest_state = dest_state.get("value", "")
                    
                    # Check if source or destination matches restricted locations
                    for restricted in restricted_locations:
                        if location_matches(source_name, source_address, source_city, source_state, restricted):
                            should_reject = True
                            rejection_reason.append("Submit Finished Goods Invoices to ITS")
                            pay_as_present = False
                            logger.warning(f"VALIDATION | Task 4: Restricted source location found - {restricted['name']}")
                            break
                        if location_matches(dest_name, dest_address, dest_city, dest_state, restricted):
                            should_reject = True
                            rejection_reason.append("Submit Finished Goods Invoices to ITS")
                            pay_as_present = False
                            logger.warning(f"VALIDATION | Task 4: Restricted destination location found - {restricted['name']}")
                            break
                    
                    if should_reject:
                        break
        
        # Add pay_as_present to custom fields
        if "custom" not in extracted_info:
            extracted_info["custom"] = {}
        extracted_info["custom"]["pay_as_present"] = pay_as_present
        logger.info(f"VALIDATION | Final pay_as_present={pay_as_present}")
        
        # If should_reject, call rejection endpoint and stop processing
        if should_reject:
            logger.error(f"VALIDATION | Invoice rejected: {'; '.join(rejection_reason)}")
            
            # Flatten data for rejection payload
            try:
                flattened_data = flatten_structured_output(extracted_info)
            except Exception as flatten_err:
                logger.error(f"Failed to flatten data for rejection: {flatten_err}")
                flattened_data = {}
            
            # Call rejection endpoint
            if REJECTION_ENDPOINT and isinstance(flattened_data, dict):
                try:
                    _rej_shipments = flattened_data.get("shipments")
                    _rej_mode = ""
                    if isinstance(_rej_shipments, list) and _rej_shipments and isinstance(_rej_shipments[0], dict):
                        _rej_mode = _rej_shipments[0].get("mode") or ""
                    
                    _rej_file_name = email_details.get("filename") or ""
                    if not _rej_file_name and original_input_key:
                        _rej_file_name = original_input_key.split("/")[-1]
                    
                    _rej_total = flattened_data.get("total_invoice_value") or 0
                    try:
                        _rej_total = float(_rej_total)
                        if isinstance(_rej_total, float) and _rej_total.is_integer():
                            _rej_total = int(_rej_total)
                    except (TypeError, ValueError):
                        _rej_total = 0
                    
                    _rej_inv_no = str(flattened_data.get("invoice_number") or "NA").strip() or "NA"
                    rejection_payload = {
                        "data": {
                            "transaction_id": email_id,
                            "invoice_number": _rej_inv_no,
                            "vendor_reference_id": flattened_data.get("vendor_reference_id") or "",
                            "invoice_amount": _rej_total,
                            "invoice_currency": (flattened_data.get("currency") or "").lower(),
                            "payment_due_date": flattened_data.get("payment_due_date") or "",
                            "mode": _rej_mode,
                            "reason": rejection_reason,
                            "attachment_bucket": original_input_bucket or "",
                            "file_name": _rej_file_name,
                            "file_key": original_input_key or "",
                            "file_type": (_rej_file_name.rsplit(".", 1)[-1].lower() if "." in _rej_file_name else "pdf"),
                        }
                    }
                    logger.info("REJECTION_API | business_rules payload: %s", json.dumps(rejection_payload))
                    
                    _rej_resp = requests.post(
                        REJECTION_ENDPOINT,
                        headers={"Content-Type": "application/json", "internal-token": INTERNAL_TOKEN},
                        data=json.dumps(rejection_payload),
                        timeout=10,
                    )
                    rejection_response = {
                        "status_code": _rej_resp.status_code,
                        "success": _rej_resp.status_code == 200,
                        "timestamp": datetime.now().isoformat(),
                        "body": _rej_resp.text,
                    }
                    logger.info("REJECTION_API | business_rules response: %s", json.dumps(rejection_response))
                except Exception as _rej_err:
                    logger.error("REJECTION_API | business_rules call failed: %s", _rej_err, exc_info=True)
            
            # Send Slack notification for rejection
            try:
                from slack_notifier import notify_invoice_rejection
                
                # Build rejection details
                _rej_shipments = flattened_data.get("shipments", [])
                rejection_details = {}
                if isinstance(_rej_shipments, list) and _rej_shipments and isinstance(_rej_shipments[0], dict):
                    _first_ship = _rej_shipments[0]
                    rejection_details = {
                        "Source": f"{_first_ship.get('source_name', '')}",
                        "Source Address": f"{_first_ship.get('source_address', '')}, {_first_ship.get('source_city', '')}, {_first_ship.get('source_state', '')} {_first_ship.get('source_zip', '')}".strip(),
                        "Destination": f"{_first_ship.get('destination_name', '')}",
                        "Destination Address": f"{_first_ship.get('destination_address', '')}, {_first_ship.get('destination_city', '')}, {_first_ship.get('destination_state', '')} {_first_ship.get('destination_zip', '')}".strip(),
                        "Mode": _first_ship.get('mode', ''),
                        "Service Level": _first_ship.get('service_level', '')
                    }
                
                # Get file details
                _sender_email = email_details.get("to", "") or email_details.get("from", "")
                _s3_path = f"s3://{original_input_bucket}/{original_input_key}" if original_input_bucket and original_input_key else None
                
                notify_invoice_rejection(
                    invoice_number=_rej_inv_no,
                    email_id=email_id,
                    attachment_id=attachment_id,
                    filename=_rej_file_name,
                    sender_email=_sender_email,
                    rejection_reason="; ".join(rejection_reason),
                    rejection_details=rejection_details,
                    s3_path=_s3_path
                )
                logger.info("✅ Rejection notification sent to Slack")
            except ImportError:
                logger.warning("⚠️ slack_notifier module not found, skipping Slack notification")
            except Exception as slack_err:
                logger.error(f"❌ Failed to send Slack rejection notification: {slack_err}", exc_info=True)
            
            # Update status tracker and return rejection response
            update_attachment_status(
                dynamodb_client,
                DYNAMODB_TABLE,
                email_id,
                attachment_id,
                "FAILED",
                error={'message': '; '.join(rejection_reason), 'error_code': 'BUSINESS_RULES_VALIDATION_FAILED'}
            )
            
            return {
                'statusCode': 422,
                'body': json.dumps({
                    'message': 'Invoice rejected: business rules validation failed',
                    'reason': rejection_reason,
                    'pay_as_present': pay_as_present
                })
            }
        
        logger.info("VALIDATION | All business rules passed - proceeding with normal processing")
        
        
        # Step 4: Format the extracted information as HTML
        logger.info("Step 4: Formatting data as HTML")
        logger.info(f"Extracted info:\n{_format_json_for_log(extracted_info)}")
        try:
            flattened_data = flatten_structured_output(extracted_info)
            logger.info(f"Flattened data:\n{_format_json_for_log(flattened_data)}")
            
            # Removed carrier-specific charge consolidation and CASS location matching
            
            html_content = format_invoice_data_as_html(flattened_data)
            logger.info("Formatted data as HTML")
            if not html_content:
                error_msg = "Failed to format data as HTML"
                update_attachment_status(
                    dynamodb_client,
                    DYNAMODB_TABLE,
                    email_id,
                    attachment_id,
                    'FAILED',
                    error={
                        'message': error_msg,
                        'error_code': 'FORMAT_ERROR'
                    },
                    format_failed=1,           # Add format failure flag
                    missing_fields=[],         # Empty array for missing fields
                    confidence_score=average_confidence,  # Include confidence score even though formatting failed
                    extracted_fields=extracted_fields_array,  # Include extracted fields array
                    api_payload=None,  # No API payload at format stage
                    payment_terms=validate_payment_terms(flattened_data.get('payment_terms'))  # Include payment terms with validation
                )
                raise ValueError(error_msg)
        except Exception as e:
            logger.error(f"Error formatting data: {str(e)}")
            # Create a basic HTML representation even if formatting failed
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto;">
                <h2>Invoice Details</h2>
                <p style="color: red;">Error formatting full invoice data. Basic details are shown below.</p>
                <table style="width: 100%; border-collapse: collapse;">
                    <tr>
                        <th style="text-align: left; padding: 8px; border: 1px solid #ddd;">Field</th>
                        <th style="text-align: left; padding: 8px; border: 1px solid #ddd;">Value</th>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Invoice Number</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{extracted_info.get('invoice_number', {}).get('value', 'N/A')}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border: 1px solid #ddd;"><strong>Invoice Date</strong></td>
                        <td style="padding: 8px; border: 1px solid #ddd;">{extracted_info.get('invoice_date', {}).get('value', 'N/A')}</td>
                    </tr>
                </table>
                <p>For complete details, please check the system output.</p>
            </div>
            """
            # Continue with the process despite formatting issues
            logger.info("Created fallback HTML content for email")
        
        # ── CHARGE TOTAL VALIDATION + RETRY ──────────────────────────────────────
        _MAX_CHARGE_RETRIES = 5
        _charge_mismatch = False
        _charge_val_started_at = datetime.now().isoformat()

        def _sum_charges(_fd):
            _total = 0.0
            for _s in (_fd.get("shipments") or []):
                if isinstance(_s, dict):
                    for _c in (_s.get("charges") or []):
                        if isinstance(_c, dict):
                            _amt = _c.get("charge_gross_amount")
                            try:
                                _total += float(_amt) if _amt is not None else 0.0
                            except (TypeError, ValueError):
                                pass
            return round(_total, 2)

        _tiv_raw = flattened_data.get("total_invoice_value")
        try:
            _tiv = float(_tiv_raw) if _tiv_raw is not None else None
        except (TypeError, ValueError):
            _tiv = None

        if _tiv is not None:
            _charges_sum = _sum_charges(flattened_data)
            if _tiv != _charges_sum:
                _charge_mismatch = True
                logger.warning(
                    "CHARGE_VALIDATION | MISMATCH detected — total_invoice_value=%.2f, charges_sum=%.2f, starting %d retries",
                    _tiv, _charges_sum, _MAX_CHARGE_RETRIES
                )
                for _retry_num in range(1, _MAX_CHARGE_RETRIES + 1):
                    _correction_hint = (
                        f"\n\nCRITICAL CORRECTION REQUIRED (Attempt {_retry_num}/{_MAX_CHARGE_RETRIES}): "
                        f"Your previous extraction produced charge amounts that sum to {_charges_sum} "
                        f"but total_invoice_value = {_tiv}. These MUST be equal. "
                        f"Re-examine every charge line on the invoice carefully. "
                        f"Ensure no charge rows are missed, duplicated, or have incorrect amounts. "
                        f"If there is a discount row, subtract it from the base freight charge before reporting. "
                        f"The individual charge_gross_amount values MUST add up exactly to {_tiv}. "
                        f"Do NOT change total_invoice_value — only correct the charge amounts."
                    )
                    logger.info(
                        "CHARGE_VALIDATION | retry %d/%d — re-extracting with correction hint (total=%.2f, current_sum=%.2f)",
                        _retry_num, _MAX_CHARGE_RETRIES, _tiv, _charges_sum
                    )
                    extracted_info = extract_information_with_claude(
                        raw_text, carrier_name=carrier_name, charge_correction_hint=_correction_hint
                    )
                    flattened_data = flatten_structured_output(extracted_info)
                    _tiv_raw = flattened_data.get("total_invoice_value")
                    try:
                        _tiv = float(_tiv_raw) if _tiv_raw is not None else None
                    except (TypeError, ValueError):
                        _tiv = None
                    _charges_sum = _sum_charges(flattened_data)
                    if _tiv is None or _tiv == _charges_sum:
                        logger.info(
                            "CHARGE_VALIDATION | retry %d/%d result — total=%.2f, new_charges_sum=%.2f — MATCHED, continuing",
                            _retry_num, _MAX_CHARGE_RETRIES, _tiv or 0, _charges_sum
                        )
                        _charge_mismatch = False
                        average_confidence = calculate_weighted_confidence(extracted_info)
                        extracted_fields_array = prepare_extracted_fields_array(extracted_info)
                        break
                    logger.warning(
                        "CHARGE_VALIDATION | retry %d/%d result — total=%.2f, new_charges_sum=%.2f — still mismatch",
                        _retry_num, _MAX_CHARGE_RETRIES, _tiv or 0, _charges_sum
                    )
            else:
                logger.info(
                    "CHARGE_VALIDATION | PASSED — total_invoice_value=%.2f == charges_sum=%.2f",
                    _tiv, _charges_sum
                )

            if _charge_mismatch:
                _rej_reason = (
                    f"total_invoice_value ({_tiv}) does not equal the sum of all charge amounts ({_charges_sum}) "
                    f"after {_MAX_CHARGE_RETRIES} extraction attempts"
                )
                logger.error(
                    "CHARGE_VALIDATION | REJECTED after %d retries — total_invoice_value=%.2f != charges_sum=%.2f",
                    _MAX_CHARGE_RETRIES, _tiv or 0, _charges_sum
                )
                _charge_rej_payload = None
                _charge_rej_response = None
                if REJECTION_ENDPOINT and isinstance(flattened_data, dict):
                    try:
                        _rej_s = flattened_data.get("shipments")
                        _rej_mode = ""
                        if isinstance(_rej_s, list) and _rej_s and isinstance(_rej_s[0], dict):
                            _rej_mode = _rej_s[0].get("mode") or ""
                        _rej_file_name = email_details.get("filename") or ""
                        if not _rej_file_name and original_input_key:
                            _rej_file_name = original_input_key.split("/")[-1]
                        _rej_total = flattened_data.get("total_invoice_value") or 0
                        try:
                            _rej_total = float(_rej_total)
                            if isinstance(_rej_total, float) and _rej_total.is_integer():
                                _rej_total = int(_rej_total)
                        except (TypeError, ValueError):
                            _rej_total = 0
                        _rej_inv_no = str(flattened_data.get("invoice_number") or "NA").strip() or "NA"
                        _charge_rej_payload = {
                            "data": {
                                "invoice_number": _rej_inv_no,
                                "vendor_reference_id": flattened_data.get("vendor_reference_id") or "",
                                "invoice_amount": _rej_total,
                                "invoice_currency": (flattened_data.get("currency") or "").lower(),
                                "payment_due_date": flattened_data.get("payment_due_date") or "",
                                "mode": _rej_mode,
                                "reason": [_rej_reason],
                                "attachment_bucket": original_input_bucket or "",
                                "file_name": _rej_file_name,
                                "file_key": original_input_key or "",
                                "file_type": (_rej_file_name.rsplit(".", 1)[-1].lower() if "." in _rej_file_name else "pdf"),
                            }
                        }
                        logger.info("REJECTION_API | charge_mismatch payload: %s", json.dumps(_charge_rej_payload))
                        _rej_resp = requests.post(
                            REJECTION_ENDPOINT,
                            headers={"Content-Type": "application/json", "internal-token": INTERNAL_TOKEN},
                            data=json.dumps(_charge_rej_payload),
                            timeout=10,
                        )
                        _charge_rej_response = {
                            "status_code": _rej_resp.status_code,
                            "success": _rej_resp.status_code == 200,
                            "timestamp": datetime.now().isoformat(),
                            "body": _rej_resp.text,
                        }
                        logger.info("REJECTION_API | charge_mismatch response: %s", json.dumps(_charge_rej_response))
                    except Exception as _rej_err:
                        logger.error("REJECTION_API | charge_mismatch call failed: %s", _rej_err, exc_info=True)

                # ── SLACK NOTIFICATION: charge total mismatch rejection ────────────
                try:
                    from slack_notifier import notify_invoice_rejection

                    _slack_rej_shipments = flattened_data.get("shipments", []) if isinstance(flattened_data, dict) else []
                    _slack_rej_details = {}
                    if isinstance(_slack_rej_shipments, list) and _slack_rej_shipments and isinstance(_slack_rej_shipments[0], dict):
                        _slack_first_ship = _slack_rej_shipments[0]
                        _slack_rej_details = {
                            "Source": f"{_slack_first_ship.get('source_name', '')}",
                            "Destination": f"{_slack_first_ship.get('destination_name', '')}",
                            "Mode": _slack_first_ship.get('mode', ''),
                            "Service Level": _slack_first_ship.get('service_level', '')
                        }

                    _slack_sender_email = email_details.get("to", "") or email_details.get("from", "")
                    _slack_s3_path = f"s3://{original_input_bucket}/{original_input_key}" if original_input_bucket and original_input_key else None
                    _slack_file_name = email_details.get("filename") or ""
                    if not _slack_file_name and original_input_key:
                        _slack_file_name = original_input_key.split("/")[-1]

                    notify_invoice_rejection(
                        invoice_number=_rej_inv_no,
                        email_id=email_id,
                        attachment_id=attachment_id,
                        filename=_slack_file_name,
                        sender_email=_slack_sender_email,
                        rejection_reason=_rej_reason,
                        rejection_details=_slack_rej_details,
                        s3_path=_slack_s3_path
                    )
                    logger.info("✅ Charge mismatch rejection notification sent to Slack")
                except ImportError:
                    logger.warning("⚠️ slack_notifier module not found, skipping Slack notification for charge mismatch")
                except Exception as slack_err:
                    logger.error(f"❌ Failed to send Slack notification for charge mismatch: {slack_err}", exc_info=True)
                # ─────────────────────────────────────────────────────────────────

                update_attachment_status(
                    dynamodb_client,
                    DYNAMODB_TABLE,
                    email_id,
                    attachment_id,
                    'FAILED',
                    error={
                        'message': _rej_reason,
                        'error_code': 'CHARGE_TOTAL_MISMATCH'
                    },
                    missing_critical_field=0,
                    missing_fields=[],
                    confidence_score=average_confidence,
                    extracted_fields=extracted_fields_array,
                    api_response=_charge_rej_response,
                    api_payload=_charge_rej_payload,
                    payment_terms=validate_payment_terms(flattened_data.get('payment_terms'))
                )
                return {
                    'statusCode': 422,
                    'body': json.dumps({
                        'message': 'Invoice rejected: charge total mismatch',
                        'reason': _rej_reason,
                    })
                }
        else:
            logger.info("CHARGE_VALIDATION | SKIPPED — total_invoice_value not found in LLM response")

        # Re-check invoice_date after any charge re-extraction (no-op if still within 6 months)
        _inv_before = _extract_invoice_date_value(extracted_info)
        extracted_info = _maybe_retry_stale_invoice_date(
            extracted_info, raw_text, carrier_name=carrier_name
        )
        if _extract_invoice_date_value(extracted_info) != _inv_before:
            flattened_data = flatten_structured_output(extracted_info)
            average_confidence = calculate_weighted_confidence(extracted_info)
            extracted_fields_array = prepare_extracted_fields_array(extracted_info)
        # ─────────────────────────────────────────────────────────────────────────

        # Step 5: Prepare for email notification (validation moved to just before API call)
        logger.info("Step 5: Preparing for email notification")
        email_status = True  # Default to True for email sending (validation happens later)
        validation_errors = {"required_field_errors": [], "other_field_errors": []}
        has_critical_field_errors = False
        missing_field_names = []  # List to store missing field names
        
        # Normalize payment_terms to valid values (collect/prepaid/net 30) before processing
        current_payment_terms = flattened_data.get("payment_terms", "")
        normalized_payment_terms = validate_payment_terms(current_payment_terms)
        flattened_data["payment_terms"] = normalized_payment_terms
        if current_payment_terms != normalized_payment_terms:
            logger.info(f"Normalized payment_terms from '{current_payment_terms}' to '{normalized_payment_terms}'")
        else:
            logger.info(f"Payment_terms already valid: '{normalized_payment_terms}'")
        
        # Add payment due date validation errors to the validation system
        # Only add errors if validation failed AND no auto-correction occurred
        if not payment_due_date_validation['is_valid'] and not payment_due_date_validation.get('auto_corrected', False):
            for error in payment_due_date_validation['errors']:
                validation_errors["other_field_errors"].append({
                    "field": "payment_due_date",
                    "message": error['message'],
                    "type": error['type'],
                    "invoice_date": error.get('invoice_date'),
                    "payment_due_date": error.get('payment_due_date')
                })
            logger.warning(f"Added {len(payment_due_date_validation['errors'])} payment due date validation errors to validation system")
        elif payment_due_date_validation.get('auto_corrected', False):
            logger.info("Payment due date was auto-corrected, no validation errors to add")
            
        # Step 6: Send email notification with HTML content
        logger.info("Step 6: Sending email notification")
        
        try:
            # Use status email info from email_details if present, else fallback
            status_message_id = email_details.get('status_message_id') or email_details.get('message_id')
            status_email_body = email_details.get('status_email_body')
            status_email_subject = email_details.get('status_email_subject') or email_details.get('subject')
            status_email_sender = email_details.get('status_email_sender') or email_details.get('from')
            status_email_date = email_details.get('status_email_date') or email_details.get('date')

            # Send email (validation will happen later before API call)
            if True:  # Always send email at this stage
                try:
                    # Email sending is currently disabled
                    logger.info("Email sending is currently disabled - would send success email here")
                    # send_email(
                    #     smtp_server=SMTP_SERVER,
                    #     smtp_port=SMTP_PORT,
                    #     smtp_username=SMTP_USERNAME,
                    #     smtp_password=SMTP_PASSWORD,
                    #     from_email=FROM_EMAIL,
                    #     to_email=email_details['to'],
                    #     subject=email_details['subject'],
                    #     body=html_content,
                    #     json_data={
                    #         **flattened_data,
                    #         'original_body': email_details['original_body'],
                    #         'filename': email_details['filename']
                    #     },
                    #     message_id=status_message_id,
                    #     quoted_sender=status_email_sender,
                    #     quoted_date=status_email_date,
                    #     quoted_subject=status_email_subject,
                    #     quoted_body=status_email_body
                    # )
                except Exception as e:
                    logger.error(f"Failed to send success email: {str(e)}")
                    # Continue processing even if email fails
        except Exception as e:
            logger.error(f"Failed to send email: {str(e)}")

        # Step 7: Process payload and send data to external API
        logger.info("Step 7: Processing payload for API")
        final_payload = None
        api_success = False  # Flag to track API success
        api_response_data = None  # Store API response details
        mode = ""  # Extract mode from shipments if possible

        # Process payload (validation will happen just before API call)
        try:
            # Check if extracted_fields_array exists and ensure all fields have confidence values
            if extracted_fields_array:
                for field in extracted_fields_array:
                    if "confidence" not in field:
                        field["confidence"] = 0.0  # Default confidence score
                        logger.warning(f"No confidence provided by LLM for field: {field['field_name']}")
            else:
                logger.warning("extracted_fields_array not available for confidence validation")
            
            # Removed carrier-specific shipment number logic
            
            for data_item in flattened_data:
                if isinstance(data_item, dict):
                    # Replace nulls in each data item's direct fields
                    for field, field_type in schema.items():
                        if field in data_item and data_item[field] is None:
                            if field_type == "string":
                                data_item[field] = ""
                                logger.info(f"Replaced None with empty string for field {field}")
                            elif field_type == "number":
                                data_item[field] = 0
                                logger.info(f"Replaced None with 0 for field {field}")
    
            # Fix null values in top-level fields
            for field, field_type in schema.items():
                if field in flattened_data:
                    value = flattened_data[field]
                    if value is None:
                        if field_type == "string":
                            flattened_data[field] = ""
                            logger.info(f"Replaced None with empty string for top-level field {field}")
                        elif field_type == "number":
                            flattened_data[field] = 0
                            logger.info(f"Replaced None with 0 for top-level field {field}")
                        elif field_type == "boolean":
                            flattened_data[field] = False
                            logger.info(f"Replaced None with False for top-level field {field}")
                        elif field_type == "array":
                            flattened_data[field] = []
                            logger.info(f"Replaced None with empty array for top-level field {field}")
            
            # Ensure net_invoice_value exists - use total_invoice_value as first fallback for all carriers
            total_invoice_value = flattened_data.get("total_invoice_value")
            has_total_value = total_invoice_value is not None and total_invoice_value != "" and total_invoice_value != 0

            if "net_invoice_value" not in flattened_data or flattened_data["net_invoice_value"] is None:
                if has_total_value:
                    flattened_data["net_invoice_value"] = total_invoice_value
                    logger.info(f"Set net_invoice_value from total_invoice_value (was missing/None): {flattened_data['net_invoice_value']}")
                else:
                    flattened_data["net_invoice_value"] = 0
                    logger.info("Set net_invoice_value to 0 (missing/None and total_invoice_value unavailable)")
            elif isinstance(flattened_data["net_invoice_value"], str) and flattened_data["net_invoice_value"].strip() == "":
                if has_total_value:
                    flattened_data["net_invoice_value"] = total_invoice_value
                    logger.info(f"Set net_invoice_value from total_invoice_value (was empty string): {flattened_data['net_invoice_value']}")
                else:
                    flattened_data["net_invoice_value"] = 0
                    logger.info("Set net_invoice_value to 0 (empty string and total_invoice_value unavailable)")
            elif flattened_data["net_invoice_value"] == 0 and has_total_value:
                flattened_data["net_invoice_value"] = total_invoice_value
                logger.info(f"Set net_invoice_value from total_invoice_value (was 0): {flattened_data['net_invoice_value']}")
            
            # Fix custom_fields object
            if "custom_fields" in flattened_data and isinstance(flattened_data["custom_fields"], dict):
                if "petrol_charge" in flattened_data["custom_fields"] and flattened_data["custom_fields"]["petrol_charge"] is None:
                    flattened_data["custom_fields"]["petrol_charge"] = 0
            
            # Fix documents_attachment array
            if "documents_attachment" in flattened_data and flattened_data["documents_attachment"] is None:
                flattened_data["documents_attachment"] = []
    
            # Loop through schema and fix null values based on type
            for shipment in flattened_data["shipments"]:
                logger.info(f"Checking for null values in shipment...")
                if isinstance(shipment, dict):
                    for field, field_type in schema.items():
                        if field in shipment:
                            value = shipment[field]
                            
                            # Check for None (null in JSON) and replace
                            if value is None:
                                if field_type == "string":
                                    shipment[field] = ""
                                    logger.info(f"Replaced None with empty string for {field}")
                                elif field_type == "number":
                                    shipment[field] = 0
                                    logger.info(f"Replaced None with 0 for {field}")
                                elif field_type == "boolean":
                                    shipment[field] = False
                                    logger.info(f"Replaced None with False for {field}")
                                elif field_type == "array":
                                    shipment[field] = []
                                    logger.info(f"Replaced None with empty array for {field}")
                    
                    # Fix charges array
                    if "charges" in shipment and isinstance(shipment["charges"], list):
                        logger.info(f"Processing charges array with {len(shipment['charges'])} items")
                        for charge in shipment["charges"]:
                            if isinstance(charge, dict):
                                for field, field_type in schema.items():
                                    if field in charge:
                                        value = charge[field]
                                        
                                        # Check for None (null in JSON) and replace
                                        if value is None:
                                            if field_type == "string":
                                                charge[field] = ""
                                                logger.info(f"Replaced None with empty string for charge {field}")
                                            elif field_type == "number":
                                                charge[field] = 0
                                                logger.info(f"Replaced None with 0 for charge {field}")
                                            elif field_type == "boolean":
                                                charge[field] = False
                                                logger.info(f"Replaced None with False for charge {field}")
                    
                    # Fix container array
                    if "container" in shipment and isinstance(shipment["container"], list):
                        logger.info(f"Processing container array with {len(shipment['container'])} items")
                        # Get mode to determine container defaults for Air/drayage
                        shipment_mode = shipment.get("mode", "").upper()
                        is_air_or_drayage = shipment_mode in ["AIR", "DRAYAGE"]
                        
                        for container in shipment["container"]:
                            if isinstance(container, dict):
                                # Handle container fields
                                if "container_id" in container and container["container_id"] is None:
                                    container["container_id"] = None
                                if "container_number" in container and container["container_number"] is None:
                                    container["container_number"] = 0
                                if "container_type" in container and container["container_type"] is None:
                                    container["container_type"] = ""
                                if "no_of_containers" in container and container["no_of_containers"] is None:
                                    container["no_of_containers"] = 0
                                if "container_weight" in container and container["container_weight"] is None:
                                    container["container_weight"] = 0  # Default to 0 for all modes
                                if "container_weight_uom" in container and container["container_weight_uom"] is None:
                                    # For Air/drayage, default to "Lb", otherwise empty string
                                    container["container_weight_uom"] = "Lb" if is_air_or_drayage else ""
                    
                    # Fix custom object in shipment - ensure it's fully flattened
                    if "custom" in shipment and isinstance(shipment["custom"], dict):
                        flattened_custom = {}
                        for custom_key, custom_value in shipment["custom"].items():
                            # Handle structured format (value/explanation/confidence)
                            if isinstance(custom_value, dict) and "value" in custom_value:
                                flattened_custom[custom_key] = custom_value["value"]
                            else:
                                flattened_custom[custom_key] = custom_value
                            # Fix None values
                            if flattened_custom[custom_key] is None:
                                if custom_key == "unloading_charge":
                                    flattened_custom[custom_key] = 0
                                else:
                                    flattened_custom[custom_key] = ""
                        shipment["custom"] = flattened_custom
            
            logger.info(f"Pure Data:\n{_format_json_for_log(flattened_data)}")
            
            # Payment_terms should already be set, but ensure it's normalized here
            # Normalize payment terms to valid values (collect/prepaid/net 30) before sending to API
            current_payment_terms = flattened_data.get("payment_terms", "")
            normalized_payment_terms = validate_payment_terms(current_payment_terms)
            flattened_data["payment_terms"] = normalized_payment_terms
            if current_payment_terms != normalized_payment_terms:
                logger.info(f"Normalized payment_terms from '{current_payment_terms}' to '{normalized_payment_terms}'")
            else:
                logger.info(f"Payment_terms already valid: '{normalized_payment_terms}'")
            
            # Get invoice_date and ship_date for setting shipment_creation_date
            invoice_date = flattened_data.get("invoice_date", "")
            ship_date = flattened_data.get("ship_date", "")  # Check invoice level for ship_date
            
            # Get vendor_reference_id to determine default mode (handle both string and dict from flatten)
            _vendor_ref_raw = flattened_data.get("vendor_reference_id", "")
            if isinstance(_vendor_ref_raw, dict) and "value" in _vendor_ref_raw:
                vendor_reference_id = _vendor_ref_raw.get("value", "")
            else:
                vendor_reference_id = _vendor_ref_raw if isinstance(_vendor_ref_raw, str) else str(_vendor_ref_raw or "")
            default_mode = get_default_mode_from_vendor_reference_id(vendor_reference_id)
            
            # Set mode: use LLM output as-is; only set default when mode is empty (no RXO-specific override)
            if "shipments" in flattened_data and isinstance(flattened_data["shipments"], list):
                for i, shipment in enumerate(flattened_data["shipments"]):
                    if isinstance(shipment, dict):
                        current_mode = shipment.get("mode", "")
                        if not current_mode or (isinstance(current_mode, str) and current_mode.strip() == ""):
                            shipment["mode"] = default_mode
                            logger.info(f"Set mode to '{default_mode}' in shipment based on vendor '{vendor_reference_id}'")
                        else:
                            logger.info(f"Preserving LLM-extracted mode '{current_mode}' in shipment")
                        
                        # RXO (XPON): if mode is other than Road or Air, map to Air for API ingestion
                        vendor_ref_upper = str(vendor_reference_id or "").upper().strip()
                        if vendor_ref_upper == "XPON":
                            _mode_raw = shipment.get("mode")
                            if isinstance(_mode_raw, dict) and "value" in _mode_raw:
                                current_mode_val = (str(_mode_raw.get("value", "") or "").strip().upper())
                            else:
                                current_mode_val = (str(_mode_raw or "").strip().upper())
                            if current_mode_val and current_mode_val not in ("ROAD", "AIR"):
                                shipment["mode"] = "Air"
                                logger.info(f"RXO (XPON) invoice: mapped mode from '{current_mode_val}' to 'Air' for API ingestion")
                        
                        # Set shipment_creation_date only if not already present
                        # Priority: 1) ship_date (if present), 2) invoice_date (if ship_date not present)
                        current_shipment_date = shipment.get("shipment_creation_date", "")
                        if not current_shipment_date or (isinstance(current_shipment_date, str) and current_shipment_date.strip() == ""):
                            # Check if ship_date exists in shipment level first, then invoice level
                            shipment_ship_date = shipment.get("ship_date", "")
                            if shipment_ship_date and isinstance(shipment_ship_date, str) and shipment_ship_date.strip():
                                shipment["shipment_creation_date"] = shipment_ship_date
                                logger.info(f"Set shipment_creation_date to ship_date '{shipment_ship_date}' from shipment")
                            elif ship_date and isinstance(ship_date, str) and ship_date.strip():
                                shipment["shipment_creation_date"] = ship_date
                                logger.info(f"Set shipment_creation_date to ship_date '{ship_date}' from invoice level")
                            elif invoice_date:
                                # Only set shipment_creation_date to invoice_date if mode is "Ocean"
                                current_mode = shipment.get("mode", "")
                                if current_mode == "Ocean":
                                    shipment["shipment_creation_date"] = invoice_date
                                    logger.info(f"Set shipment_creation_date to invoice_date '{invoice_date}' (mode is 'Ocean', ship_date not available)")
                                else:
                                    logger.info(f"Skipping shipment_creation_date setting (mode is '{current_mode}', not 'Ocean')")
                            else:
                                logger.warning("Neither ship_date nor invoice_date available, cannot set shipment_creation_date")
                        else:
                            logger.info(f"Preserving existing shipment_creation_date '{current_shipment_date}' in shipment")
                        
                        # Before API: if shipment_creation_date is still empty string, populate with invoice_date
                        current_shipment_date_after = shipment.get("shipment_creation_date", "")
                        if (not current_shipment_date_after or (isinstance(current_shipment_date_after, str) and current_shipment_date_after.strip() == "")) and invoice_date:
                            shipment["shipment_creation_date"] = invoice_date
                            logger.info(f"Set shipment_creation_date to invoice_date '{invoice_date}' (was empty before API)")
                        
                        # Consolidate charges with the same name within each shipment
                        consolidated_shipment = consolidate_charges_in_shipment(shipment)
                        flattened_data["shipments"][i] = consolidated_shipment
                        logger.info(f"Consolidated charges in shipment {i} for API payload")
                        
                        # Set service_level for DRAYAGE carriers (set early in flattened_data processing)
                        vendor_ref_id = flattened_data.get("vendor_reference_id", "")
                        vendor_ref_upper = str(vendor_ref_id).upper().strip() if vendor_ref_id else ""
                        
                        # DRAYAGE carriers (extended list for service_level / payload): MXNG, NFBR, PLOK, PDCM, CPGP, GUCI, BRJF, BMT, FZMK, TTLQ, HOAL
                        drayage_carriers = ["MXNG", "NFBR", "PLOK", "PDCM", "CPGP", "GUCI", "BRJF", "BMT", "FZMK", "TTLQ", "HOAL"]
                        
                        if vendor_ref_upper in drayage_carriers:
                            charge_names_found = []
                            has_transloading_charge = False
                            if "charges" in consolidated_shipment and isinstance(consolidated_shipment["charges"], list):
                                for charge in consolidated_shipment["charges"]:
                                    if isinstance(charge, dict):
                                        charge_name = str(charge.get("charge_name", "")).strip()
                                        charge_name_upper = charge_name.upper()
                                        tariff_description = str(charge.get("tariff_description", "")).upper()
                                        charge_names_found.append(charge_name)
                                        if (charge_name_upper == "TRANSLOADING" or
                                            "TRANSLOADING" in charge_name_upper or
                                            charge_name_upper == "TRANSLOAD" or
                                            "DRAYAGE" in charge_name_upper or
                                            "TRANSLOADING" in tariff_description or
                                            "DRAYAGE" in tariff_description):
                                            has_transloading_charge = True

                            logger.info(f"{vendor_ref_upper} carrier (flattened_data): Charge names found: {charge_names_found}, has_transloading_charge={has_transloading_charge}")

                            if vendor_ref_upper == "MXNG":
                                # LLM sets service_level="TL-STANDARD" when VAN or REEFER keyword is detected in the invoice.
                                # VAN matches: DRYVAN, DRY VAN, CARGO VAN, or any text containing "VAN"
                                # REEFER matches: REEFER, 6Y REEFER, or any text containing "REEFER"
                                # By this point charge names are already mapped (e.g. "Glass Window"→"Base Freight"),
                                # so VAN/REEFER keywords are no longer visible in charge names — use LLM output as the indicator.
                                llm_service_level = str(consolidated_shipment.get("service_level", "")).strip()
                                if llm_service_level == "TL-STANDARD":
                                    consolidated_shipment["service_level"] = "TL-STANDARD"
                                    consolidated_shipment["delivery_type"] = "TL"
                                    logger.info("MXNG carrier (flattened_data): VAN or REEFER keyword detected by LLM (service_level=TL-STANDARD) — keeping TL-STANDARD, delivery_type=TL")
                                else:
                                    consolidated_shipment["delivery_type"] = "DRAYAGE"
                                    destination_name = str(consolidated_shipment.get("destination_name", "")).strip().upper()
                                    # TRANS DRAYAGE only when BOTH destination=SLC AND Transload charge present
                                    if destination_name == "SLC" and has_transloading_charge:
                                        consolidated_shipment["service_level"] = "TRANS DRAYAGE"
                                        logger.info("MXNG carrier (flattened_data): No VAN/REEFER keywords, destination_name=SLC AND Transloading found — service_level=TRANS DRAYAGE, delivery_type=DRAYAGE")
                                    else:
                                        consolidated_shipment["service_level"] = "SINGLE DRAYAGE"
                                        logger.info(f"MXNG carrier (flattened_data): No VAN/REEFER keywords, destination_name={destination_name}, has_transloading={has_transloading_charge} — service_level=SINGLE DRAYAGE, delivery_type=DRAYAGE")

                            # Update the shipment in flattened_data
                            flattened_data["shipments"][i] = consolidated_shipment
            
            final_payload = api_handler.create_invoice_payload(
                flattened_data,
                email_details,
                original_input_key,
                original_input_bucket,
                validation_errors if 'validation_errors' in locals() else {},
                transaction_id=email_id,
            )
            
            # Define allowed fields for the API payload
            allowed_fields = [
                "invoice_number", "invoice_date", "vendor_reference_id", "payment_due_date", 
                "bill_of_lading_number", "bill_of_entry_number", "currency", "total_invoice_value", 
                "payment_terms", "po_number", "assessable_value", "proforma_invoice_creation_date",
                "net_invoice_value", "round_off", "rate_applicability_date",
                "arrival_date", "departure_date", "airway_bill_number", "hsn_number",
                "bill_to_name", "bill_to_address", "documents_attachment", "shipments", "custom"
            ]
            
            # Clean the payload to remove irrelevant fields and ensure all required fields are present
            if "data" in final_payload and isinstance(final_payload["data"], list) and final_payload["data"]:
                clean_data = {}
                for field in allowed_fields:
                    # Special handling for net_invoice_value - use total_invoice_value as first fallback
                    if field == "net_invoice_value":
                        original_value = final_payload["data"][0].get(field)
                        total_value = final_payload["data"][0].get("total_invoice_value")

                        # Always use total_invoice_value when net_invoice_value is empty/missing/0
                        if total_value is not None and total_value != "":
                            if isinstance(total_value, str):
                                try:
                                    clean_data[field] = float(total_value.replace(",", "").strip())
                                except (ValueError, AttributeError):
                                    clean_data[field] = 0
                            else:
                                clean_data[field] = float(total_value) if not isinstance(total_value, (int, float)) else total_value
                            logger.info(f"Set net_invoice_value from total_invoice_value: {clean_data[field]}")
                        elif original_value is not None:
                            if isinstance(original_value, str):
                                try:
                                    clean_data[field] = float(original_value.replace(",", "").strip()) if original_value.strip() else 0
                                except (ValueError, AttributeError):
                                    clean_data[field] = 0
                            else:
                                clean_data[field] = original_value
                        else:
                            clean_data[field] = 0
                    # Special handling for payment_terms - normalize to valid values (collect/prepaid/net 30)
                    elif field == "payment_terms":
                        original_value = final_payload["data"][0].get(field, "")
                        # Normalize payment terms using validate_payment_terms function
                        clean_data[field] = validate_payment_terms(original_value)
                        if original_value != clean_data[field]:
                            logger.info(f"Normalized payment_terms from '{original_value}' to '{clean_data[field]}' before sending to API")
                    # Special handling for total_invoice_value - ensure it's always a number (not string)
                    elif field == "total_invoice_value":
                        original_value = final_payload["data"][0].get(field)
                        if original_value is not None:
                            # Convert to number if it's a string
                            if isinstance(original_value, str):
                                try:
                                    clean_data[field] = float(original_value.replace(",", "").strip())
                                    logger.info(f"Converted total_invoice_value from string '{original_value}' to number {clean_data[field]}")
                                except (ValueError, AttributeError):
                                    clean_data[field] = 0
                                    logger.warning(f"Failed to convert total_invoice_value '{original_value}' to number, defaulting to 0")
                            else:
                                # Already a number, ensure it's float
                                clean_data[field] = float(original_value) if not isinstance(original_value, (int, float)) else original_value
                        else:
                            clean_data[field] = 0
                    # Use existing value if present, otherwise set default based on schema type
                    elif field in final_payload["data"][0] and final_payload["data"][0][field] is not None:
                        clean_data[field] = final_payload["data"][0][field]
                    else:
                        # Set default based on schema type
                        field_type = schema.get(field, "string")
                        if field_type == "string":
                            clean_data[field] = ""
                        elif field_type == "number":
                            clean_data[field] = 0
                        elif field_type == "boolean":
                            clean_data[field] = False
                        elif field_type == "array":
                            clean_data[field] = []
                        else:
                            clean_data[field] = ""
                
                # Add SCAC prefix to BOL for ocean carriers only (not for Road/drayage)
                vendor_ref_id = clean_data.get("vendor_reference_id", "")
                if vendor_ref_id:
                    vendor_ref_upper = str(vendor_ref_id).upper().strip()
                    
                    # Special handling for Matson (MATS) - ocean carrier: add prefix "MATS" and suffix "000"
                    if vendor_ref_upper == "MATS":
                        if "bill_of_lading_number" in clean_data and clean_data["bill_of_lading_number"]:
                            bill_of_lading_number = str(clean_data["bill_of_lading_number"]).strip()
                            if bill_of_lading_number:
                                # Remove any existing MATS prefix and 000 suffix if present
                                if bill_of_lading_number.upper().startswith("MATS"):
                                    bill_of_lading_number = bill_of_lading_number[4:]
                                if bill_of_lading_number.endswith("000"):
                                    bill_of_lading_number = bill_of_lading_number[:-3]
                                # Add MATS prefix and 000 suffix (no gaps)
                                clean_data["bill_of_lading_number"] = "MATS" + bill_of_lading_number + "000"
                                logger.info(f"Added MATS prefix and 000 suffix to bill_of_lading_number: {clean_data['bill_of_lading_number']}")
                
                # Validate shipments array if present
                if "shipments" in clean_data and isinstance(clean_data["shipments"], list):
                    # Set mode from first shipment if available
                    if clean_data["shipments"] and isinstance(clean_data["shipments"][0], dict):
                        mode = clean_data["shipments"][0].get("mode", "")
                        
                    # Define allowed shipment fields
                    allowed_shipment_fields = [
                        "shipment_number", "consignee_name", "shipment_tracking_number", "stopover_location",
                        "dangerous_goods_indicator", "thu_type", "thu_name", "service_level",
                        "delivery_type",
                        "number_of_pallets", "pro_number", "mode", "source_name", "source_code", "source_city",
                        "source_state", "source_country", "source_country_code", "source_address",
                        "source_province", "source_region", "port_of_loading", "destination_name",
                        "destination_code", "destination_city", "destination_state", "destination_country", "destination_country_code",
                        "destination_address", "destination_province", "destination_region",
                        "port_of_discharge", "shipment_weight", "shipment_volume", "shipment_weight_uom", "shipment_volume_uom", "shipment_total_value",
                        "container", "charges", "shipment_creation_date"
                    ]
                    
                    cleaned_shipments = []
                    for shipment in clean_data["shipments"]:
                        if isinstance(shipment, dict):
                            clean_shipment = {}
                            for field in allowed_shipment_fields:
                                # Use existing value if present, otherwise set default based on schema type
                                if field in shipment and shipment[field] is not None:
                                    clean_shipment[field] = shipment[field]
                                else:
                                    # Set default based on schema type
                                    field_type = schema.get(field, "string")
                                    if field_type == "string":
                                        clean_shipment[field] = ""
                                    elif field_type == "number":
                                        clean_shipment[field] = 0
                                    elif field_type == "boolean":
                                        clean_shipment[field] = False
                                    elif field_type == "array":
                                        clean_shipment[field] = []
                                    else:
                                        clean_shipment[field] = ""
                                    
                            # Set source_code to the same value as source_name
                            if "source_name" in clean_shipment:
                                clean_shipment["source_code"] = clean_shipment["source_name"]
                            elif "source_code" not in clean_shipment:
                                clean_shipment["source_code"] = ""
                            
                            # Set destination_code to the same value as destination_name
                            if "destination_name" in clean_shipment:
                                clean_shipment["destination_code"] = clean_shipment["destination_name"]
                            elif "destination_code" not in clean_shipment:
                                clean_shipment["destination_code"] = ""
                            
                            # Ensure custom object exists with unloading_charge (only if missing)
                            if "custom" not in clean_shipment:
                                clean_shipment["custom"] = {"unloading_charge": 0}
                            elif isinstance(clean_shipment["custom"], dict):
                                if "unloading_charge" not in clean_shipment["custom"]:
                                    clean_shipment["custom"]["unloading_charge"] = 0
                                    
                            # Clean up charges if present - ensure all fields are always present
                            if "charges" in clean_shipment and isinstance(clean_shipment["charges"], list):
                                allowed_charge_fields = [
                                    "charge_name", "charge_gross_amount", "charge_code", "currency",
                                    "tariff_rate", "tariff_qty", "tariff_uom", "tariff_description"
                                ]
                                
                                clean_charges = []
                                for charge in clean_shipment["charges"]:
                                    if isinstance(charge, dict):
                                        # Skip charges with zero or missing amount before API send
                                        raw_amount = charge.get("charge_gross_amount", None)
                                        try:
                                            amount_val = float(raw_amount) if raw_amount not in (None, "", "null") else 0.0
                                        except (ValueError, TypeError):
                                            amount_val = 0.0
                                        if amount_val == 0.0:
                                            logger.info(
                                                f"Skipping charge '{charge.get('charge_name', '')}' "
                                                f"— charge_gross_amount is 0 or empty"
                                            )
                                            continue

                                        clean_charge = {}
                                        # Always include all allowed charge fields
                                        for field in allowed_charge_fields:
                                            # Use existing value if present and not None, otherwise set default based on schema type
                                            if field in charge and charge[field] is not None:
                                                clean_charge[field] = charge[field]
                                            else:
                                                # Set default based on schema type
                                                field_type = schema.get(field, "string")
                                                if field_type == "string":
                                                    clean_charge[field] = ""
                                                elif field_type == "number":
                                                    clean_charge[field] = 0
                                                elif field_type == "boolean":
                                                    clean_charge[field] = False
                                                elif field_type == "array":
                                                    clean_charge[field] = []
                                                else:
                                                    clean_charge[field] = ""
                                        clean_charges.append(clean_charge)
                                        
                                clean_shipment["charges"] = clean_charges
                            elif "charges" not in clean_shipment:
                                # Ensure charges array exists even if empty
                                clean_shipment["charges"] = []

                            # ── LTL Discount absorption into Base Freight (Task 3) ──────────────
                            # For LTL carriers: If a charge contains "Discount", add its negative
                            # value to Base Freight and remove the Discount charge from the array.
                            # This ensures proper audit of base rate for contracted LTL rates.
                            current_delivery_type = str(clean_shipment.get("delivery_type", "")).upper()
                            
                            if current_delivery_type == "LTL" and isinstance(clean_shipment.get("charges"), list):
                                discount_total = 0.0
                                non_discount_charges = []
                                discount_found = False
                                
                                for _ch in clean_shipment["charges"]:
                                    charge_name = str(_ch.get("charge_name", "")).strip().lower()
                                    
                                    # Check if this charge contains "discount"
                                    if "discount" in charge_name:
                                        discount_found = True
                                        try:
                                            _amt = float(_ch.get("charge_gross_amount", 0) or 0)
                                        except (ValueError, TypeError):
                                            _amt = 0.0
                                        discount_total += _amt  # Typically negative
                                        logger.info(
                                            f"LTL Discount charge found: '{_ch.get('charge_name', '')}' "
                                            f"amount={_amt} will be added to Base Freight"
                                        )
                                    else:
                                        non_discount_charges.append(_ch)
                                
                                if discount_found and discount_total != 0.0:
                                    # Find Base Freight charge to add discount to
                                    base_idx = next(
                                        (i for i, c in enumerate(non_discount_charges)
                                         if c.get("charge_code") == "400"
                                         or str(c.get("charge_name", "")).strip().lower() == "base freight"),
                                        None
                                    )
                                    
                                    if base_idx is not None:
                                        try:
                                            base_amt = float(non_discount_charges[base_idx].get("charge_gross_amount", 0) or 0)
                                        except (ValueError, TypeError):
                                            base_amt = 0.0
                                        new_base_amt = round(base_amt + discount_total, 4)
                                        non_discount_charges[base_idx]["charge_gross_amount"] = new_base_amt
                                        logger.info(
                                            f"LTL Base Freight adjusted for discount: {base_amt} + ({discount_total}) = {new_base_amt}"
                                        )
                                        clean_shipment["charges"] = non_discount_charges
                                    else:
                                        # No Base Freight found — log warning and keep discount charge
                                        logger.warning(
                                            f"LTL: No Base Freight charge found to absorb discount total {discount_total}; "
                                            f"discount charge kept in payload"
                                        )
                                elif discount_found:
                                    logger.info(f"LTL: Discount charge found but amount is 0, no adjustment needed")
                            
                            # ── Generic negative-charge absorption (non-LTL carriers) ────────────
                            # For non-LTL carriers: Any charge with negative amount is absorbed
                            # into Base Freight and removed from the array.
                            elif isinstance(clean_shipment.get("charges"), list):
                                neg_total = 0.0
                                positive_charges = []
                                for _ch in clean_shipment["charges"]:
                                    try:
                                        _amt = float(_ch.get("charge_gross_amount", 0) or 0)
                                    except (ValueError, TypeError):
                                        _amt = 0.0
                                    if _amt < 0:
                                        neg_total += _amt
                                        logger.info(
                                            f"Negative charge absorbed: '{_ch.get('charge_name', '')}' "
                                            f"amount={_amt} will be added to Base Freight"
                                        )
                                    else:
                                        positive_charges.append(_ch)

                                if neg_total != 0.0:
                                    # Find Base Freight charge to absorb into
                                    base_idx = next(
                                        (i for i, c in enumerate(positive_charges)
                                         if c.get("charge_code") == "400"
                                         or str(c.get("charge_name", "")).strip().lower() == "base freight"),
                                        None
                                    )
                                    if base_idx is not None:
                                        try:
                                            base_amt = float(positive_charges[base_idx].get("charge_gross_amount", 0) or 0)
                                        except (ValueError, TypeError):
                                            base_amt = 0.0
                                        new_base_amt = round(base_amt + neg_total, 4)
                                        positive_charges[base_idx]["charge_gross_amount"] = new_base_amt
                                        logger.info(
                                            f"Base Freight adjusted: {base_amt} + ({neg_total}) = {new_base_amt}"
                                        )
                                    else:
                                        # No Base Freight found — log and keep negative charges as-is
                                        logger.warning(
                                            f"No Base Freight charge found to absorb negative total {neg_total}; "
                                            f"negative charges kept in payload"
                                        )
                                        positive_charges = clean_shipment["charges"]  # restore originals

                                    clean_shipment["charges"] = positive_charges

                            # ───────────────────────────────────────────────────────────────────

                            # Clean up container array if present
                            if "container" in clean_shipment and isinstance(clean_shipment["container"], list) and len(clean_shipment["container"]) > 0:
                                allowed_container_fields = [
                                    "container_id", "container_number", "container_type",
                                    "no_of_containers", "container_weight", "container_weight_uom"
                                ]
                                
                                clean_containers = []
                                # Get mode to determine container defaults for Air/drayage
                                shipment_mode = clean_shipment.get("mode", "").upper()
                                is_air_or_drayage = shipment_mode in ["AIR", "DRAYAGE"]
                                
                                for container in clean_shipment["container"]:
                                    if isinstance(container, dict):
                                        clean_container = {}
                                        for field in allowed_container_fields:
                                            # Special handling for container_number - should be nullable string
                                            if field == "container_number":
                                                container_number = container.get(field)
                                                # Convert 0 or empty to None (nullable)
                                                if container_number is None or container_number == 0 or container_number == "":
                                                    clean_container[field] = None
                                                else:
                                                    # Convert to string if it's a number
                                                    clean_container[field] = str(container_number) if not isinstance(container_number, str) else container_number
                                            # Use existing value if present, otherwise set default based on schema type
                                            elif field in container and container[field] is not None:
                                                clean_container[field] = container[field]
                                            else:
                                                # Special handling for container_weight and container_weight_uom for Air/drayage
                                                if field == "container_weight":
                                                    if is_air_or_drayage:
                                                        clean_container[field] = 0  # Default to 0 for Air/drayage
                                                    else:
                                                        clean_container[field] = 0  # Default to 0 for other modes
                                                elif field == "container_weight_uom":
                                                    # Get vendor_reference_id to determine default
                                                    vendor_ref_id = clean_data.get("vendor_reference_id", "")
                                                    vendor_ref_upper = str(vendor_ref_id).upper().strip() if vendor_ref_id else ""
                                                    is_drayage_carrier = vendor_ref_upper in ["MXNG", "NFBR", "PLOK", "CPGP", "GUCI", "PDCM", "BRJF", "FZMK", "TTLQ", "HOAL"]
                                                    if is_air_or_drayage:
                                                        if is_drayage_carrier:
                                                            clean_container[field] = "KG"  # Default to "KG" for drayage carriers
                                                        else:
                                                            clean_container[field] = "Lb"  # Default to "Lb" for Air
                                                    else:
                                                        clean_container[field] = ""  # Default to empty for other modes
                                                else:
                                                    # Set default based on schema type
                                                    field_type = schema.get(field, "string")
                                                    if field_type == "string":
                                                        clean_container[field] = ""
                                                    elif field_type == "number":
                                                        clean_container[field] = 0
                                                    elif field_type == "boolean":
                                                        clean_container[field] = False
                                                    elif field_type == "array":
                                                        clean_container[field] = []
                                                    else:
                                                        clean_container[field] = ""
                                        clean_containers.append(clean_container)
                                # For drayage carriers, force container_id and no_of_containers to 1 always.
                                vendor_ref_id_for_container = clean_data.get("vendor_reference_id", "")
                                vendor_ref_upper_for_container = str(vendor_ref_id_for_container).upper().strip() if vendor_ref_id_for_container else ""
                                drayage_carriers_for_container = ["MXNG", "NFBR", "PLOK", "PDCM", "CPGP", "GUCI", "BRJF", "BMT", "FZMK", "TTLQ", "HOAL"]
                                if vendor_ref_upper_for_container in drayage_carriers_for_container:
                                    for c in clean_containers:
                                        c["container_id"] = 1
                                        c["no_of_containers"] = 1
                                    logger.info(f"Forced container_id=1 and no_of_containers=1 for drayage carrier '{vendor_ref_upper_for_container}'")
                                else:
                                    # Keep container_id sequential and deterministic across container entries (1..n)
                                    for idx, c in enumerate(clean_containers, start=1):
                                        c["container_id"] = idx
                                    # Keep no_of_containers consistent across all container entries in the shipment
                                    container_count = sum(
                                        1 for c in clean_containers
                                        if c.get("container_number") not in (None, "", 0)
                                    )
                                    for c in clean_containers:
                                        c["no_of_containers"] = container_count
                                    logger.info(f"Set no_of_containers={container_count} for {len(clean_containers)} container entry(ies)")

                                clean_shipment["container"] = clean_containers
                            
                            # Ensure container array exists - if empty or missing, set to empty array (do not add default container)
                            if "container" not in clean_shipment or not isinstance(clean_shipment["container"], list):
                                clean_shipment["container"] = []
                                
                            # Ensure all charges have all required fields before consolidation
                            for charge in clean_shipment.get("charges", []):
                                if isinstance(charge, dict):
                                    # Ensure all tariff fields are present and properly typed
                                    if "tariff_rate" not in charge or charge["tariff_rate"] is None:
                                        charge["tariff_rate"] = 0
                                    if "tariff_qty" not in charge or charge["tariff_qty"] is None:
                                        charge["tariff_qty"] = 0
                                    
                                    # Convert tariff_qty to number if it's a string or empty
                                    try:
                                        if isinstance(charge["tariff_qty"], str):
                                            # Remove whitespace and convert empty string to 0
                                            tariff_qty_str = charge["tariff_qty"].strip()
                                            if tariff_qty_str == "" or tariff_qty_str.lower() in ["null", "none", "n/a"]:
                                                charge["tariff_qty"] = 0
                                            else:
                                                # Try to convert to float first, then int if it's a whole number
                                                tariff_qty_float = float(tariff_qty_str)
                                                charge["tariff_qty"] = int(tariff_qty_float) if tariff_qty_float.is_integer() else tariff_qty_float
                                        elif charge["tariff_qty"] == "":
                                            charge["tariff_qty"] = 0
                                    except (ValueError, TypeError, AttributeError):
                                        logger.warning(f"Failed to convert tariff_qty '{charge.get('tariff_qty')}' to number, setting to 0")
                                        charge["tariff_qty"] = 0
                                    
                                    if "tariff_uom" not in charge or charge["tariff_uom"] is None:
                                        charge["tariff_uom"] = ""
                                    if "tariff_description" not in charge or charge["tariff_description"] is None:
                                        charge["tariff_description"] = ""
                            
                            # Consolidate charges again after cleaning (safety measure) - but preserve tariff fields
                            # Note: We consolidate charges but ensure tariff fields are preserved from first occurrence
                            if "charges" in clean_shipment and isinstance(clean_shipment["charges"], list):
                                consolidated_charges = {}
                                for charge in clean_shipment["charges"]:
                                    if isinstance(charge, dict):
                                        charge_name = charge.get("charge_name", "").strip()
                                        if charge_name:
                                            # Get charge_code, and if empty or missing, use charge_name
                                            charge_code = charge.get("charge_code", "").strip() if charge.get("charge_code") else ""
                                            if not charge_code:
                                                charge_code = charge_name
                                                logger.info(f"Charge code missing for charge '{charge_name}', setting charge_code to charge_name")
                                            
                                            if charge_name not in consolidated_charges:
                                                # First occurrence - preserve all fields including tariff fields
                                                consolidated_charges[charge_name] = {
                                                    "charge_name": charge.get("charge_name", ""),
                                                    "charge_gross_amount": charge.get("charge_gross_amount", 0),
                                                    "charge_code": charge_code,
                                                    "currency": charge.get("currency", ""),
                                                    "tariff_rate": charge.get("tariff_rate", 0),
                                                    "tariff_qty": charge.get("tariff_qty", 0),
                                                    "tariff_uom": charge.get("tariff_uom", ""),
                                                    "tariff_description": charge.get("tariff_description", "")
                                                }
                                            else:
                                                # Add amounts for duplicate charge names
                                                consolidated_charges[charge_name]["charge_gross_amount"] += charge.get("charge_gross_amount", 0)
                                clean_shipment["charges"] = list(consolidated_charges.values())
                            
                            # Set service_level for DRAYAGE carriers (always overwrite for drayage carriers)
                            vendor_ref_id = clean_data.get("vendor_reference_id", "")
                            vendor_ref_upper = str(vendor_ref_id).upper().strip() if vendor_ref_id else ""
                            
                            # DRAYAGE carriers (extended list for service_level): MXNG, NFBR, PLOK, PDCM, CPGP, GUCI, BRJF, BMT, FZMK, TTLQ, HOAL
                            drayage_carriers = ["MXNG", "NFBR", "PLOK", "PDCM", "CPGP", "GUCI", "BRJF", "BMT", "FZMK", "TTLQ", "HOAL"]
                            
                            if vendor_ref_upper in drayage_carriers:
                                has_transloading_charge = False
                                charge_names_found = []
                                if "charges" in clean_shipment and isinstance(clean_shipment["charges"], list):
                                    for charge in clean_shipment["charges"]:
                                        if isinstance(charge, dict):
                                            charge_name = str(charge.get("charge_name", "")).strip()
                                            charge_name_upper = charge_name.upper()
                                            tariff_description = str(charge.get("tariff_description", "")).upper()
                                            charge_names_found.append(charge_name)
                                            if (charge_name_upper == "TRANSLOADING" or
                                                "TRANSLOADING" in charge_name_upper or
                                                charge_name_upper == "TRANSLOAD" or
                                                "DRAYAGE" in charge_name_upper or
                                                "TRANSLOADING" in tariff_description or
                                                "DRAYAGE" in tariff_description):
                                                has_transloading_charge = True

                                logger.info(f"{vendor_ref_upper} carrier (payload cleaning): Charge names found: {charge_names_found}, has_transloading_charge={has_transloading_charge}")

                                if vendor_ref_upper == "MXNG":
                                    # service_level was already set correctly by the flattened_data block above.
                                    # Use it to determine the VAN or REEFER keyword case rather than re-checking charge names.
                                    # VAN matches: DRYVAN, DRY VAN, CARGO VAN, or any text containing "VAN"
                                    # REEFER matches: REEFER, 6Y REEFER, or any text containing "REEFER"
                                    current_service_level = str(clean_shipment.get("service_level", "")).strip()
                                    if current_service_level == "TL-STANDARD":
                                        clean_shipment["service_level"] = "TL-STANDARD"
                                        clean_shipment["delivery_type"] = "TL"
                                        logger.info("MXNG carrier (payload cleaning): VAN or REEFER keyword case — service_level=TL-STANDARD, delivery_type=TL")
                                    else:
                                        clean_shipment["delivery_type"] = "DRAYAGE"
                                        destination_name = str(clean_shipment.get("destination_name", "")).strip().upper()
                                        # TRANS DRAYAGE only when BOTH destination=SLC AND Transload charge present
                                        if destination_name == "SLC" and has_transloading_charge:
                                            clean_shipment["service_level"] = "TRANS DRAYAGE"
                                            logger.info("MXNG carrier (payload cleaning): No VAN/REEFER keywords, destination_name=SLC AND Transloading found — service_level=TRANS DRAYAGE, delivery_type=DRAYAGE")
                                        else:
                                            clean_shipment["service_level"] = "SINGLE DRAYAGE"
                                            logger.info(f"MXNG carrier (payload cleaning): No VAN/REEFER keywords, destination_name={destination_name}, has_transloading={has_transloading_charge} — service_level=SINGLE DRAYAGE, delivery_type=DRAYAGE")
                            # Note: For non-drayage carriers, service_level is kept as extracted from LLM or defaults to empty string
                            
                            cleaned_shipments.append(clean_shipment)
                            
                    clean_data["shipments"] = cleaned_shipments
                
                # Clean up custom object - only include allowed fields, preserve existing values
                allowed_custom_fields = [
                    "source_type", "shipper_email", "sender_email", "vendor_name",
                    "client_id", "attachment_key", "attachment_bucket", "transaction_id",
                    "invoice_source_name", "invoice_destination_name", "pay_as_present"
                ]
                
                if "custom" in final_payload["data"][0]:
                    original_custom = final_payload["data"][0]["custom"]
                    if isinstance(original_custom, dict):
                        clean_custom = {}
                        for field in allowed_custom_fields:
                            # Preserve existing value if present (even if empty string)
                            if field in original_custom:
                                # Always set client_id to 36, ignore any extracted value
                                if field == "client_id":
                                    clean_custom[field] = 36
                                else:
                                    clean_custom[field] = original_custom[field]
                            else:
                                # Only set default if field is truly missing
                                if field == "source_type":
                                    clean_custom[field] = "email"
                                elif field == "client_id":
                                    clean_custom[field] = 36
                                elif field == "pay_as_present":
                                    clean_custom[field] = False
                                else:
                                    clean_custom[field] = ""
                        clean_data["custom"] = clean_custom
                    else:
                        # If custom is not a dict, preserve from final_payload if it exists
                        if "custom" in clean_data:
                            # Keep existing custom if already set
                            pass
                        else:
                            # Create default only if truly missing
                            clean_data["custom"] = {
                                "source_type": "email",
                                "shipper_email": "",
                                "sender_email": "",
                                "vendor_name": "",
                                "client_id": CLIENT_ID,
                                "attachment_key": "",
                                "attachment_bucket": "",
                                "transaction_id": email_id,
                            }
                else:
                    # If custom doesn't exist in final_payload, check if it's already in clean_data
                    if "custom" not in clean_data:
                        clean_data["custom"] = {
                            "source_type": "email",
                            "shipper_email": "",
                            "sender_email": "",
                            "vendor_name": "",
                            "client_id": CLIENT_ID,
                            "attachment_key": "",
                            "attachment_bucket": "",
                            "transaction_id": email_id,
                        }
                
                # Replace with clean data
                final_payload["data"][0] = clean_data
                # Log any fields that were removed
                removed_fields = []

                # Check top-level fields
                for field in flattened_data:
                    # Skip the custom field since it's handled separately
                    if field != "custom":
                        # If field is not in allowed_fields and not in clean_data, it was removed
                        if field not in allowed_fields:
                            removed_fields.append(field)

                # Check nested fields in shipments if present
                if "shipments" in flattened_data and isinstance(flattened_data["shipments"], list):
                    for i, shipment in enumerate(flattened_data["shipments"]):
                        if isinstance(shipment, dict):
                            # Check shipment-level fields
                            for field in shipment:
                                if field != "charges" and field not in allowed_shipment_fields:
                                    removed_fields.append(f"shipments[{i}].{field}")
                            
                            # Check charge-level fields
                            if "charges" in shipment and isinstance(shipment["charges"], list):
                                allowed_charge_fields_check = [
                                    "charge_name", "charge_gross_amount", "charge_code", "currency",
                                    "tariff_rate", "tariff_qty", "tariff_uom", "tariff_description"
                                ]
                                for j, charge in enumerate(shipment["charges"]):
                                    if isinstance(charge, dict):
                                        for field in charge:
                                            if field not in allowed_charge_fields_check:
                                                removed_fields.append(f"shipments[{i}].charges[{j}].{field}")
                            
                            # Check container-level fields
                            if "container" in shipment and isinstance(shipment["container"], list):
                                allowed_container_fields_check = [
                                    "container_id", "container_number", "container_type",
                                    "no_of_containers", "container_weight", "container_weight_uom"
                                ]
                                for j, container in enumerate(shipment["container"]):
                                    if isinstance(container, dict):
                                        for field in container:
                                            if field not in allowed_container_fields_check:
                                                removed_fields.append(f"shipments[{i}].container[{j}].{field}")

                if removed_fields:
                    logger.info(f"Removed irrelevant fields from API payload:\n{_format_json_for_log(removed_fields)}")

            # Log summary only here; full payload is logged once at "Final payload being sent to API"
            try:
                first_inv = (final_payload.get("data") or [{}])[0]
                inv_num = first_inv.get("invoice_number", "N/A")
                total = first_inv.get("total_invoice_value", "N/A")
                logger.info(f"Created API payload: invoice_number={inv_num}, total_invoice_value={total}")
            except Exception:
                logger.info("Created API payload")
            # Save the API payload to S3
            _payload_s3_uri = ""
            try:
                payload_base_name = os.path.splitext(os.path.basename(input_key))[0]
                payload_output_key = f"output/{payload_base_name}_payload.json"
                s3.put_object(
                    Bucket=S3_BUCKET,
                    Key=payload_output_key,
                    Body=json.dumps(final_payload, indent=2),
                    ContentType='application/json'
                )
                _payload_s3_uri = f"s3://{S3_BUCKET}/{payload_output_key}"
                logger.info(f"Saved API payload to {_payload_s3_uri}")
                # Copy payload to status tracker bucket
                _tracker_payload_uri = _copy_to_tracker(
                    _payload_s3_uri,
                    f"{STATUS_TRACKER_PREFIX}/{email_id}/{os.path.basename(payload_output_key)}"
                )
                if _tracker_payload_uri:
                    _payload_s3_uri = _tracker_payload_uri
            except Exception as payload_save_err:
                logger.warning(f"Failed to save API payload to S3: {str(payload_save_err)}")
                # Continue processing even if payload save fails
            
            # Step 7.5: Validate data using validator lambda (just before API call)
            logger.info("Step 7.5: Validating data with validator lambda (just before API call)")
            validation_passed = True  # Default to True if validation is skipped
            _validation_started_at = datetime.now().isoformat()
            _invoice_number_for_payload = flattened_data.get("invoice_number", "")

            try:
                validation_method = "generic"
                input_type = "invoice"
                validator_lambda_arn = os.environ.get('VALIDATOR_LAMBDA')
                logger.info(f"Validator Lambda ARN: {validator_lambda_arn}")
                
                if not validator_lambda_arn:
                    logger.warning("VALIDATOR_LAMBDA environment variable is not set")
                    # Continue without validation if no lambda configured
                    validation_passed = True
                else:
                    # Use the cleaned payload data for validation (final_payload["data"][0])
                    validation_data = final_payload["data"][0] if final_payload and "data" in final_payload and final_payload["data"] else clean_data
                    logger.info(f"Calling validator lambda with cleaned payload data")
                    
                    validation_result = invoke_validator_lambda(validator_lambda_arn, validation_data, validation_method, input_type)
                    logger.info(f"Validation result received")
                    
                    if validation_result is None:
                        raise Exception("Failed to get validation result from Lambda")

                    validation_errors = extract_validation_errors(validation_result, validation_method, input_type)
                    logger.info(f"Extracted validation errors")

                    # Check if there are required field errors
                    required_errors = validation_errors.get('required_field_errors', [])
                    
                    # Removed carrier-specific bill of lading exception logic
                    allow_exception = False
                    
                    if allow_exception:
                        # Remove bill_of_lading_number from required errors to allow processing
                        filtered_required_errors = [err for err in required_errors if err.get("field", "") != "bill_of_lading_number"]
                        validation_passed = not bool(filtered_required_errors)  # True if no remaining required errors
                        logger.info("Exception applied: Allowing invoice with missing bill of lading number due to 'Trailer rent' in charges")
                        
                        # Extract names of missing fields (use filtered list if exception was applied)
                        if filtered_required_errors:
                            has_critical_field_errors = True
                            missing_field_names = [err.get("field", "") for err in filtered_required_errors]
                            logger.info(f"Validator found required field errors (after exception): {missing_field_names}")
                    else:
                        validation_passed = not bool(required_errors)  # True if no required errors
                        
                        # Extract names of missing fields
                        if required_errors:
                            has_critical_field_errors = True
                            missing_field_names = [err.get("field", "") for err in required_errors]
                            logger.info(f"Validator found required field errors: {missing_field_names}")
                    
                    # Update email_status based on validation results
                    email_status = validation_passed

                    # ── TRANSACTION PAYLOAD: validation result ─────────────────────────
                    if validation_passed:
                        _val_payload = _build_transaction_payload(
                            transaction_id=email_id,
                            step_key="VALIDATION",
                            transaction_status="PROCESSING",
                            step_status="SUCCESS",
                            email_details=email_details,
                            attachment_id=attachment_id,
                            entity_uri=_tracker_entity_uri,
                            entity_type="freight_invoice",
                            step_started_at=_validation_started_at,
                            step_completed_at=datetime.now().isoformat(),
                            ingested_data_uri=_payload_s3_uri,
                        )
                        _save_payload_to_tracker(email_id, "VALIDATION", _val_payload)
                        logger.info("TRANSACTION_PAYLOAD | validation | %s", json.dumps(_val_payload))
                        _post_transaction_payload(_val_payload, "validation")
                    else:
                        _val_fail_payload = _build_transaction_payload(
                            transaction_id=email_id,
                            step_key="VALIDATION",
                            transaction_status="FAILED",
                            step_status="FAILED",
                            email_details=email_details,
                            attachment_id=attachment_id,
                            entity_uri=_tracker_entity_uri,
                            entity_type="freight_invoice",
                            step_error_code="VALIDATION_FAILED",
                            step_error_message=f"Required fields missing: {missing_field_names if 'missing_field_names' in locals() else []}",
                            step_started_at=_validation_started_at,
                            step_completed_at=datetime.now().isoformat(),
                            ingested_data_uri=_payload_s3_uri,
                        )
                        _save_payload_to_tracker(email_id, "VALIDATION_failed", _val_fail_payload)
                        logger.info("TRANSACTION_PAYLOAD | validation (failed) | %s", json.dumps(_val_fail_payload))
                        _post_transaction_payload(_val_fail_payload, "validation (failed)")
                    # ─────────────────────────────────────────────────────────────────

            except Exception as e:
                logger.error(f"Validation error: {str(e)}")
                validation_errors = {"required_field_errors": [], "other_field_errors": []}
                validation_passed = True  # Continue processing if validation fails
                email_status = True

                # ── TRANSACTION PAYLOAD: validation (exception) ───────────────────
                _val_exc_payload = _build_transaction_payload(
                    transaction_id=email_id,
                    step_key="VALIDATION",
                    transaction_status="PROCESSING",
                    step_status="FAILED",
                    email_details=email_details,
                    attachment_id=attachment_id,
                    entity_uri=_tracker_entity_uri,
                    entity_type="freight_invoice",
                step_error_code="VALIDATION_ERROR",
                step_error_message=str(e),
                step_started_at=_validation_started_at,
                step_completed_at=datetime.now().isoformat(),
                ingested_data_uri=_payload_s3_uri if '_payload_s3_uri' in locals() else "",
                )
                _save_payload_to_tracker(email_id, "VALIDATION_error", _val_exc_payload)
                logger.info("TRANSACTION_PAYLOAD | validation (exception) | %s", json.dumps(_val_exc_payload))
                _post_transaction_payload(_val_exc_payload, "validation (exception)")
                # ─────────────────────────────────────────────────────────────────

            # Only proceed with API call if validation passed
            if not validation_passed:
                logger.error("Validation failed - skipping API call")
                api_success = False
                api_response_data = {
                    'status_code': 0,
                    'success': False,
                    'timestamp': datetime.now().isoformat(),
                    'body': 'Validation failed - API call skipped'
                }
                
                # Update DynamoDB with FAILED status due to validation failure
                update_attachment_status(
                    dynamodb_client,
                    DYNAMODB_TABLE,
                    email_id,
                    attachment_id,
                    'FAILED',
                    error={
                        'message': f"Validation failed: {missing_field_names if 'missing_field_names' in locals() else 'Required fields missing'}",
                        'error_code': 'VALIDATION_FAILED'
                    },
                    mode=mode,
                    invoice_number=flattened_data.get("invoice_number", ""),
                    missing_critical_field=1,
                    missing_fields=missing_field_names if 'missing_field_names' in locals() else [],
                    confidence_score=average_confidence,
                    extracted_fields=extracted_fields_array,
                    api_payload=final_payload if 'final_payload' in locals() else None,  # Include API payload if available
                    api_response=api_response_data,
                    payment_terms=validate_payment_terms(flattened_data.get('payment_terms'))
                )
            else:
                # Before sending: if any shipment has empty source_name or destination_name, run LLM fallback from Textract text
                if "data" in final_payload and final_payload["data"] and "shipments" in final_payload["data"][0]:
                    def _str_value(field_val):
                        if field_val is None:
                            return ""
                        if isinstance(field_val, dict) and "value" in field_val:
                            v = field_val.get("value")
                            return (v or "").strip() if isinstance(v, str) else str(v or "").strip()
                        return (field_val or "").strip() if isinstance(field_val, str) else str(field_val or "").strip()

                    needs_fallback = False
                    for shipment in final_payload["data"][0]["shipments"]:
                        if not isinstance(shipment, dict):
                            continue
                        if not _str_value(shipment.get("source_name")) or not _str_value(shipment.get("destination_name")):
                            needs_fallback = True
                            break
                    if needs_fallback and raw_text and str(raw_text).strip():
                        logger.info("One or more shipments have empty source_name or destination_name; running LLM fallback from Textract input")
                        try:
                            fallback_result = extract_shipper_consignee_from_textract_with_llm(raw_text)
                            sn = (fallback_result.get("source_name") or "").strip()
                            dn = (fallback_result.get("destination_name") or "").strip()
                            if sn or dn:
                                for shipment in final_payload["data"][0]["shipments"]:
                                    if not isinstance(shipment, dict):
                                        continue
                                    if not _str_value(shipment.get("source_name")) and sn:
                                        shipment["source_name"] = sn
                                        shipment["source_code"] = sn
                                        logger.info(f"Filled empty source_name from LLM fallback: {sn}")
                                    if not _str_value(shipment.get("destination_name")) and dn:
                                        shipment["destination_name"] = dn
                                        shipment["destination_code"] = dn
                                        logger.info(f"Filled empty destination_name from LLM fallback: {dn}")
                        except Exception as fallback_err:
                            logger.warning(f"LLM fallback for source/destination failed: {fallback_err}")

                # Send the payload to the external API
                logger.info("Validation passed - proceeding with API call")
                
                # Replace attachment_bucket with USER_S3_BUCKET before API call
                if USER_S3_BUCKET and "data" in final_payload and isinstance(final_payload["data"], list):
                    for item in final_payload["data"]:
                        if isinstance(item, dict) and "custom" in item and isinstance(item["custom"], dict):
                            if "attachment_bucket" in item["custom"]:
                                old_bucket = item["custom"]["attachment_bucket"]
                                item["custom"]["attachment_bucket"] = USER_S3_BUCKET
                                logger.info(f"Replaced attachment_bucket from '{old_bucket}' to '{USER_S3_BUCKET}' before API call")
                
                logger.info(f"Final payload being sent to API:\n{_format_json_for_log(final_payload)}")
                _api_started_at = datetime.now().isoformat()
                response = api_handler.sending_json_to_external_api(final_payload)

                # Create API response data for DynamoDB
                api_response_data = {
                    'status_code': response.status_code if response else 0,
                    'success': response.status_code == 200 if response else False,
                    'timestamp': datetime.now().isoformat(),
                    'body': response.text if response and hasattr(response, 'text') else ''
                }
                
                logger.info(f"API response status code: {response.status_code if response else 'No response'}")

                # ── TESTING AGENT: push payload for independent AI validation ──
                _post_to_testing_agent(
                    final_payload=final_payload,
                    api_response_data=api_response_data,
                    original_input_bucket=original_input_bucket,
                    original_input_key=original_input_key,
                )
                # ──────────────────────────────────────────────────────────────

                # Check if API call was successful (status code 200)
                if response and response.status_code == 200:
                    api_success = True
                    logger.info("API request accepted successfully")

                    # ── TRANSACTION PAYLOAD: api_submission (success) ───────────────
                    _api_payload = _build_transaction_payload(
                        transaction_id=email_id,
                        step_key="INGEST",
                        transaction_status="COMPLETED",
                        step_status="SUCCESS",
                        email_details=email_details,
                        attachment_id=attachment_id,
                        entity_uri=_tracker_entity_uri,
                        entity_type="freight_invoice",
                        step_started_at=_api_started_at,
                        step_completed_at=datetime.now().isoformat(),
                        ingested_data_uri=_payload_s3_uri if '_payload_s3_uri' in locals() else "",
                        invoice_number=flattened_data.get("invoice_number", "") if 'flattened_data' in locals() and isinstance(flattened_data, dict) else "",
                    )
                    _save_payload_to_tracker(email_id, "INGEST", _api_payload)
                    logger.info("TRANSACTION_PAYLOAD | api_submission | %s", json.dumps(_api_payload))
                    _post_transaction_payload(_api_payload, "api_submission")
                    # ─────────────────────────────────────────────────────────────────
                else:
                    api_success = False
                    error_message = f"API request failed with status code: {response.status_code if response else 'No response'}"
                    logger.error(error_message)

                    # ── TRANSACTION PAYLOAD: api_submission (failure) ───────────────
                    _api_fail_payload = _build_transaction_payload(
                        transaction_id=email_id,
                        step_key="INGEST",
                        transaction_status="FAILED",
                        step_status="FAILED",
                        email_details=email_details,
                        attachment_id=attachment_id,
                        entity_uri=_tracker_entity_uri,
                        entity_type="freight_invoice",
                        step_error_code="API_ERROR",
                        step_error_message=error_message,
                        step_started_at=_api_started_at,
                        step_completed_at=datetime.now().isoformat(),
                        ingested_data_uri=_payload_s3_uri if '_payload_s3_uri' in locals() else "",
                        invoice_number=flattened_data.get("invoice_number", "") if 'flattened_data' in locals() and isinstance(flattened_data, dict) else "",
                    )
                    _save_payload_to_tracker(email_id, "INGEST_failed", _api_fail_payload)
                    logger.info("TRANSACTION_PAYLOAD | api_submission (failed) | %s", json.dumps(_api_fail_payload))
                    _post_transaction_payload(_api_fail_payload, "api_submission (failed)")
                    # ─────────────────────────────────────────────────────────────────

                    # ── REJECTION API: hit when main API returns non-200 ────────────
                    if REJECTION_ENDPOINT and "flattened_data" in locals() and isinstance(flattened_data, dict):
                        try:
                            _rej_shipments = flattened_data.get("shipments")
                            _rej_mode = ""
                            if isinstance(_rej_shipments, list) and _rej_shipments and isinstance(_rej_shipments[0], dict):
                                _rej_mode = _rej_shipments[0].get("mode") or ""
                            _rej_file_name = email_details.get("filename") or ""
                            if not _rej_file_name and original_input_key:
                                _rej_file_name = original_input_key.split("/")[-1]
                            _rej_total = flattened_data.get("total_invoice_value") or 0
                            try:
                                _rej_total = float(_rej_total)
                                if isinstance(_rej_total, float) and _rej_total.is_integer():
                                    _rej_total = int(_rej_total)
                            except (TypeError, ValueError):
                                _rej_total = 0
                            _rej_inv_no = str(flattened_data.get("invoice_number") or "NA").strip() or "NA"
                            _api_rej_payload = {
                                "data": {
                                    "transaction_id": email_id,
                                    "invoice_number": _rej_inv_no,
                                    "vendor_reference_id": flattened_data.get("vendor_reference_id") or "",
                                    "invoice_amount": _rej_total,
                                    "invoice_currency": (flattened_data.get("currency") or "").lower(),
                                    "payment_due_date": flattened_data.get("payment_due_date") or "",
                                    "mode": _rej_mode,
                                    "reason": [error_message],
                                    "attachment_bucket": original_input_bucket or "",
                                    "file_name": _rej_file_name,
                                    "file_key": original_input_key or "",
                                    "file_type": (_rej_file_name.rsplit(".", 1)[-1].lower() if "." in _rej_file_name else "pdf"),
                                }
                            }
                            logger.info("REJECTION_API | api_error payload: %s", json.dumps(_api_rej_payload))
                            _api_rej_resp = requests.post(
                                REJECTION_ENDPOINT,
                                headers={"Content-Type": "application/json", "internal-token": INTERNAL_TOKEN},
                                data=json.dumps(_api_rej_payload),
                                timeout=10,
                            )
                            logger.info("REJECTION_API | api_error response: %s", json.dumps({
                                "status_code": _api_rej_resp.status_code,
                                "success": _api_rej_resp.status_code == 200,
                                "body": _api_rej_resp.text,
                            }))
                        except Exception as _api_rej_err:
                            logger.error("REJECTION_API | api_error call failed: %s", _api_rej_err, exc_info=True)
                    # ─────────────────────────────────────────────────────────────────

                    # ── SLACK NOTIFICATION: main API returned non-200 status ────────
                    try:
                        from slack_notifier import notify_invoice_rejection

                        _slack_rej_shipments = flattened_data.get("shipments", []) if isinstance(flattened_data, dict) else []
                        _slack_rej_details = {}
                        if isinstance(_slack_rej_shipments, list) and _slack_rej_shipments and isinstance(_slack_rej_shipments[0], dict):
                            _slack_first_ship = _slack_rej_shipments[0]
                            _slack_rej_details = {
                                "Source": f"{_slack_first_ship.get('source_name', '')}",
                                "Destination": f"{_slack_first_ship.get('destination_name', '')}",
                                "Mode": _slack_first_ship.get('mode', ''),
                                "Service Level": _slack_first_ship.get('service_level', '')
                            }
                        _slack_rej_details["API Status Code"] = str(response.status_code if response else "No response")

                        _slack_sender_email = email_details.get("to", "") or email_details.get("from", "")
                        _slack_s3_path = f"s3://{original_input_bucket}/{original_input_key}" if original_input_bucket and original_input_key else None
                        _slack_inv_no = str(flattened_data.get("invoice_number") or "NA").strip() or "NA" if isinstance(flattened_data, dict) else "NA"
                        _slack_file_name = email_details.get("filename") or ""
                        if not _slack_file_name and original_input_key:
                            _slack_file_name = original_input_key.split("/")[-1]

                        notify_invoice_rejection(
                            invoice_number=_slack_inv_no,
                            email_id=email_id,
                            attachment_id=attachment_id,
                            filename=_slack_file_name,
                            sender_email=_slack_sender_email,
                            rejection_reason=error_message,
                            rejection_details=_slack_rej_details,
                            s3_path=_slack_s3_path
                        )
                        logger.info("✅ API failure notification sent to Slack")
                    except ImportError:
                        logger.warning("⚠️ slack_notifier module not found, skipping Slack notification for API failure")
                    except Exception as slack_err:
                        logger.error(f"❌ Failed to send Slack notification for API failure: {slack_err}", exc_info=True)
                    # ─────────────────────────────────────────────────────────────────

                    # Update DynamoDB with FAILED status due to API failure
                    update_attachment_status(
                        dynamodb_client,
                        DYNAMODB_TABLE,
                        email_id,
                        attachment_id,
                        'FAILED',
                        error={
                            'message': error_message,
                            'error_code': 'API_ERROR'
                        },
                        mode=mode,
                        invoice_number=flattened_data.get("invoice_number", ""),
                        missing_fields=[],
                        confidence_score=average_confidence,
                        extracted_fields=extracted_fields_array,
                        api_response=api_response_data,
                        api_payload=final_payload if 'final_payload' in locals() else None,  # Include API payload
                        payment_terms=validate_payment_terms(flattened_data.get('payment_terms'))
                    )
        
        except Exception as e:
            api_success = False
            logger.error(f"Error processing payload or sending data to API: {str(e)}")

            # ── TRANSACTION PAYLOAD: api_submission (exception) ───────────────────
            _api_exc_payload = _build_transaction_payload(
                transaction_id=email_id,
                step_key="INGEST",
                transaction_status="FAILED",
                step_status="FAILED",
                email_details=email_details,
                attachment_id=attachment_id if 'attachment_id' in locals() else "",
                entity_uri=_tracker_entity_uri if '_tracker_entity_uri' in locals() else "",
                entity_type="freight_invoice",
                step_error_code="API_EXCEPTION",
                step_error_message=str(e),
                step_started_at=_api_started_at if '_api_started_at' in locals() else "",
                step_completed_at=datetime.now().isoformat(),
                ingested_data_uri=_payload_s3_uri if '_payload_s3_uri' in locals() else "",
                invoice_number=flattened_data.get("invoice_number", "") if 'flattened_data' in locals() and isinstance(flattened_data, dict) else "",
            )
            _save_payload_to_tracker(email_id, "INGEST_error", _api_exc_payload)
            logger.info("TRANSACTION_PAYLOAD | api_submission (exception) | %s", json.dumps(_api_exc_payload))
            _post_transaction_payload(_api_exc_payload, "api_submission (exception)")
            # ─────────────────────────────────────────────────────────────────────

            # ── REJECTION API: hit when main API call raises an exception ─────────
            if REJECTION_ENDPOINT and "flattened_data" in locals() and isinstance(flattened_data, dict):
                try:
                    _rej_shipments = flattened_data.get("shipments")
                    _rej_mode = ""
                    if isinstance(_rej_shipments, list) and _rej_shipments and isinstance(_rej_shipments[0], dict):
                        _rej_mode = _rej_shipments[0].get("mode") or ""
                    _rej_file_name = email_details.get("filename") or ""
                    if not _rej_file_name and original_input_key:
                        _rej_file_name = original_input_key.split("/")[-1]
                    _rej_total = flattened_data.get("total_invoice_value") or 0
                    try:
                        _rej_total = float(_rej_total)
                        if isinstance(_rej_total, float) and _rej_total.is_integer():
                            _rej_total = int(_rej_total)
                    except (TypeError, ValueError):
                        _rej_total = 0
                    _rej_inv_no = str(flattened_data.get("invoice_number") or "NA").strip() or "NA"
                    _exc_rej_payload = {
                        "data": {
                            "transaction_id": email_id,
                            "invoice_number": _rej_inv_no,
                            "vendor_reference_id": flattened_data.get("vendor_reference_id") or "",
                            "invoice_amount": _rej_total,
                            "invoice_currency": (flattened_data.get("currency") or "").lower(),
                            "payment_due_date": flattened_data.get("payment_due_date") or "",
                            "mode": _rej_mode,
                            "reason": [f"API request failed with exception: {str(e)}"],
                            "attachment_bucket": original_input_bucket or "",
                            "file_name": _rej_file_name,
                            "file_key": original_input_key or "",
                            "file_type": (_rej_file_name.rsplit(".", 1)[-1].lower() if "." in _rej_file_name else "pdf"),
                        }
                    }
                    logger.info("REJECTION_API | api_exception payload: %s", json.dumps(_exc_rej_payload))
                    _exc_rej_resp = requests.post(
                        REJECTION_ENDPOINT,
                        headers={"Content-Type": "application/json", "internal-token": INTERNAL_TOKEN},
                        data=json.dumps(_exc_rej_payload),
                        timeout=10,
                    )
                    logger.info("REJECTION_API | api_exception response: %s", json.dumps({
                        "status_code": _exc_rej_resp.status_code,
                        "success": _exc_rej_resp.status_code == 200,
                        "body": _exc_rej_resp.text,
                    }))
                except Exception as _exc_rej_err:
                    logger.error("REJECTION_API | api_exception call failed: %s", _exc_rej_err, exc_info=True)
            # ─────────────────────────────────────────────────────────────────────

            # ── SLACK NOTIFICATION: main API call raised an exception ─────────────
            if "flattened_data" in locals() and isinstance(flattened_data, dict):
                try:
                    from slack_notifier import notify_invoice_rejection

                    _slack_rej_shipments = flattened_data.get("shipments", [])
                    _slack_rej_details = {}
                    if isinstance(_slack_rej_shipments, list) and _slack_rej_shipments and isinstance(_slack_rej_shipments[0], dict):
                        _slack_first_ship = _slack_rej_shipments[0]
                        _slack_rej_details = {
                            "Source": f"{_slack_first_ship.get('source_name', '')}",
                            "Destination": f"{_slack_first_ship.get('destination_name', '')}",
                            "Mode": _slack_first_ship.get('mode', ''),
                            "Service Level": _slack_first_ship.get('service_level', '')
                        }

                    _slack_sender_email = email_details.get("to", "") or email_details.get("from", "")
                    _slack_s3_path = f"s3://{original_input_bucket}/{original_input_key}" if original_input_bucket and original_input_key else None
                    _slack_inv_no = str(flattened_data.get("invoice_number") or "NA").strip() or "NA"
                    _slack_file_name = email_details.get("filename") or ""
                    if not _slack_file_name and original_input_key:
                        _slack_file_name = original_input_key.split("/")[-1]

                    notify_invoice_rejection(
                        invoice_number=_slack_inv_no,
                        email_id=email_id,
                        attachment_id=attachment_id if 'attachment_id' in locals() else "",
                        filename=_slack_file_name,
                        sender_email=_slack_sender_email,
                        rejection_reason=f"API request failed with exception: {str(e)}",
                        rejection_details=_slack_rej_details,
                        s3_path=_slack_s3_path
                    )
                    logger.info("✅ API exception notification sent to Slack")
                except ImportError:
                    logger.warning("⚠️ slack_notifier module not found, skipping Slack notification for API exception")
                except Exception as slack_err:
                    logger.error(f"❌ Failed to send Slack notification for API exception: {slack_err}", exc_info=True)
            # ─────────────────────────────────────────────────────────────────────

            # Create API error response data for DynamoDB
            api_response_data = {
                'status_code': 0,
                'success': False,
                'timestamp': datetime.now().isoformat(),
                'body': str(e)
            }
            
            # Update DynamoDB with FAILED status due to API exception
            update_attachment_status(
                dynamodb_client,
                DYNAMODB_TABLE,
                email_id,
                attachment_id,
                'FAILED',
                error={
                    'message': f"API request failed with exception: {str(e)}",
                    'error_code': 'API_EXCEPTION'
                },
                mode=mode,
                invoice_number=flattened_data.get("invoice_number", ""),
                missing_fields=[],
                confidence_score=average_confidence,
                extracted_fields=extracted_fields_array,
                api_response=api_response_data,
                api_payload=final_payload if 'final_payload' in locals() else None,  # Include API payload
                payment_terms=validate_payment_terms(flattened_data.get('payment_terms'))
            )

        # Format and save the output result to the output bucket
        base_name = os.path.splitext(os.path.basename(input_key))[0]
        output_key = f"{output_prefix}{base_name}.json"
        
        # Only proceed with successful completion if both validation passed and API call succeeded
        if email_status and api_success:
            try:
                # Set payment_terms to "collect" in extracted_info only if it's empty, otherwise keep LLM extraction
                current_payment_terms_value = None
                if "payment_terms" in extracted_info:
                    if isinstance(extracted_info["payment_terms"], dict) and "value" in extracted_info["payment_terms"]:
                        current_payment_terms_value = extracted_info["payment_terms"]["value"]
                    elif isinstance(extracted_info["payment_terms"], str):
                        current_payment_terms_value = extracted_info["payment_terms"]
                
                if not current_payment_terms_value or (isinstance(current_payment_terms_value, str) and current_payment_terms_value.strip() == ""):
                    extracted_info["payment_terms"] = {"value": "collect", "explanation": "Set to collect (was empty)", "confidence": 1.0}
                    logger.info("Set payment_terms to 'collect' in extracted_info for S3 output (was empty)")
                else:
                    # Keep the LLM extraction, but ensure it's in the correct format
                    if isinstance(extracted_info["payment_terms"], dict):
                        logger.info(f"Keeping LLM-extracted payment_terms in extracted_info: '{current_payment_terms_value}'")
                    else:
                        extracted_info["payment_terms"] = {"value": current_payment_terms_value, "explanation": "LLM extraction", "confidence": 1.0}
                        logger.info(f"Formatted and kept LLM-extracted payment_terms in extracted_info: '{current_payment_terms_value}'")
                
                # Get invoice_date and ship_date for setting shipment_creation_date
                invoice_date_value = None
                if "invoice_date" in extracted_info:
                    if isinstance(extracted_info["invoice_date"], dict) and "value" in extracted_info["invoice_date"]:
                        invoice_date_value = extracted_info["invoice_date"]["value"]
                    elif isinstance(extracted_info["invoice_date"], str):
                        invoice_date_value = extracted_info["invoice_date"]
                
                ship_date_value = None
                if "ship_date" in extracted_info:
                    if isinstance(extracted_info["ship_date"], dict) and "value" in extracted_info["ship_date"]:
                        ship_date_value = extracted_info["ship_date"]["value"]
                    elif isinstance(extracted_info["ship_date"], str):
                        ship_date_value = extracted_info["ship_date"]
                
                # Get vendor_reference_id to determine default mode for S3 output
                vendor_reference_id_value = None
                if "vendor_reference_id" in extracted_info:
                    if isinstance(extracted_info["vendor_reference_id"], dict) and "value" in extracted_info["vendor_reference_id"]:
                        vendor_reference_id_value = extracted_info["vendor_reference_id"]["value"]
                    elif isinstance(extracted_info["vendor_reference_id"], str):
                        vendor_reference_id_value = extracted_info["vendor_reference_id"]
                
                default_mode_value = get_default_mode_from_vendor_reference_id(vendor_reference_id_value if vendor_reference_id_value else "")
                
                # Set mode: use LLM output as-is; only set default when mode is empty (no RXO-specific override)
                if "shipments" in extracted_info and isinstance(extracted_info["shipments"], list):
                    for i, shipment in enumerate(extracted_info["shipments"]):
                        if isinstance(shipment, dict):
                            current_mode_field = shipment.get("mode", {})
                            if isinstance(current_mode_field, dict):
                                current_mode = current_mode_field.get("value", "")
                            else:
                                current_mode = str(current_mode_field) if current_mode_field else ""
                            
                            if not current_mode or (isinstance(current_mode, str) and current_mode.strip() == ""):
                                shipment["mode"] = {"value": default_mode_value, "explanation": f"Set to {default_mode_value} based on vendor '{vendor_reference_id_value}'", "confidence": 1.0}
                                logger.info(f"Set mode to '{default_mode_value}' in shipment for S3 output based on vendor '{vendor_reference_id_value}'")
                            else:
                                logger.info(f"Preserving LLM-extracted mode '{current_mode}' in shipment for S3 output")
                            
                            # RXO (XPON): if mode is other than Road or Air, map to Air for consistency with API
                            vendor_ref_upper_s3 = str(vendor_reference_id_value or "").upper().strip()
                            if vendor_ref_upper_s3 == "XPON":
                                mode_val = (current_mode or "").strip().upper()
                                if mode_val and mode_val not in ("ROAD", "AIR"):
                                    shipment["mode"] = {"value": "Air", "explanation": f"RXO invoice: mapped from '{current_mode}' to Air for ingestion", "confidence": 1.0}
                                    logger.info(f"RXO (XPON) invoice: mapped mode from '{current_mode}' to 'Air' for S3 output")
                            
                            # Set shipment_creation_date only if not already present
                            # Priority: 1) ship_date from shipment, 2) ship_date from invoice, 3) invoice_date
                            current_shipment_date_field = shipment.get("shipment_creation_date", {})
                            if isinstance(current_shipment_date_field, dict):
                                current_shipment_date = current_shipment_date_field.get("value", "")
                            else:
                                current_shipment_date = str(current_shipment_date_field) if current_shipment_date_field else ""
                            
                            if not current_shipment_date or (isinstance(current_shipment_date, str) and current_shipment_date.strip() == ""):
                                # Check if ship_date exists in shipment level first
                                shipment_ship_date_field = shipment.get("ship_date", {})
                                if isinstance(shipment_ship_date_field, dict):
                                    shipment_ship_date = shipment_ship_date_field.get("value", "")
                                else:
                                    shipment_ship_date = str(shipment_ship_date_field) if shipment_ship_date_field else ""
                                
                                if shipment_ship_date and isinstance(shipment_ship_date, str) and shipment_ship_date.strip():
                                    shipment["shipment_creation_date"] = {"value": shipment_ship_date, "explanation": "Set to ship_date from shipment", "confidence": 1.0}
                                    logger.info(f"Set shipment_creation_date to ship_date '{shipment_ship_date}' from shipment for S3 output")
                                elif ship_date_value:
                                    shipment["shipment_creation_date"] = {"value": ship_date_value, "explanation": "Set to ship_date from invoice", "confidence": 1.0}
                                    logger.info(f"Set shipment_creation_date to ship_date '{ship_date_value}' from invoice level for S3 output")
                                elif invoice_date_value:
                                    # Only set shipment_creation_date to invoice_date if mode is "Ocean"
                                    current_mode_check = shipment.get("mode", {})
                                    if isinstance(current_mode_check, dict):
                                        current_mode_val = current_mode_check.get("value", "")
                                    else:
                                        current_mode_val = str(current_mode_check) if current_mode_check else ""
                                    
                                    if current_mode_val == "Ocean":
                                        shipment["shipment_creation_date"] = {"value": invoice_date_value, "explanation": "Set to invoice_date (mode is Ocean, ship_date not available)", "confidence": 1.0}
                                        logger.info(f"Set shipment_creation_date to invoice_date '{invoice_date_value}' (mode is 'Ocean', ship_date not available) for S3 output")
                                    else:
                                        logger.info(f"Skipping shipment_creation_date setting (mode is '{current_mode_val}', not 'Ocean') for S3 output")
                                else:
                                    logger.warning("Neither ship_date nor invoice_date available, cannot set shipment_creation_date for S3 output")
                            else:
                                logger.info(f"Preserving existing shipment_creation_date '{current_shipment_date}' in shipment for S3 output")
                            
                            # Consolidate charges with the same name within each shipment (structured format)
                            consolidated_shipment = consolidate_charges_in_shipment_structured(shipment)
                            extracted_info["shipments"][i] = consolidated_shipment
                            logger.info(f"Consolidated charges in shipment {i} for S3 output")
                
                # Create a properly formatted output JSON with confidence score
                output_json = format_output_json(
                    extracted_info, 
                    email_details,
                    average_confidence  # Include confidence score in output JSON
                )
                
                s3.put_object(
                    Bucket=output_bucket,
                    Key=output_key,
                    Body=json.dumps(output_json, indent=2),
                    ContentType='application/json'
                )
                logger.info(f"Saved results to s3://{output_bucket}/{output_key}")
                
                # Extract invoice number 
                invoice_number = flattened_data.get("invoice_number", "")
                
                # Update attachment status to COMPLETED with all flags set to 0 (success)
                # Only update as COMPLETED if both validation passed and API call succeeded
                # Check if bill of lading exception was applied
                missing_critical_field_flag = 0
                missing_fields_array = []
                
                if 'allow_exception' in locals() and allow_exception:
                    # Exception was applied, so bill_of_lading_number was missing but allowed
                    missing_critical_field_flag = 0  # Still consider it successful since exception was applied
                    missing_fields_array = ["bill_of_lading_number"]  # Track that this field was missing but allowed
                    logger.info("Marking as COMPLETED with bill of lading exception applied")
                else:
                    missing_fields_array = []  # No missing fields
                
                update_attachment_status(
                    dynamodb_client,
                    DYNAMODB_TABLE,
                    email_id,
                    attachment_id,
                    'COMPLETED',
                    output_path=f"s3://{output_bucket}/{output_key}",
                    mode=mode,
                    invoice_number=invoice_number,
                    missing_critical_field=missing_critical_field_flag,  # Use calculated flag
                    textract_failed=0,             # No Textract failures
                    classification_failed=0,       # No classification failures
                    extraction_failed=0,           # No extraction failures
                    format_failed=0,               # No format failures
                    missing_fields=missing_fields_array,  # Use calculated missing fields array
                    confidence_score=average_confidence,  # Store confidence score
                    extracted_fields=extracted_fields_array,  # Include extracted fields array
                    api_response=api_response_data,  # Include API response details
                    api_payload=final_payload,  # Include API request payload
                    payment_terms=validate_payment_terms(flattened_data.get('payment_terms')),  # Include payment terms with validation
                    textract_model_result=textract_model_result if textract_model_result else None,
                    image_model_result=image_model_result if image_model_result else None,
                )
            except Exception as e:
                logger.error(f"Failed to save results to S3: {str(e)}")
                update_attachment_status(
                    dynamodb_client,
                    DYNAMODB_TABLE,
                    email_id,
                    attachment_id,
                    'FAILED',
                    error={
                        'message': str(e),
                        'error_code': 'S3_SAVE_ERROR'
                    },
                    missing_fields=[],         # Empty array since this is not a validation error
                    confidence_score=average_confidence,  # Store confidence score despite failure
                    extracted_fields=extracted_fields_array,  # Include extracted fields array
                    api_response=api_response_data,  # Include API response details if available
                    api_payload=final_payload if 'final_payload' in locals() else None,  # Include API payload if available
                    payment_terms=validate_payment_terms(flattened_data.get('payment_terms'))  # Include payment terms with validation
                )
                raise
        elif not email_status:
            # This is the case where validation has failed with required field errors
            # Check if the exception was applied but there are still other missing fields
            if 'allow_exception' in locals() and allow_exception and missing_field_names:
                # Exception was applied but there are still other missing fields besides bill_of_lading_number
                error_message = f"Required fields missing (bill of lading exception applied but other fields still missing): {missing_field_names}"
                logger.warning(error_message)
            else:
                error_message = "Required field missing"

            # ── REJECTION API: hit when validation fails with missing/invalid fields ──
            rejection_final_payload = None
            rejection_api_response_data = None
            if REJECTION_ENDPOINT and "flattened_data" in locals() and isinstance(flattened_data, dict):
                try:
                    _rej_shipments = flattened_data.get("shipments")
                    _rej_mode = ""
                    if isinstance(_rej_shipments, list) and _rej_shipments and isinstance(_rej_shipments[0], dict):
                        _rej_mode = _rej_shipments[0].get("mode") or ""

                    _rej_file_name = email_details.get("filename") or ""
                    if not _rej_file_name and original_input_key:
                        _rej_file_name = original_input_key.split("/")[-1]

                    _rej_reasons = [error_message]
                    _ve = validation_errors if "validation_errors" in locals() else {}
                    for _bucket in ("required_field_errors", "other_field_errors"):
                        for _err in (_ve.get(_bucket) or []):
                            if isinstance(_err, dict):
                                _field = _err.get("field", "") or ""
                                _msg = (_err.get("message", "") or "").strip()
                                _part = f"{_field}: {_msg}" if _field else _msg
                                if _part and _part not in _rej_reasons:
                                    _rej_reasons.append(_part)

                    _rej_total = flattened_data.get("total_invoice_value") or 0
                    try:
                        _rej_total = float(_rej_total)
                        if isinstance(_rej_total, float) and _rej_total.is_integer():
                            _rej_total = int(_rej_total)
                    except (TypeError, ValueError):
                        _rej_total = 0

                    _rej_inv_no = str(flattened_data.get("invoice_number") or "NA").strip() or "NA"
                    rejection_final_payload = {
                        "data": {
                            "transaction_id": email_id,
                            "invoice_number": _rej_inv_no,
                            "vendor_reference_id": flattened_data.get("vendor_reference_id") or "",
                            "invoice_amount": _rej_total,
                            "invoice_currency": (flattened_data.get("currency") or "").lower(),
                            "payment_due_date": flattened_data.get("payment_due_date") or "",
                            "mode": _rej_mode,
                            "reason": _rej_reasons,
                            "attachment_bucket": original_input_bucket or "",
                            "file_name": _rej_file_name,
                            "file_key": original_input_key or "",
                            "file_type": (_rej_file_name.rsplit(".", 1)[-1].lower() if "." in _rej_file_name else "pdf"),
                        }
                    }
                    logger.info("REJECTION_API | payload: %s", json.dumps(rejection_final_payload))
                    _rej_resp = requests.post(
                        REJECTION_ENDPOINT,
                        headers={"Content-Type": "application/json", "internal-token": INTERNAL_TOKEN},
                        data=json.dumps(rejection_final_payload),
                        timeout=10,
                    )
                    rejection_api_response_data = {
                        "status_code": _rej_resp.status_code,
                        "success": _rej_resp.status_code == 200,
                        "timestamp": datetime.now().isoformat(),
                        "body": _rej_resp.text,
                    }
                    logger.info("REJECTION_API | response: %s", json.dumps(rejection_api_response_data))
                except Exception as _rej_err:
                    logger.error("REJECTION_API | call failed: %s", _rej_err, exc_info=True)
            # ─────────────────────────────────────────────────────────────────────────

            # ── SLACK NOTIFICATION: required field validation failure ─────────────
            if "flattened_data" in locals() and isinstance(flattened_data, dict):
                try:
                    from slack_notifier import notify_invoice_rejection

                    _slack_rej_shipments = flattened_data.get("shipments", [])
                    _slack_rej_details = {}
                    if isinstance(_slack_rej_shipments, list) and _slack_rej_shipments and isinstance(_slack_rej_shipments[0], dict):
                        _slack_first_ship = _slack_rej_shipments[0]
                        _slack_rej_details = {
                            "Source": f"{_slack_first_ship.get('source_name', '')}",
                            "Destination": f"{_slack_first_ship.get('destination_name', '')}",
                            "Mode": _slack_first_ship.get('mode', ''),
                            "Service Level": _slack_first_ship.get('service_level', '')
                        }

                    _slack_sender_email = email_details.get("to", "") or email_details.get("from", "")
                    _slack_s3_path = f"s3://{original_input_bucket}/{original_input_key}" if original_input_bucket and original_input_key else None
                    _slack_inv_no = str(flattened_data.get("invoice_number") or "NA").strip() or "NA"
                    _slack_file_name = email_details.get("filename") or ""
                    if not _slack_file_name and original_input_key:
                        _slack_file_name = original_input_key.split("/")[-1]

                    notify_invoice_rejection(
                        invoice_number=_slack_inv_no,
                        email_id=email_id,
                        attachment_id=attachment_id if 'attachment_id' in locals() else "",
                        filename=_slack_file_name,
                        sender_email=_slack_sender_email,
                        rejection_reason="; ".join(_rej_reasons) if '_rej_reasons' in locals() else error_message,
                        rejection_details=_slack_rej_details,
                        s3_path=_slack_s3_path
                    )
                    logger.info("✅ Required field validation rejection notification sent to Slack")
                except ImportError:
                    logger.warning("⚠️ slack_notifier module not found, skipping Slack notification for validation failure")
                except Exception as slack_err:
                    logger.error(f"❌ Failed to send Slack notification for validation failure: {slack_err}", exc_info=True)
            # ─────────────────────────────────────────────────────────────────────────

            # ── MISSING FIELDS REPLY EMAIL ─────────────────────────────────────────
            try:
                _mandatory_field_display = {
                    "invoice_number": "Invoice Number",
                    "invoice_date": "Invoice Date",
                    "vendor_reference_id": "Vendor Reference ID",
                    "payment_due_date": "Payment Due Date",
                    "bill_of_lading_number": "Tendered Bill of Lading Number",
                    "currency": "Currency",
                    "total_invoice_value": "Total Invoice Value",
                    "payment_terms": "Payment Terms",
                    "assessable_value": "Assessable Value",
                    "shipments": "Shipments",
                    "shipment_number": "Tendered Shipment Number",
                }
                _sender_email = _extract_email_address(email_details.get("from", ""))
                _inv_no_display = str(flattened_data.get("invoice_number") or "N/A").strip() or "N/A"
                _missing_display = [
                    _mandatory_field_display.get(f, f.replace("_", " ").title())
                    for f in (missing_field_names or [])
                ]
                _missing_bullets_html = "".join(
                    f"<li style='margin: 6px 0;'>{name}</li>" for name in _missing_display
                )

                _missing_email_subject = f"Invoice Processing Failed: {_inv_no_display} – Action Required"
                _missing_email_body = f"""
<p>Your invoice submission could not be ingested into the <strong>GE Appliances</strong> system due to missing mandatory fields.</p>
<p><strong>Submission Reference:</strong> {_inv_no_display}</p>
<p><strong>Invoice:</strong>&nbsp;Please find the original invoice attached to this email for your reference.</p>
<p style="color: #cc0000; font-weight: bold;">&#9888; The following fields are missing:</p>
<ul style="background: #fff8f0; border-left: 4px solid #cc0000; padding: 12px 20px; list-style-type: disc;">
  {_missing_bullets_html}
</ul>
<p>Please update your invoice to include all the fields listed above and resubmit. No further action will be taken on the current submission.</p>
<p>For questions or format guidance, contact your point of contact at GE Appliances.</p>"""

                if _sender_email and _missing_display:
                    # Fetch the original PDF from S3 to attach it to the rejection email
                    _pdf_attachments = []
                    try:
                        _pdf_filename = (
                            email_details.get("filename")
                            or (original_input_key.split("/")[-1] if original_input_key else None)
                            or "invoice.pdf"
                        )
                        _s3_obj = s3.get_object(Bucket=original_input_bucket, Key=original_input_key)
                        _pdf_bytes = _s3_obj["Body"].read()
                        _pdf_attachments = [(_pdf_filename, _pdf_bytes)]
                        logger.info(
                            f"MISSING_FIELDS_EMAIL | fetched PDF attachment '{_pdf_filename}' "
                            f"from s3://{original_input_bucket}/{original_input_key} "
                            f"({len(_pdf_bytes)} bytes)"
                        )
                    except Exception as _pdf_err:
                        logger.warning(
                            f"MISSING_FIELDS_EMAIL | could not fetch PDF from S3: {_pdf_err}. "
                            f"Sending email without attachment."
                        )

                    send_email(
                        smtp_server=SMTP_SERVER,
                        smtp_port=SMTP_PORT,
                        smtp_username=SMTP_USERNAME,
                        smtp_password=SMTP_PASSWORD,
                        from_email=FROM_EMAIL,
                        to_email=_sender_email,
                        subject=_missing_email_subject,
                        body=_missing_email_body,
                        json_data={},
                        message_id=email_details.get("status_message_id") or email_details.get("message_id"),
                        quoted_sender=email_details.get("status_email_sender") or email_details.get("from"),
                        quoted_date=email_details.get("status_email_date") or email_details.get("date"),
                        quoted_subject=email_details.get("status_email_subject") or email_details.get("subject"),
                        quoted_body=email_details.get("status_email_body") or email_details.get("original_body"),
                        attachments=_pdf_attachments or None,
                    )
                    logger.info(
                        "MISSING_FIELDS_EMAIL | reply sent to %s for invoice %s | missing=%s",
                        _sender_email, _inv_no_display, _missing_display
                    )
                elif not _sender_email:
                    logger.warning("MISSING_FIELDS_EMAIL | skipped — no sender email address found")
                else:
                    logger.warning("MISSING_FIELDS_EMAIL | skipped — missing_field_names is empty")
            except Exception as _mfe_err:
                logger.error("MISSING_FIELDS_EMAIL | failed to send: %s", _mfe_err, exc_info=True)
            # ─────────────────────────────────────────────────────────────────────────

            update_attachment_status(
                dynamodb_client,
                DYNAMODB_TABLE,
                email_id,
                attachment_id,
                'FAILED',
                error={
                    'message': error_message,
                    'error_code': 'FIELD_MISSING'
                },
                missing_critical_field=1,      # Set to 1 since validator found required field errors
                missing_fields=missing_field_names,  # Add the array of missing field names
                confidence_score=average_confidence,  # Store confidence score even for failed validation
                extracted_fields=extracted_fields_array,  # Include extracted fields array
                api_response=rejection_api_response_data,
                api_payload=rejection_final_payload,
                payment_terms=validate_payment_terms(flattened_data.get('payment_terms'))  # Include payment terms with validation
            )
        # Note: The case where email_status is True but api_success is False is handled above in the API section
          
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Invoice processed successfully',
                'average_confidence': average_confidence,
                'output_location': f"s3://{output_bucket}/{output_key}",
                'missing_critical_field': 1 if has_critical_field_errors else 0,
                'missing_fields': missing_field_names if 'missing_field_names' in locals() else [],
                'api_success': api_success,
                'bill_of_lading_exception_applied': allow_exception if 'allow_exception' in locals() else False
            }, indent=2)
        }
        
        # Log final cost summary before successful completion
        try:
            cost_summary = cost_tracker.log_final_summary(job_id=email_id if 'email_id' in locals() else None)
            logger.info(f"Cost tracking completed successfully for invoice processing")
        except Exception as cost_err:
            logger.warning(f"Failed to generate cost summary: {str(cost_err)}")
        
    except Exception as e:
        logger.error(f"Error processing invoice: {str(e)}")
        
        # Log cost summary even in error case
        try:
            cost_summary = cost_tracker.log_final_summary(job_id=email_id if 'email_id' in locals() else None)
            logger.info(f"Cost tracking completed despite processing error: {cost_summary}")
        except Exception as cost_err:
            logger.warning(f"Failed to generate cost summary in error case: {str(cost_err)}")
        
        # If we have attachment info, update status (skip if already set to UNCLASSIFIED)
        try:
            if 'email_id' in locals() and 'attachment_id' in locals():
                # Do not overwrite UNCLASSIFIED with FAILED - we already updated DynamoDB for unclassified documents
                error_msg = str(e)
                if 'Unclassified document' in error_msg or 'unsupported or unknown carrier' in error_msg:
                    logger.info("Skipping DynamoDB update - attachment already set to UNCLASSIFIED")
                else:
                    # If we have a confidence score, include it
                    confidence_to_store = average_confidence if 'average_confidence' in locals() and average_confidence is not None else None
                    extracted_fields_to_store = extracted_fields_array if 'extracted_fields_array' in locals() else None
                    api_response_to_store = api_response_data if 'api_response_data' in locals() else None
                    
                    update_attachment_status(
                        dynamodb_client,
                        DYNAMODB_TABLE,
                        email_id,
                        attachment_id,
                        'FAILED',
                        error={
                            'message': error_msg,
                            'error_code': 'PROCESSING_ERROR'
                        },
                        missing_fields=[],  # Empty array since this is a general error
                        confidence_score=confidence_to_store,  # Include confidence score if available
                        extracted_fields=extracted_fields_to_store,  # Include extracted fields array if available
                        api_response=api_response_to_store,  # Include API response details if available
                        api_payload=final_payload if 'final_payload' in locals() else None,  # Include API payload if available
                        payment_terms=validate_payment_terms(flattened_data.get('payment_terms')) if 'flattened_data' in locals() else 'collect'  # Include payment terms if available with validation
                    )
        except Exception as db_err:
            logger.error(f"Failed to update DynamoDB: {str(db_err)}")
            
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            }, indent=2)
        }