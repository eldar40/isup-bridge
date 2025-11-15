"""
ISUP v5 Protocol Parser for Hikvision Access Control (Corrected)
"""

import struct
import logging
from datetime import datetime
from typing import Optional
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# -------------------------------------------------------
# ENUMS
# -------------------------------------------------------

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


# -------------------------------------------------------
# STRUCTURES
# -------------------------------------------------------

@dataclass
class ISUPHeader:
    start_marker: bytes
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


# -------------------------------------------------------
# MAIN PARSER
# -------------------------------------------------------

class ISUPv5Parser:
    """Correct ISUP v5 parser according to Hikvision specification"""

    HEADER_SIZE = 2 + 1 + 1 + 2 + 16 + 4 + 2

    def __init__(self, strict_mode: bool = True):
        self.strict_mode = strict_mode
        self.log = logging.getLogger(self.__class__.__name__)

    # ---------------------------------------------------
    # Public API
    # ---------------------------------------------------

    def parse(self, packet: bytes) -> Optional[ISUPAccessEvent]:
        if len(packet) < self.HEADER_SIZE:
            self.log.warning("Short packet")
            return None

        # Header
        header = self._parse_header(packet)
        if not header:
            return None

        # Checksum
        if not self._verify_crc(packet):
            self.log.warning("CRC mismatch")
            if self.strict_mode:
                return None

        # Event body
        body = packet[self.HEADER_SIZE:self.HEADER_SIZE + header.data_length]
        return self._parse_access_event(header, body)

    # ---------------------------------------------------
    # Header parsing
    # ---------------------------------------------------

    def _parse_header(self, data: bytes) -> Optional[ISUPHeader]:
        try:
            if data[:2] != b"##":
                return None

            version = data[2]
            cmd = data[3]
            data_len = struct.unpack(">H", data[4:6])[0]

            # 16-byte Device ID (ASCII)
            device_id_raw = data[6:22]
            device_id = device_id_raw.decode("ascii", errors="ignore").strip("\x00")

            # 4-byte sequence
            seq = struct.unpack(">I", data[22:26])[0]

            checksum = struct.unpack(">H", data[26:28])[0]

            return ISUPHeader(
                start_marker=b"##",
                version=version,
                command_type=cmd,
                data_length=data_len,
                device_id=device_id,
                sequence_number=seq,
                checksum=checksum
            )

        except Exception as e:
            self.log.error(f"Header parse error: {e}")
            return None

    # ---------------------------------------------------
    # Access event parsing
    # ---------------------------------------------------

    def _parse_access_event(self, header: ISUPHeader, d: bytes) -> Optional[ISUPAccessEvent]:
        try:
            if len(d) < 26:
                return None

            # According to Hikvision ISUP v5
            verify_mode = d[2]
            direction = d[3]

            user_id = str(struct.unpack(">I", d[4:8])[0])

            # Card number (8 bytes binary)
            card_hex = d[8:16].hex().upper()

            # Timestamp YYMMDDHHMMSS
            ts_bytes = d[16:22]
            timestamp = self._parse_timestamp(ts_bytes)

            door = d[22]
            reader = d[23]
            verify_result = d[24]

            return ISUPAccessEvent(
                header=header,
                card_number=card_hex,
                access_type=self._map_access_type(verify_mode),
                direction=self._map_direction(direction),
                timestamp=timestamp,
                door_number=door,
                reader_number=reader,
                user_id=user_id,
                verify_result=verify_result
            )

        except Exception as e:
            self.log.error(f"Event parse error: {e}")
            return None

    # ---------------------------------------------------
    # Helpers
    # ---------------------------------------------------

    def _parse_timestamp(self, b: bytes) -> datetime:
        try:
            yy, mm, dd, hh, mi, ss = b
            return datetime(2000 + yy, mm, dd, hh, mi, ss)
        except:
            return datetime.now()

    def _map_access_type(self, v: int) -> ISUPAccessType:
        return {
            1: ISUPAccessType.CARD,
            2: ISUPAccessType.FINGERPRINT,
            3: ISUPAccessType.FACE,
            4: ISUPAccessType.PIN_CODE,
            5: ISUPAccessType.QR_CODE,
            6: ISUPAccessType.COMBINED,
        }.get(v, ISUPAccessType.UNKNOWN)

    def _map_direction(self, v: int) -> ISUPDirection:
        return {
            1: ISUPDirection.IN,
            2: ISUPDirection.OUT
        }.get(v, ISUPDirection.UNKNOWN)

    # ---------------------------------------------------
    # CRC16 (Hikvision)
    # ---------------------------------------------------

    def _verify_crc(self, data: bytes) -> bool:
        """CRC16/IBM (poly=0xA001, init=0xFFFF)"""

        # The CRC covers all bytes except the last 2 (CRC itself)
        body = data[:-2]
        crc_actual = struct.unpack(">H", data[-2:])[0]
        crc_calc = self._crc16(body)

        return crc_calc == crc_actual

    def _crc16(self, data: bytes) -> int:
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc & 0xFFFF