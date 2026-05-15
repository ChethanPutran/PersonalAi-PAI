from enum import Enum
class NodeNames(Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    RESPONDER = "responder"
    CLEANUP = "cleanup"

class EventNames(Enum):
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"

class NextStates(Enum):
    EXECUTE = "execute"
    RESPOND = "respond"
    CONTINUE = "continue"
    NEED_APPROVAL = "need_approval"
    DONE = "done"
    END = "end"

class Commands(Enum):
    APPROVE = "approve"
    REJECT = "reject"
