import uuid, datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any

class ACPIntent(Enum):
    INITIATE_APPLICATION = "INITIATE_APPLICATION"
    REQUEST_INFORMATION = "REQUEST_INFORMATION"
    PROVIDE_INFORMATION = "PROVIDE_INFORMATION"
    NEGOTIATE_TERMS = "NEGOTIATE_TERMS"
    ACCEPT_OFFER = "ACCEPT_OFFER"
    BANK_CHAT = "BANK_CHAT"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"

class ApplicationState(Enum):
    INFO_REQUESTED = "INFO_REQUESTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

@dataclass
class ACPMessage:
    sender_id: str
    receiver_id: str
    intent: ACPIntent
    session_id: str
    payload: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

@dataclass
class ACPResponse:
    success: bool
    state: ApplicationState
    message: str
    payload: Dict[str, Any]
