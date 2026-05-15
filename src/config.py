from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    root_dir: Path
    data_dir: Path
    workspace_dir: Path


def load_config() -> AppConfig:
    root_dir = Path(__file__).resolve().parent.parent
    data_dir = root_dir / "data"
    workspace_dir = root_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(root_dir=root_dir, data_dir=data_dir, workspace_dir=workspace_dir)

