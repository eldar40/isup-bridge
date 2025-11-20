# -*- coding: utf-8 -*-
"""
Server metrics — лёгкий модуль для сбора и отдачи метрик в JSON.
Поддерживает: uptime, счетчики событий, last event timestamp, success rate.
"""

from datetime import datetime
from typing import Optional, Dict


class ServerMetrics:
    def __init__(self):
        self.start_time = datetime.utcnow()
        # basic counters
        self.connections_total = 0
        self.events_received = 0
        self.events_parsed = 0
        self.events_ok = 0
        self.events_failed = 0
        self.events_retried_ok = 0
        self.events_retried_fail = 0
        self.events_pending = 0

        # ISAPI specific
        self.isapi_events_received = 0
        self.isapi_events_processed = 0

        self.last_event_time: Optional[datetime] = None

    # -------------------------
    # computed properties
    # -------------------------
    @property
    def uptime_seconds(self) -> float:
        return (datetime.utcnow() - self.start_time).total_seconds()

    @property
    def success_rate(self) -> float:
        total = self.events_received
        if total == 0:
            return 0.0
        return (self.events_ok / total) * 100.0

    # -------------------------
    # export to dict/json
    # -------------------------
    def to_dict(self) -> Dict:
        return {
            "start_time": self.start_time.isoformat(),
            "uptime_seconds": int(self.uptime_seconds),
            "connections_total": self.connections_total,
            "events": {
                "received": self.events_received,
                "parsed": self.events_parsed,
                "ok": self.events_ok,
                "failed": self.events_failed,
                "pending": self.events_pending,
                "retries_ok": self.events_retried_ok,
                "retries_failed": self.events_retried_fail,
                "success_rate_percent": round(self.success_rate, 2)
            },
            "isapi": {
                "received": self.isapi_events_received,
                "processed": self.isapi_events_processed
            },
            "last_event_time": self.last_event_time.isoformat() if self.last_event_time else None
        }