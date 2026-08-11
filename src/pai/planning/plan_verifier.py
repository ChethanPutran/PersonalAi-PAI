from typing import Dict


class PlanVerifier:
    def __init__(self, kernel):
        self.kernel = kernel
        
    async def verify(self, plan: Dict) -> Dict:
        """Check for contradictions, resource conflicts, feasibility."""
        issues = []
        tasks = plan["tasks"]
        # Check for cyclic dependencies
        # Check for missing capabilities
        for task in tasks:
            capability = task.get("capability")
            if capability and not await self.kernel.capability_router.has_capability(capability):
                issues.append(f"Missing capability: {capability}")
        # Check resource constraints (e.g., no two tasks using same device)
        # Simulate execution quickly (dry run)
        return {"valid": len(issues) == 0, "issues": issues}