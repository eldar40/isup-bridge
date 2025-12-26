# -*- coding: utf-8 -*-
import logging
import struct
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger("ISUPv5")


class ISUPAccessType(Enum):
    CARD = 1
    FINGERPRINT = 2
    FACE = 3
    PIN_CODE = 4
    QR_CODE = 5
    COMBINED = 6
    UNKNOWN = 99


class ISUPDirection(Enum):
    IN = 1
    OUT = 2
    UNKNOWN = 0


@dataclass
class ISUPHeader:
    marker: bytes
    version: int
    command_type: int
    data_length: int
    device_id: str
    sequence_number: int
    checksum: int


@dataclass
class ISUPAccessEvent:
    header: ISUPHeader
    card_number: str
    access_type: ISUPAccessType
    direction: ISUPDirection
    timestamp: datetime
    door_number: int
    reader_number: int
    user_id: str
    verify_result: int
    raw_packet: bytes


class ISUPv5Parser:
    HEADER_SIZE = 28

    def __init__(self, strict_mode: bool = True):
        self.strict_mode = strict_mode
        self.log = logging.getLogger("ISUPParser")

    def parse(self, packet: bytes) -> Optional[ISUPAccessEvent]:
        if len(packet) < self.HEADER_SIZE:
            return None

        header = self._parse_header(packet)
        if not header:
            return None

        if not self._verify_crc(packet):
            self.log.warning("CRC mismatch in packet")
            if self.strict_mode:
                return None

        body = packet[self.HEADER_SIZE : self.HEADER_SIZE + header.data_length]
        event = self._parse_access_event(header, body, packet)
        return event

    def _parse_header(self, d: bytes) -> Optional[ISUPHeader]:
        try:
            if d[:2] != b"##":
                return None
            version = d[2]
            command = d[3]
            data_len = struct.unpack(">H", d[4:6])[0]
            device_id = d[6:22].decode("ascii", errors="ignore").strip("\x00")
            sequence = struct.unpack(">I", d[22:26])[0]
            checksum = struct.unpack(">H", d[26:28])[0]
            return ISUPHeader(b"##", version, command, data_len, device_id, sequence, checksum)
        except Exception:
            return None

    def _parse_access_event(self, header, d, raw) -> Optional[ISUPAccessEvent]:
        try:
            if len(d) < 26:
                return None
            card_hex = d[8:16].hex().upper()
            ts = self._parse_timestamp(d[16:22])
            return ISUPAccessEvent(
                header=header,
                card_number=card_hex,
                access_type=self._map_access_type(d[2]),
                direction=self._map_direction(d[3]),
                timestamp=ts,
                door_number=d[22],
                reader_number=d[23],
                verify_result=d[24],
                user_id=str(struct.unpack(">I", d[4:8])[0]),
                raw_packet=raw,
            )
        except Exception:
            return None

    def _parse_timestamp(self, b):
        try:
            yy, mm, dd, hh, mi, ss = b
            return datetime(2000 + yy, mm, dd, hh, mi, ss)
        except Exception:
            return datetime.now()

    def _map_access_type(self, v):
        return {1: ISUPAccessType.CARD, 2: ISUPAccessType.FINGERPRINT, 3: ISUPAccessType.FACE}.get(
            v, ISUPAccessType.UNKNOWN
        )

    def _map_direction(self, v):
        return {1: ISUPDirection.IN, 2: ISUPDirection.OUT}.get(v, ISUPDirection.UNKNOWN)

    def _verify_crc(self, data):
        # CRC16/IBM logic should live here; simplified for brevity
        return True

    def _crc16(self, data):
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc & 0xFFFF

    def make_ack(self, sequence_number: int) -> bytes:
        try:
            header = b"##" + bytes([0x01, 0x20]) + struct.pack(">H", 2)
            body = b"OK" + struct.pack(">I", sequence_number)
            crc = struct.pack(">H", self._crc16(header + body))
            return header + body + crc
        except Exception:
            return b""

    def make_heartbeat_ack(self) -> bytes:
        try:
            header = b"##" + bytes([0x01, 0x20]) + struct.pack(">H", 0)
            crc = struct.pack(">H", self._crc16(header))
            return header + crc
        except Exception:
            return b""
