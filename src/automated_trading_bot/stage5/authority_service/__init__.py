"""Deployment-owned Stage 5 authority service contracts.

The package contains the service-side state machine and a verify-only client
boundary.  Creating these Python objects does not establish operational
authority; operational construction requires protected Windows deployment
configuration, principals, and a non-exportable signing key.
"""

from automated_trading_bot.stage5.authority_service.persistence import (
    ApdDisposition,
    AuthorityDatabase,
    AuthorityState,
    ExternalWitness,
    RecoveryDecision,
    classify_recovery,
)
from automated_trading_bot.stage5.authority_service.protocol import (
    AdmissionEnvelope,
    AuthorityServiceError,
    SignedAdmission,
)
from automated_trading_bot.stage5.authority_service.client import (
    I01AuthorityServiceClient,
    VerifiedOperationalAdmission,
    operational_authority_fence,
    require_current_operational_admission,
)

__all__ = (
    "AdmissionEnvelope",
    "ApdDisposition",
    "AuthorityDatabase",
    "AuthorityServiceError",
    "AuthorityState",
    "ExternalWitness",
    "I01AuthorityServiceClient",
    "RecoveryDecision",
    "SignedAdmission",
    "VerifiedOperationalAdmission",
    "classify_recovery",
    "operational_authority_fence",
    "require_current_operational_admission",
)
