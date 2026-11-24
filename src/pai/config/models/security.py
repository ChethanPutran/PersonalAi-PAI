from enum import Enum, StrEnum
from typing import Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from pai.common.enums.permission import PermissionAction, PermissionResource, Role

class AuthenticationConfig(BaseModel):
    """Authentication configuration."""
    enabled: bool = Field(default=True)
    access_token_expire_minutes: int = Field(default=60)
    refresh_token_expiration_days: int = Field(default=7)
    algorithm: str = Field(default="HS256")


class AuthorizationPolicy(StrEnum):
    """Authorization policies."""
    DEFAULT_POLICY = "default_policy"
    ADMIN_POLICY = "admin_policy"
    USER_POLICY = "user_policy"


class AuthorizationPolicyConfig(BaseModel):
    """Authorization policy configuration."""
    rules: dict[PermissionResource, list[PermissionAction]] = Field(default_factory=dict)


class DefaultAuthorizationPolicyConfig(BaseModel):
    """Default authorization policy configuration."""
    policy: str = Field(default=AuthorizationPolicy.DEFAULT_POLICY.value)


class RolePolicyConfig(BaseModel):
    """Role-based authorization policy configuration."""
    # `name` is accepted as an alias: the YAML used `name:` and it was silently ignored, which
    # collapsed every role into "user" (last one wins) and denied `execute` to everybody.
    model_config = ConfigDict(populate_by_name=True)
    role: Role = Field(default=Role.USER, validation_alias=AliasChoices("role", "name"))
    permissions: list[PermissionAction] = Field(default=[PermissionAction.READ, PermissionAction.WRITE])

class PolicyRuleConfig(BaseModel):
    """Declarative authorization rule. Populated fields are ANDed; list items are
    glob-matched and ORed; empty = wildcard. See security/policies/rules.py."""
    id: str
    effect: Literal["allow", "deny", "approval_required"]
    permissions: list[str] = Field(default_factory=list)   # e.g. "camera.*"
    capabilities: list[str] = Field(default_factory=list)
    plugins: list[str] = Field(default_factory=list)
    origins: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    min_risk: Optional[Literal["low", "medium", "high", "critical"]] = None
    priority: int = 50
    reason: str = ""


class AuthorizationConfig(BaseModel):
    """Authorization configuration."""
    enabled: bool = Field(default=True)
    roles: list[RolePolicyConfig] = Field(default=[
        RolePolicyConfig(role=Role.USER, permissions=[PermissionAction.READ, PermissionAction.WRITE]),
        RolePolicyConfig(role=Role.ADMIN, permissions=[PermissionAction.READ, PermissionAction.WRITE, PermissionAction.EXECUTE, PermissionAction.MANAGE]),
        RolePolicyConfig(role=Role.GUEST, permissions=[PermissionAction.READ])])
    
    default_role: Role = Field(default=Role.USER)
    admin_role: Role = Field(default=Role.ADMIN)
    # How far an approval of keyboard/mouse actions may reach ("input" permission).
    #   once     every action asks again (the default: typing and clicking is the most powerful thing
    #            a plugin can do, and nothing it approved can be remembered)
    #   session  "approve for this session" may be offered (up to 8 hours; high-risk actions never get
    #            "always"). Lets an agent work without a prompt per click, after ONE explicit approval.
    # Camera control is not affected: it stays once-only whatever this says.
    input_grant_scope: Literal["once", "session"] = "once"
    auth_policy: AuthorizationPolicy = Field(default=AuthorizationPolicy.DEFAULT_POLICY)
    rules: list[PolicyRuleConfig] = Field(default_factory=list)

class EncryptionConfig(BaseModel):
    """Encryption configuration."""
    algorithm: str = Field(default="AES-256-GCM")
    key_length: int = Field(default=32)  # 32 bytes for AES-256
    iv_length: int = Field(default=12)   # 12 bytes for GCM
    tag_length: int = Field(default=16)  # 16 bytes for GCM

class SecurityConfig(BaseModel):
    """Security configuration."""
    authentication: AuthenticationConfig = AuthenticationConfig()
    authorization: AuthorizationConfig = AuthorizationConfig()
    encryption: EncryptionConfig = EncryptionConfig()