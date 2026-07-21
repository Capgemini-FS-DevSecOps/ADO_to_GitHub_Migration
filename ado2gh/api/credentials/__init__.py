"""Cloud credential detection, probing, and management.

Consolidated from scattered modules in ado2gh/api/ per FR-028.
"""
from ado2gh.api.credentials.cloud_credentials_store import (
    CloudCredentialsError as CloudCredentialsError,
    CloudCredentialsStore as CloudCredentialsStore,
    CloudCredentialSource as CloudCredentialSource,
    ScanInFlightError as ScanInFlightError,
    get_ambient_credentials as get_ambient_credentials,
)
from ado2gh.api.credentials.cloud_credential_detector import scan_all_presence as scan_all_presence
from ado2gh.api.credentials.cloud_credential_probe import probe_provider as probe_provider
from ado2gh.api.credentials.credential_validation import (
    validate_github_token as validate_github_token,
    validate_ado_pat as validate_ado_pat,
)

__all__ = [
    "CloudCredentialsError",
    "CloudCredentialsStore",
    "CloudCredentialSource",
    "ScanInFlightError",
    "get_ambient_credentials",
    "scan_all_presence",
    "probe_provider",
    "validate_github_token",
    "validate_ado_pat",
]
