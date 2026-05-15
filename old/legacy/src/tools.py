from typing import Dict, Any
import importlib.util
import os
from pathlib import Path
# Your tools
from .tools2 import (
    weather_tool,
    search_tool,
    capdesc_tool,
    time_tool,
    human_assistance
)
SKILLS_DIR = Path(__file__).parent / "skills"

class SkillManager:
    def __init__(self, skills_dir=SKILLS_DIR):
        self.skills_dir = skills_dir
        self.skills = {}
        self.tools = []
        self._init_tools()
        self.load_skills()
    
    # ==================== TOOLS ====================
    def _init_tools(self):
        self.tools = [
            weather_tool,
            time_tool,
            search_tool,
            capdesc_tool,
            human_assistance
        ]

    def load_skills(self):
        """Dynamically load all skills from skills directory"""
        for filename in os.listdir(self.skills_dir):
            if filename.endswith('.py') and filename != '__init__.py':
                skill_name = filename[:-3]
                spec = importlib.util.spec_from_file_location(
                    skill_name, 
                    os.path.join(self.skills_dir, filename)
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                if hasattr(module, 'execute'):
                    self.skills[skill_name] = module.execute
    
    def execute_skill(self, skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a skill with parameters"""
        if skill_name not in self.skills:
            return {"error": f"Skill {skill_name} not found"}
        
        try:
            result = self.skills[skill_name](**params)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def list_skills(self) -> list:
        return list(self.skills.keys())
    
    # ==================== TOOLS MGMT ====================
    def add_tool(self, tool):
        self.tools.append(tool)

    def remove_tool(self, name: str):
        self.tools = [t for t in self.tools if t.name != name]

    def list_tools(self):
        return self.tools

    
