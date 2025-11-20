"""
hikvision.multipart_parser

Pure-Python multipart parser for Hikvision ISAPI multipart streams.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List

logger = logging.getLogger(__name__)


@dataclass
class Part:
    """Represents a single multipart part.

    Attributes:
        headers: dict of header-name -> value (lowercased names)
        body: raw bytes of the part body
        type: 'xml' | 'json' | 'image' | 'unknown'
    """

    headers: Dict[str, str]
    body: bytes
    type: str

    def to_dict(self) -> Dict[str, object]:
        """Return a plain dictionary representation."""

        return {"type": self.type, "headers": self.headers, "body": self.body}


class MultipartParser:
    """A minimal multipart parser tuned for Hikvision ISAPI payloads.

    It accepts a raw byte stream and a boundary string (without leading `--`) and
    returns a list of :class:`Part` objects. The parser is intentionally lightweight and
    focuses on common ISAPI patterns (XML event blocks and JPEG images).
    """

    @staticmethod
    def _parse_headers(raw: bytes) -> Dict[str, str]:
        """Parse HTTP-style headers block into a dict with lowercased keys."""

        headers: Dict[str, str] = {}
        try:
            lines = raw.split(b"\r\n")
            for line in lines:
                if not line or b":" not in line:
                    continue
                k, v = line.split(b":", 1)
                key = k.decode("utf-8", errors="ignore").strip().lower()
                val = v.decode("utf-8", errors="ignore").strip()
                headers[key] = val
        except Exception:
            logger.exception("Failed to parse part headers")
        return headers

    @staticmethod
    def _detect_type(content_type: str, body: bytes) -> str:
        """Detect part type from Content-Type header or body heuristics."""

        if content_type:
            ct = content_type.lower()
            if "xml" in ct:
                return "xml"
            if "json" in ct:
                return "json"
            if "jpeg" in ct or "jpg" in ct or ct.startswith("image/"):
                return "image"

        trimmed = body.lstrip()
        if not trimmed:
            return "unknown"
        if trimmed.startswith(b"<"):
            return "xml"
        try:
            decoded = trimmed.decode("utf-8")
            prefix = decoded.lstrip()[:1]
            if prefix in ("{", "["):
                return "json"
            return "xml"
        except Exception:
            return "unknown"

    @staticmethod
    def parse(stream: bytes, boundary: str) -> List[Part]:
        """Parse a raw multipart stream by given boundary.

        Args:
            stream: raw bytes received from HTTP multipart stream
            boundary: the boundary string (without initial `--`)

        Returns:
            list of :class:`Part` objects
        """

        parts: List[Part] = []
        if not boundary:
            logger.debug("No boundary provided to MultipartParser.parse")
            return parts

        try:
            b_boundary = ("--" + boundary).encode("utf-8")
            segments = stream.split(b_boundary)
            for seg in segments:
                if not seg or seg in (b"--", b""):
                    continue
                seg = seg.strip(b"\r\n")
                if not seg or seg == b"--":
                    continue

                if seg.endswith(b"--"):
                    seg = seg[:-2]

                header_body = seg.split(b"\r\n\r\n", 1)
                if len(header_body) == 2:
                    raw_headers, body = header_body
                    headers = MultipartParser._parse_headers(raw_headers)
                else:
                    headers = {}
                    body = seg

                content_type = headers.get("content-type", "")
                ptype = MultipartParser._detect_type(content_type, body)
                parts.append(Part(headers=headers, body=body, type=ptype))
        except Exception:
            logger.exception("Failed to parse multipart stream")

        logger.debug("MultipartParser.parse produced %d parts (boundary=%s)", len(parts), boundary)
        return parts