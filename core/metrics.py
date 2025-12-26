from datetime import datetime
from typing import Optional


class ServerMetrics:
    def __init__(self):
        self.connections_total = 0
        self.events_received = 0
        self.events_parsed = 0
        self.events_sent_to_1c = 0
        self.events_failed = 0
        self.last_event_time: Optional[datetime] = None

    def reset(self):
        self.connections_total = 0
        self.events_received = 0
        self.events_parsed = 0
        self.events_sent_to_1c = 0
        self.events_failed = 0
        self.last_event_time = None
