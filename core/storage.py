import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List


class EventStorage:
    def __init__(self, storage_path: Path, max_pending_days: int, logger: logging.Logger):
        self.storage_path = storage_path
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.max_pending_days = max_pending_days

    async def save_event(self, event: Dict, tenant_id: str):
        if not self.storage_path:
            return

        filename = self.storage_path / f"pending_{tenant_id}_{int(datetime.now().timestamp())}.json"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(event, f, ensure_ascii=False, indent=2)
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error(f"Failed to save pending event: {e}")

    async def get_pending_events(self) -> List[str]:
        if not self.storage_path.exists():
            return []

        cutoff = datetime.now() - timedelta(days=self.max_pending_days)
        pending = []
        for file_path in self.storage_path.glob("pending_*.json"):
            try:
                ts = int(file_path.stem.split("_")[-1])
                if datetime.fromtimestamp(ts) >= cutoff:
                    pending.append(str(file_path))
                else:
                    file_path.unlink(missing_ok=True)
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning(f"Skipping pending file {file_path}: {exc}")
        return pending

    async def delete_event(self, filepath: str):
        try:
            Path(filepath).unlink(missing_ok=True)
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error(f"Failed to delete pending event file {filepath}: {e}")

    async def close(self):
        pass
