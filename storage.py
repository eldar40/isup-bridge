# -*- coding: utf-8 -*-
"""
Local Storage for Pending Events
Хранит события, которые не удалось отправить в 1С.
"""

import json
import uuid
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any


class EventStorage:

    def __init__(self, directory: str, max_days: int = 30):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

        self.log = logging.getLogger(self.__class__.__name__)
        self.lock = asyncio.Lock()
        self.max_days = max_days

    # =====================================================================
    # SAVE EVENT
    # =====================================================================

    async def save_pending(self, event: Dict[str, Any]) -> bool:
        """
        Сохраняет событие в локальное хранилище.
        """

        event_id = str(uuid.uuid4())
        event["_pending_id"] = event_id
        event["saved_at"] = datetime.now().isoformat()

        filename = self.dir / f"{event_id}.json"

        try:
            async with self.lock:
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(event, f, ensure_ascii=False, indent=2)

            self.log.info(f"💾 Saved pending event: {filename.name}")
            return True

        except Exception as e:
            self.log.error(f"❌ Error saving pending event: {e}")
            return False

    # =====================================================================
    # LOAD ALL EVENTS
    # =====================================================================

    async def load_all(self) -> List[Dict[str, Any]]:
        """
        Загружает все pending-события.
        """

        events = []
        files = sorted(self.dir.glob("*.json"))

        for file in files:
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    data["_file_path"] = str(file)
                    events.append(data)

            except Exception as e:
                self.log.error(f"❌ Error loading {file}: {e}")

        return events

    # =====================================================================
    # REMOVE EVENT
    # =====================================================================

    async def remove(self, event: Dict[str, Any]) -> bool:
        """
        Удаляет событие после успешной отправки.
        """
        path = event.get("_file_path")
        if not path:
            return False

        try:
            file_path = Path(path)
            file_path.unlink()
            self.log.info(f"🗑 Removed pending event: {file_path.name}")
            return True
        except Exception as e:
            self.log.error(f"❌ Error deleting {path}: {e}")
            return False

    # =====================================================================
    # CLEAN OLD FILES
    # =====================================================================

    async def cleanup_old(self):
        """
        Удаляет файлы старше max_days.
        """
        cutoff = datetime.now() - timedelta(days=self.max_days)

        for file in self.dir.glob("*.json"):
            try:
                ts = datetime.fromtimestamp(file.stat().st_mtime)
                if ts < cutoff:
                    file.unlink()
                    self.log.info(f"🧹 Removed old pending file: {file.name}")
            except Exception as e:
                self.log.error(f"Cleanup error for {file}: {e}")