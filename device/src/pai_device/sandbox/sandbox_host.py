# sandbox_host.py
import asyncio, json, sys
from typing import Any, Dict

class SubprocessSandbox:
    def __init__(self, plugin_dir: str, limits: Dict[str, Any]):
        self.plugin_dir = plugin_dir
        self.limits = limits
        self.proc: asyncio.subprocess.Process | None = None

    async def start(self):
        # resource limits via preexec_fn on POSIX
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "sandbox_child", self.plugin_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
            preexec_fn=self._apply_rlimits,
            cwd="/tmp/pai-sandbox",         # empty writable cwd
            env={"PATH": "/usr/bin:/bin"},   # minimal env
        )

    def _apply_rlimits(self):
        import resource
        # 512 MB address space, 60 s CPU, no core dumps, 64 processes
        resource.setrlimit(resource.RLIMIT_AS,    (512<<20, 512<<20))
        resource.setrlimit(resource.RLIMIT_CPU,   (60, 60))
        resource.setrlimit(resource.RLIMIT_CORE,  (0, 0))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))

    async def invoke(self, capability: str, params: dict, timeout: float = 30.0) -> Any:
        assert self.proc and self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(
            (json.dumps({"capability": capability, "parameters": params}) + "\n").encode()
        )
        await self.proc.stdin.drain()
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout)
        return json.loads(line)

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()