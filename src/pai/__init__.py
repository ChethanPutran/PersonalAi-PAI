"""Personal AI System - Context-Aware Autonomous Personal Agent System."""

__version__ = "1.0.0"
__author__ = "PAI Team"

from pai.kernel.ai_kernel import AIKernel
from pai.agents.base_agent import BaseAgent
from pai.plugins.base_plugin import BasePlugin
from pai.executors.base_executor import BaseExecutor

__all__ = [
    "AIKernel",
    "BaseAgent", 
    "BasePlugin",
    "BaseExecutor"
]