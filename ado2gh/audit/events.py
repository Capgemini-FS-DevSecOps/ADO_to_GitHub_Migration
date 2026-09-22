"""Registry of every audit event name written by ``ado2gh`` and ``services``.

Before this module, each audit call site typed its event name as a bare string
literal passed straight to ``write_profile_audit``, so a rename or a typo in
one caller silently created a second, unrelated event name instead of
failing. An ``Enum`` (like ``ProfileStatus`` and ``PlatformRole`` elsewhere in
this codebase) also self-documents in an IDE, whereas a frozen set of module
constants would not carry that discoverability without extra ceremony — so
this module follows the same convention rather than inventing a second one.

Members are grouped by the subsystem that writes them: platform live-execution
approvals, platform authentication, profile lifecycle, settings and
connectivity, job claiming, and the agent session/tool audit trail. Every
member's value is the exact string already written to the audit log before
this registry existed, so behaviour and wire values are unchanged.
"""
from __future__ import annotations

from enum import Enum


class AuditEvent(str, Enum):
    """Every audit event name written by ``ado2gh`` and ``services`` code."""

    # --- Platform live-execution approvals (ado2gh/api/live_approval_store.py,
    # live_approval_scopes.py, platform_rbac.py; services/accelerator_api/routes/migrate_guard.py)
    ACCELERATOR_MIGRATE_LIVE_EXECUTION = "accelerator.migrate.live_execution"
    """Written when a `/v1/migrate/*` feature route runs live without needing an approval."""

    LIVE_EXECUTION_REQUESTED = "platform.live_execution.requested"
    """Written when a new live-execution approval is queued."""

    LIVE_EXECUTION_SCOPE_MISMATCH = "platform.live_execution.scope_mismatch"
    """Written when a live-execution context re-derives to a different scope than approved."""

    LIVE_EXECUTION_APPROVED = "platform.live_execution.approved"
    """Written when an approver grants a queued live-execution request."""

    LIVE_EXECUTION_DENIED = "platform.live_execution.denied"
    """Written when an approver denies a queued live-execution request."""

    LIVE_EXECUTION_RESUME_FAILED = "platform.live_execution.resume_failed"
    """Written when resuming an approved scope raises instead of executing."""

    LIVE_EXECUTION_NOTIFY_FAILED = "platform.live_execution.notify_failed"
    """Written when telling the agent about a decision fails or times out."""

    LIVE_EXECUTION_APPROVAL_REFUSED = "platform.live_execution.approval_refused"
    """Written when a caller is refused permission to approve live execution."""

    # --- Platform authentication (ado2gh/auth/service.py)
    USER_REGISTERED = "user.registered"
    """Written when an operator account is created pending admin approval."""

    USER_BOOTSTRAP = "user.bootstrap"
    """Written when the first admin account is created."""

    USER_LOGIN = "user.login"
    """Written on a successful sign-in."""

    USER_LOGOUT = "user.logout"
    """Written when a session is signed out."""

    USER_CREATED = "user.created"
    """Written when an admin creates another account directly."""

    USER_UPDATED = "user.updated"
    """Written when an account's role, status or display name changes."""

    # --- Profile lifecycle (services/accelerator_api/routes/profile_routes.py)
    PROFILE_VALIDATION_FAILED = "profile.validation_failed"
    """Written when a submitted profile's Azure DevOps or GitHub credential check fails."""

    PROFILE_CREATED = "profile.created"
    """Written when a submitted profile is created already active."""

    PROFILE_SUBMITTED = "profile.submitted"
    """Written when a submitted profile is created pending approval."""

    PROFILE_DELETED = "profile.deleted"
    """Written when a profile is deleted."""

    PROFILE_DEFAULT_CHANGED = "profile.default_changed"
    """Written when the default profile changes."""

    PROFILE_DEACTIVATED = "profile.deactivated"
    """Written when an active profile is deactivated."""

    PROFILE_APPROVED = "profile.approved"
    """Written when a pending profile is approved."""

    PROFILE_DENIED = "profile.denied"
    """Written when a pending profile is denied."""

    PROFILE_APPEALED = "profile.appealed"
    """Written when a denied profile's submitter appeals the decision."""

    # --- Settings, connectivity, and the GitHub proxy (services/accelerator_api/routes/
    # settings_routes.py, proxy_routes.py)
    ACCELERATOR_GITHUB_PROXY_WRITE = "accelerator.github_proxy.write"
    """Written when a write request is proxied straight through to the GitHub API."""

    CONNECTIVITY_UPDATED = "connectivity.updated"
    """Written when Azure DevOps or GitHub connectivity settings are saved."""

    LLM_MODEL_VALIDATED = "llm.model.validated"
    """Written when a language-model connection check completes."""

    LLM_MODEL_CREATED = "llm_model.created"
    """Written when a language-model configuration is added."""

    LLM_MODEL_ENABLED = "llm.model.enabled"
    """Written when a language-model configuration is enabled as the active one."""

    LLM_MODEL_DELETED = "llm_model.deleted"
    """Written when a language-model configuration is removed."""

    CLOUD_CREDENTIALS_SCANNED = "cloud_credentials.scanned"
    """Written when the platform scans the environment for cloud provider credentials."""

    CLOUD_CREDENTIALS_UPDATED = "cloud_credentials.updated"
    """Written when cloud provider credentials are saved."""

    CLOUD_CREDENTIALS_APPROVED = "cloud_credentials.approved"
    """Written when pending cloud provider credentials are approved."""

    CLOUD_CREDENTIALS_REJECTED = "cloud_credentials.rejected"
    """Written when pending cloud provider credentials are rejected."""

    CLOUD_CREDENTIALS_REVOKED = "cloud_credentials.revoked"
    """Written when previously approved cloud provider credentials are revoked."""

    # --- Job claiming (ado2gh/state/job_store.py)
    JOB_CLAIM_CONFLICT = "job.claim_conflict"
    """Written when two workers race to claim the same job and one loses."""

    # --- Agent session and tool audit trail (services/agent/routes/*,
    # ado2gh/agents/migration_agent/tools/shared_tools.py, nodes/executor/scope.py)
    AGENT_FORM_STALE_SUBMISSION_REFUSED = "agent.form.stale_submission_refused"
    """Written when a form submission for a plan revision the session has moved past is refused."""

    SESSION_APPROVE = "session.approve"
    """Written when an operator approves a session's pending action."""

    SESSION_APPROVE_DENIED = "session.approve.denied"
    """Written when an operator denies a session's pending action."""

    SESSION_APPROVE_DENIED_FORWARD_FAILED = "session.approve.denied.forward_failed"
    """Written when forwarding a denial decision to its target fails."""

    SESSION_CONFIRM_LIVE = "session.confirm_live"
    """Written when an operator confirms a session may execute live."""

    SESSION_RESUME_LIVE = "session.resume_live"
    """Written when a session resumes after a live-execution approval."""

    SESSION_START = "session.start"
    """Written when a new agent session is created."""

    SESSION_PROVISION = "session.provision"
    """Written when a session is provisioned with its migration profile."""

    SESSION_REQUEST_LIVE = "session.request_live"
    """Written when a session requests permission to execute live."""

    SESSION_REQUEST_LIVE_FAILED = "session.request_live.failed"
    """Written when a session's request to execute live fails."""

    AGENT_TOOL_PATH_REFUSED = "agent.tool.path_refused"
    """Written when an agent tool call is refused for naming a disallowed path."""

    AGENT_EXECUTOR_GUARDRAIL_BLOCKED = "agent.executor.guardrail_blocked"
    """Written when the executor's guardrails block a tool call."""


__all__ = ["AuditEvent"]
