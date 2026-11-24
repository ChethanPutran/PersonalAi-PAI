
from enum import StrEnum

class Role(StrEnum):
    """Supported roles for plugins."""
    ADMIN = "admin"
    USER = "user"
    GUEST = "guest"
    
# Who is requesting the operation?
class PrincipalType(StrEnum):
    USER = "user"
    AGENT = "agent"
    PLUGIN = "plugin"
    DEVICE = "device"
    SERVICE = "service"
    TASK = "task"

# How did this request come about? Lets policies treat a scheduled job
# differently from a direct tap on the phone.
class Origin(StrEnum):
    USER_DIRECT = "user_direct"
    AGENT_AUTONOMOUS = "agent_autonomous"
    SCHEDULED = "scheduled"
    PLUGIN = "plugin"
    # Spoken request. Voice can be spoofed (another speaker, a TV, a replayed recording),
    # so sensitive actions started by voice need on-screen approval, like autonomous ones.
    VOICE = "voice"

class PolicySubject(StrEnum):
    USER = "user"
    ROLE = "role"
    AGENT = "agent"
    PLUGIN = "plugin"
    ANY = "any"

class ConsentScope(StrEnum):
    """How long an approval lasts. ONCE is never stored; SESSION and ALWAYS are grants."""
    ONCE = "once"          # just this run
    SESSION = "session"    # until the session (chat/voice/tester) ends or the TTL passes
    ALWAYS = "always"      # persistent, revocable, expiring

    @property
    def rank(self) -> int:
        return {"once": 0, "session": 1, "always": 2}[self.value]


class RiskLevel(StrEnum):
    """Risk classification for plugin capabilities."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    
# Which resources does the plugin need access to?
class PermissionResource(StrEnum):
    CAMERA = "camera"
    MICROPHONE = "microphone"
    SPEAKER = "speaker"

    FILESYSTEM = "filesystem"
    NETWORK = "network"
    SHELL = "shell"

    NOTIFICATIONS = "notifications"
    LOCATION = "location"
    CONTACTS = "contacts"
    CALENDAR = "calendar"

    SCREEN = "screen"
    CLIPBOARD = "clipboard"
    # Synthetic keyboard / mouse input: can do anything the user can do on screen.
    INPUT = "input"

    DEVICE = "device"
    PROCESS = "process"
    SYSTEM = "system"
    STORAGE = "storage"

    MEMORY = "memory"
    PERSONAL_DATA = "personal_data"

# What actions does this capability need to perform on those resources?
class PermissionAction(StrEnum):
    DISCOVER = "discover"
    READ = "read"
    WRITE = "write"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"
    CONTROL = "control"
    SHARE = "share"
    MANAGE = "manage"
    REQUEST = "request"
    SEND = "send"

# Scope & Context
# Which paths, files, devices, recipients, or data are accessible, and under what conditions?
# Resource	Useful scope attributes
# FILESYSTEM	roots, path, recursive, file extensions
# CAMERA	device_id, capture_mode, duration
# MICROPHONE	device_id, recording duration
# NETWORK	domains, hosts, ports, methods
# LOCATION	precision, frequency, duration
# CALENDAR	calendar IDs, date ranges, read/write
# CONTACTS	contact IDs, groups
# CLIPBOARD	read/write, foreground app
# SCREEN	display IDs, capture vs stream
# PROCESS	executable, PID, allowed operations
# SHELL	executable allowlist, command policy

# Policy Decision

# Permit · Deny · Require Approval
# Evaluated against trusted grants, policies, risk, and execution context