"""Stage 5 portfolio-authorization input contracts; no trading authority."""

from automated_trading_bot.stage5.contracts import (
    CapitalSnapshot,
    CapitalSnapshotError,
    CapitalSnapshotPort,
    CapitalSnapshotRequest,
    EngineeringCapitalSnapshotProvider,
    FinancialComponent,
    PortfolioIntent,
    ProviderAuthority,
    adapt_portfolio_intent,
)
from automated_trading_bot.stage5.provider_authority import (
    AuthorityFence,
    AuthorityGeneration,
    AuthorityHead,
    AuthorityScope,
    AuthorityStatus,
    BootstrapEvidence,
    ProviderAuthorityError,
    ProviderImplementationDescriptor,
)

__all__ = (
    "AuthorityFence",
    "AuthorityGeneration",
    "AuthorityHead",
    "AuthorityScope",
    "AuthorityStatus",
    "BootstrapEvidence",
    "CapitalSnapshot",
    "CapitalSnapshotError",
    "CapitalSnapshotPort",
    "CapitalSnapshotRequest",
    "EngineeringCapitalSnapshotProvider",
    "FinancialComponent",
    "PortfolioIntent",
    "ProviderAuthority",
    "ProviderAuthorityError",
    "ProviderImplementationDescriptor",
    "adapt_portfolio_intent",
)
