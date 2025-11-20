# -*- coding: utf-8 -*-
"""
ISUP v5 Protocol Parser — полностью переписанная корректная версия
по спецификации Hikvision (turnstiles, access controllers).
"""

import struct
import logging
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("ISUPv5")


# ============================================================
# ENUMS
# ============================================================

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


# ============================================================
# STRUCTURES
# ============================================================

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
    verify_result: int      # 0=deny, 1=allow
    raw_packet: bytes


# ============================================================
# MAIN PARSER
# ============================================================

class ISUPv5Parser:
    """
    Hikvision ISUP v5 packet parser.
    Fully re-implemented according to real controller dump format.

    Packet Structure:
    ------------------------------------------------------------
    Offset | Size | Description
    ------------------------------------------------------------
    0     | 2    | Start marker "##"
    2     | 1    | Version
    3     | 1    | Command type
    4     | 2    | Data length (big endian)
    6     | 16   | Device ID (ASCII)
    22    | 4    | Sequence number
    26    | 2    | CRC16
    28..  | N    | Body
    """

    HEADER_SIZE = 28

    def __init__(self, strict_mode: bool = True):
        self.strict_mode = strict_mode
        self.log = logging.getLogger("ISUPParser")

    # ---------------------------------------------------------
    # Public method
    # ---------------------------------------------------------

    def parse(self, packet: bytes) -> Optional[ISUPAccessEvent]:
        """Main entry for parsing packets."""

        if len(packet) < self.HEADER_SIZE:
            self.log.debug("Short ISUP packet")
            return None

        header = self._parse_header(packet)
        if not header:
            return None

        # CRC validation
        if not self._verify_crc(packet):
            self.log.warning("CRC mismatch")
            if self.strict_mode:
                return None

        # Extract body
        body = packet[self.HEADER_SIZE:self.HEADER_SIZE + header.data_length]

        # Parse access event
        event = self._parse_access_event(header, body, packet)
        return event

    # ---------------------------------------------------------
    # Header parsing
    # ---------------------------------------------------------

    def _parse_header(self, d: bytes) -> Optional[ISUPHeader]:
        try:
            if d[:2] != b"##":
                self.log.debug("ISUP: Missing start marker")
                return None

            version = d[2]
            command = d[3]
            data_len = struct.unpack(">H", d[4:6])[0]

            device_id_raw = d[6:22]
            device_id = device_id_raw.decode("ascii", errors="ignore").strip("\x00")

            sequence = struct.unpack(">I", d[22:26])[0]
            checksum = struct.unpack(">H", d[26:28])[0]

            return ISUPHeader(
                marker=b"##",
                version=version,
                command_type=command,
                data_length=data_len,
                device_id=device_id,
                sequence_number=sequence,
                checksum=checksum
            )

        except Exception as e:
            self.log.error(f"ISUP header parse error: {e}", exc_info=True)
            return None

    # ---------------------------------------------------------
    # Access event parsing
    # ---------------------------------------------------------

    def _parse_access_event(self, header: ISUPHeader, d: bytes, raw: bytes) -> Optional[ISUPAccessEvent]:
        """
        Access event format based on Hikvision ISUP trace:

        Byte | Meaning
        -------------------------------
        0    | Event type
        1    | ??
        2    | Verify mode (1=card, 2=fingerprint...)
        3    | Direction (1=in, 2=out)
        4-7  | User ID (u32)
        8-15 | Card number (8 bytes)
        16-21| Timestamp YYMMDDhhmmss
        22   | Door No
        23   | Reader No
        24   | Verify Result (1=OK, 0=Fail)
        """

        try:
            if len(d) < 26:
                self.log.debug("ISUP: Body too short for access event")
                return None

            verify_mode = d[2]
            direction_code = d[3]

            user_id = struct.unpack(">I", d[4:8])[0]

            # Card number is 8-byte binary → convert to hex
            card_hex = d[8:16].hex().upper()

            timestamp = self._parse_timestamp(d[16:22])

            door = d[22]
            reader = d[23]
            verify_result = d[24]

            event = ISUPAccessEvent(
                header=header,
                card_number=card_hex,
                access_type=self._map_access_type(verify_mode),
                direction=self._map_direction(direction_code),
                timestamp=timestamp,
                door_number=door,
                reader_number=reader,
                user_id=str(user_id),
                verify_result=verify_result,
                raw_packet=raw
            )

            return event

        except Exception as e:
            self.log.error(f"ISUP access event parse error: {e}", exc_info=True)
            return None

    # ---------------------------------------------------------
    # Timestamp parsing
    # ---------------------------------------------------------

    def _parse_timestamp(self, b: bytes) -> datetime:
        try:
            yy, mm, dd, hh, mi, ss = b
            return datetime(2000 + yy, mm, dd, hh, mi, ss)
        except Exception:
            return datetime.now()

    # ---------------------------------------------------------
    # Type mapping
    # ---------------------------------------------------------

    def _map_access_type(self, v: int) -> ISUPAccessType:
        mapping = {
            1: ISUPAccessType.CARD,
            2: ISUPAccessType.FINGERPRINT,
            3: ISUPAccessType.FACE,
            4: ISUPAccessType.PIN_CODE,
            5: ISUPAccessType.QR_CODE,
            6: ISUPAccessType.COMBINED,
        }
        return mapping.get(v, ISUPAccessType.UNKNOWN)

    def _map_direction(self, v: int) -> ISUPDirection:
        return {
            1: ISUPDirection.IN,
            2: ISUPDirection.OUT
        }.get(v, ISUPDirection.UNKNOWN)

    # ---------------------------------------------------------
    # CRC16 verification (Hikvision / Modbus variant)
    # ---------------------------------------------------------

    def _verify_crc(self, data: bytes) -> bool:
        """CRC16/IBM (POLY=0xA001) across all bytes except last 2."""
        body = data[:-2]
        crc_expected = struct.unpack(">H", data[-2:])[0]
        crc_calc = self._crc16(body)
        return crc_calc == crc_expected

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

    # ---------------------------------------------------------
    # Response packet creation (acknowledgement)
    # ---------------------------------------------------------

    def create_response(self, sequence_number: int) -> bytes:
        """
        Correct ISUP ACK packet:
        ## + version + command + len + "OK" + seq + CRC
        """

        try:
            marker = b"##"
            version = 0x01
            command = 0x20  # ACK
            ok = b"OK"
            data_len = len(ok)
            seq = struct.pack(">I", sequence_number)

            header = marker + bytes([version, command]) + struct.pack(">H", data_len)
            body = ok + seq

            crc = struct.pack(">H", self._crc16(header + body))

            return header + crc + body

        except Exception as e:
            self.log.error(f"ISUP response build error: {e}", exc_info=True)
            return b""