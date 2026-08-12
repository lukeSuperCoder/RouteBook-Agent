from dataclasses import dataclass


@dataclass(frozen=True)
class RequestPrincipal:
    subject: str
    authenticated: bool


def get_request_principal() -> RequestPrincipal:
    """Phase 1 authorization seam; replaced by authenticated identity later."""
    return RequestPrincipal(subject="anonymous", authenticated=False)
