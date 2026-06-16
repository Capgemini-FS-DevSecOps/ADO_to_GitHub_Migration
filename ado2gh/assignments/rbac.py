"""Profile-scoped RBAC: Coordinator, Operator, Approver."""
from __future__ import annotations

from enum import Enum


class ProfileRole(str, Enum):
    """Roles per migration profile (FR-021a)."""

    COORDINATOR = "coordinator"
    OPERATOR = "operator"
    APPROVER = "approver"


class RBAC:
    """Evaluate role permissions for API and agent actions."""

    def __init__(self, roles: set[ProfileRole]):
        self.roles = roles

    def can_coordinate(self) -> bool:
        return ProfileRole.COORDINATOR in self.roles

    def can_operate(self) -> bool:
        return ProfileRole.OPERATOR in self.roles

    def can_approve(self) -> bool:
        return ProfileRole.APPROVER in self.roles

    def may_request_live(self) -> bool:
        return self.can_operate()

    def may_execute_live(self) -> bool:
        return self.can_approve()

    def may_override_gate(self) -> bool:
        return self.can_approve()

    def has_role(self, role: ProfileRole) -> bool:
        return role in self.roles

    @staticmethod
    def from_actor(actor: str) -> RBAC:
        """Map actor label to roles (dev default: approver gets all roles)."""
        if actor == "approver":
            return RBAC.from_list(["approver", "operator", "coordinator"])
        if actor == "coordinator":
            return RBAC.from_list(["coordinator", "operator"])
        return RBAC.from_list([actor] if actor in {r.value for r in ProfileRole} else ["operator"])

    @staticmethod
    def from_list(role_names: list[str]) -> RBAC:
        mapping = {r.value: r for r in ProfileRole}
        return RBAC({mapping[n] for n in role_names if n in mapping})
