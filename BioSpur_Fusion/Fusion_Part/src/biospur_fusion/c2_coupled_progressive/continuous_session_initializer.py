"""Action-independent public initializer for the complete C2 session."""

from .prospective_action00_initializer import (
    BootstrapPoseReadiness,
    BootstrapPoseReadinessStatus,
    CalibratedImuErrorStateOrigin,
    CONTINUOUS_SESSION_INITIALIZATION_SCHEMA,
    InitializationCovariancePolicy,
    InitializationStateOwner,
    LocatedContinuousArticulatedOwners,
    PreparedProspectiveInitialization,
    ProspectiveInitializationOutcome,
    ProspectiveContinuousSessionInitializer,
    ProspectiveInitializationPolicy,
)


class ContinuousSessionInitializer(ProspectiveContinuousSessionInitializer):
    """Require a full-session state owner and never consult an action interval."""

    def __init__(self, **kwargs) -> None:
        state_owner = kwargs.get("state_owner")
        if (type(state_owner) is not InitializationStateOwner
                or state_owner.stationarity is not None):
            raise ValueError("continuous session initializer forbids action stationarity")
        super().__init__(**kwargs)


__all__ = [
    "BootstrapPoseReadiness",
    "BootstrapPoseReadinessStatus",
    "CalibratedImuErrorStateOrigin",
    "CONTINUOUS_SESSION_INITIALIZATION_SCHEMA",
    "ContinuousSessionInitializer",
    "InitializationCovariancePolicy",
    "InitializationStateOwner",
    "LocatedContinuousArticulatedOwners",
    "PreparedProspectiveInitialization",
    "ProspectiveInitializationOutcome",
    "ProspectiveInitializationPolicy",
]
