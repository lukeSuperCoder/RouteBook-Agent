from enum import StrEnum


class RouteBookStatus(StrEnum):
    DRAFT = "draft"
    PLANNING = "planning"
    PENDING_CONFIRMATION = "pending_confirmation"
    EDITABLE = "editable"
    BLOCKED = "blocked"
    FINAL = "final"


class WorkflowStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowRunType(StrEnum):
    CREATE = "create"
    EDIT = "edit"
    REFRESH = "refresh"
    FINALIZE = "finalize"


class WorkflowStage(StrEnum):
    QUEUED = "queued"
    EXTRACTING_REQUIREMENTS = "extracting_requirements"
    WAITING_FOR_CLARIFICATION = "waiting_for_clarification"
    RESOLVING_PLACES = "resolving_places"
    WAITING_FOR_PLACE_CONFIRMATION = "waiting_for_place_confirmation"
    PLANNING_DAYS = "planning_days"
    FETCHING_ROUTES = "fetching_routes"
    FETCHING_WEATHER = "fetching_weather"
    VALIDATING = "validating"
    WAITING_FOR_CHANGE_CONFIRMATION = "waiting_for_change_confirmation"
    SAVING_VERSION = "saving_version"
    RENDERING_FINAL_PAGE = "rendering_final_page"
    COMPLETED = "completed"
    FAILED = "failed"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ChangeType(StrEnum):
    CREATE = "create"
    EDIT = "edit"
    UNDO = "undo"
    FINALIZE = "finalize"


class RequirementSource(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    DEFAULT = "default"
    MISSING = "missing"


class FactStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    CONFLICTED = "conflicted"
    PROPOSED = "proposed"
