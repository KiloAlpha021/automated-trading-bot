"""Provider-neutral market-data acquisition contracts."""

from automated_trading_bot.market_data.acquisition import (
    MAX_ACQUISITION_EVIDENCE_REFS,
    MAX_ENTITLEMENT_EVIDENCE_REFS,
    AcquisitionId,
    AcquisitionRecord,
    ProviderNativeId,
    SourceInterpretationId,
    SourceOrderAuthority,
    TemporalCapability,
    verify_acquisition_content_digest,
)

__all__ = [
    "MAX_ACQUISITION_EVIDENCE_REFS",
    "MAX_ENTITLEMENT_EVIDENCE_REFS",
    "AcquisitionId",
    "AcquisitionRecord",
    "ProviderNativeId",
    "SourceInterpretationId",
    "SourceOrderAuthority",
    "TemporalCapability",
    "verify_acquisition_content_digest",
]
