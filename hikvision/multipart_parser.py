"""
hikvision.multipart_parser

Lightweight multipart parser for Hikvision ISAPI multipart streams (XML + images).
"""

from typing import Dict, List, NamedTuple
import logging

logger = logging.getLogger(__name__)


class Part(NamedTuple):
    """Represents a single multipart part.

    Attributes:
        headers: dict of header-name -> value (lowercased names)
        body: raw bytes of the part body
        type: 'xml' | 'json' | 'image' | 'unknown'
    """

    headers: Dict[str, str]
    body: bytes
    type: str


class MultipartParser:
    """A minimal multipart parser tuned for Hikvision ISAPI payloads.

    It accepts a raw byte stream and a boundary string (without leading `--`) and
    returns a list of Part objects. The parser is intentionally lightweight and
    focuses on common ISAPI patterns (XML event blocks and JPEG images).
    """

    @staticmethod
    def _parse_headers(raw: bytes) -> Dict[str, str]:
        """Parse HTTP-style headers block into a dict with lowercased keys."""
        headers: Dict[str, str] = {}
        try:
            lines = raw.split(b"\r\n")
            for line in lines:
                if not line:
                    continue
                if b":" not in line:
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
        # Fallback heuristics
        trimmed = body.lstrip()
        if not trimmed:
            return "unknown"
        if trimmed.startswith(b"<"):
            return "xml"
        try:
            trimmed.decode("utf-8")
            # If decodes and starts with { or [ consider json
            s = trimmed[:32].decode("utf-8", errors="ignore").lstrip()
            if s.startswith("{") or s.startswith("["):
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
            list of Part objects
        """
        parts: List[Part] = []
        if not boundary:
            logger.debug("No boundary provided to MultipartParser.parse")
            return parts

        try:
            b_boundary = ("--" + boundary).encode("utf-8")
            # Split by boundary occurrences. Keep the raw pieces.
            segments = stream.split(b_boundary)
            for seg in segments:
                if not seg or seg == b"--" or seg.strip() == b"":
                    continue
                # Each segment may begin with CRLF
                if seg.startswith(b"\r\n"):
                    seg = seg[2:]
                # Remove trailing -- or CRLF
                if seg.endswith(b"--"):
                    seg = seg[:-2]
                if seg.endswith(b"\r\n"):
                    seg = seg[:-2]
                # Separate headers and body by first double CRLF
                header_body = seg.split(b"\r\n\r\n", 1)
                if len(header_body) == 2:
                    raw_headers, body = header_body
                    headers = MultipartParser._parse_headers(raw_headers)
                else:
                    # No headers, treat entire segment as body
                    headers = {}
                    body = seg.strip(b"\r\n")

                content_type = headers.get("content-type", "")
                ptype = MultipartParser._detect_type(content_type, body)
                parts.append(Part(headers=headers, body=body, type=ptype))
        except Exception:
            logger.exception("Failed to parse multipart stream")
        logger.debug("MultipartParser.parse produced %d parts (boundary=%s)", len(parts), boundary)
        return parts