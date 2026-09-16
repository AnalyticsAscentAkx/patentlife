"""patentlife - which patents a company still holds, where, and until when.

US-only in this version: statutory term and lapse-for-non-payment derived
from USPTO public-domain bulk data. Non-US jurisdictions need EPO INPADOC
legal event data, which is free but licensed and cannot be redistributed.
"""

from .maintfee import MaintFeeEvent, iter_events, parse_line
from .status import CodeMap, PatentStatus, derive, statutory_expiry

__version__ = "0.1.0"
__all__ = [
    "MaintFeeEvent",
    "iter_events",
    "parse_line",
    "CodeMap",
    "PatentStatus",
    "derive",
    "statutory_expiry",
]
