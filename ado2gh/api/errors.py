"""Domain exceptions for the Accelerator SDK."""
from __future__ import annotations


class AcceleratorError(Exception):
    """Base error for ado2gh.api operations."""


class ConfigurationError(AcceleratorError):
    """Missing or invalid configuration."""


class OrchestrationError(AcceleratorError):
    """Wave/phase execution failure."""


class ValidationError(AcceleratorError):
    """Post-migration validation failure."""
