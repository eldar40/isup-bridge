"""Hikvision alertStream client.

Connects to ``/ISAPI/Event/notification/alertStream`` with digest auth and
parses multipart XML/JPEG payloads according to Hikvision ISAPI docs.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from typing import Optional
from urllib.parse import urlparse

import aiohttp

from .event_dispatcher import HikvisionEventDispatcher
from .multipart_parser import MultipartParser


class HikvisionAlertStream:
    """Persistent alertStream reader with auto-reconnect."""

    def __init__(
        self,
        ip: str,
        username: str,
        password: str,
        dispatcher: HikvisionEventDispatcher,
        name: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        reconnect_delay: float = 5.0,
        heartbeat_timeout: float = 60.0,
    ) -> None:
        self.ip = ip
        self.username = username
        self.password = password
        self.dispatcher = dispatcher
        self.name = name or ip
        self.log = logger or logging.getLogger(__name__)
        self.reconnect_delay = reconnect_delay
        self.heartbeat_timeout = heartbeat_timeout

        self._running = False
        self._last_event_ts: float = time.time()

    # Public API -------------------------------------------------------------
    async def run(self) -> None:
        """Run alertStream loop with auto-reconnect."""

        if self._running:
            return
        self._running = True

        while self._running:
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                self.log.exception("Alert stream error for %s: %s", self.name, exc)

            if self._running:
                self.log.info("Reconnecting alert stream for %s in %.1fs", self.name, self.reconnect_delay)
                await asyncio.sleep(self.reconnect_delay)

    async def stop(self) -> None:
        self._running = False

    # Internal ---------------------------------------------------------------
    async def _connect_and_stream(self) -> None:
        url = f"http://{self.ip}/ISAPI/Event/notification/alertStream"
        timeout = aiohttp.ClientTimeout(sock_connect=10.0, sock_read=self.heartbeat_timeout)

        self.log.info("Connecting to Hikvision alertStream %s (%s)", self.name, url)

        async with aiohttp.ClientSession(headers={"Connection": "Keep-Alive"}, timeout=timeout) as session:
            response = await session.get(url)
            if response.status == 401:
                auth_header = response.headers.get("WWW-Authenticate")
                if not auth_header:
                    response.raise_for_status()
                auth_value = self._build_digest_header("GET", url, auth_header)
                await response.release()
                response = await session.get(url, headers={"Authorization": auth_value, "Connection": "Keep-Alive"})

            if response.status == 401:
                self.log.error("Digest authentication failed for %s", self.name)
                response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            boundary = self._extract_boundary(content_type)
            self.log.info("alertStream connected for %s, boundary=%s", self.name, boundary)

            buffer = b""
            async for chunk in response.content.iter_chunked(2048):
                if not self._running:
                    break
                if chunk:
                    buffer += chunk
                    self._last_event_ts = time.time()
                    buffer = await self._process_buffer(buffer, boundary)

                if (time.time() - self._last_event_ts) > self.heartbeat_timeout:
                    self.log.warning("Heartbeat timeout for %s, reconnecting", self.name)
                    break

    async def _process_buffer(self, buffer: bytes, boundary: str) -> bytes:
        if not boundary:
            return buffer

        marker = ("--" + boundary).encode()
        parts_data = buffer.split(marker)
        # Keep last incomplete segment as buffer remainder
        remainder = parts_data.pop() if parts_data else b""
        if remainder.strip() in (b"", b"--"):
            remainder = b""

        for raw_part in parts_data:
            raw_part = raw_part.strip(b"\r\n")
            if not raw_part or raw_part == b"--":
                continue
            part_payload = marker + raw_part
            for part in MultipartParser.parse(part_payload, boundary):
                await self._handle_part(part.type, part.body)
        return remainder

    async def _handle_part(self, part_type: str, body: bytes) -> None:
        if part_type == "image":
            self.log.debug("Received image part (%d bytes) from %s", len(body), self.name)
            return

        if part_type in {"xml", "json"}:
            payload = body.decode("utf-8", errors="ignore")
            event = (
                self.dispatcher.parse_xml(payload)
                if part_type == "xml"
                else self.dispatcher.parse_json(payload)
            )
            if event:
                if len(event) == 1 and isinstance(next(iter(event.values())), dict):
                    payload_dict = next(iter(event.values()))
                else:
                    payload_dict = event if isinstance(event, dict) else {"data": event}

                if "eventType" in payload_dict:
                    self.log.info("Event type: %s", payload_dict.get("eventType"))
                self.dispatcher.handle_event(payload_dict)
            else:
                self.log.warning("Failed to parse %s payload from %s", part_type, self.name)
        else:
            self.log.debug("Unknown part type %s from %s", part_type, self.name)

    def _build_digest_header(self, method: str, url: str, auth_header: str) -> str:
        """Construct Digest Authorization header value."""

        if not auth_header.lower().startswith("digest"):
            return ""

        challenge = auth_header[len("Digest ") :]
        parts = {}
        for item in challenge.split(","):
            if "=" not in item:
                continue
            k, v = item.split("=", 1)
            parts[k.strip()] = v.strip().strip('"')

        realm = parts.get("realm", "")
        nonce = parts.get("nonce", "")
        qop = parts.get("qop", "auth")
        opaque = parts.get("opaque")

        parsed = urlparse(url)
        uri = parsed.path or "/"
        if parsed.query:
            uri += f"?{parsed.query}"

        ha1 = hashlib.md5(f"{self.username}:{realm}:{self.password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        nonce_count = "00000001"
        cnonce = secrets.token_hex(8)
        response = hashlib.md5(
            f"{ha1}:{nonce}:{nonce_count}:{cnonce}:{qop}:{ha2}".encode()
        ).hexdigest()

        header = (
            f'Digest username="{self.username}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", algorithm="MD5", response="{response}", qop={qop}, nc={nonce_count}, cnonce="{cnonce}"'
        )
        if opaque:
            header += f', opaque="{opaque}"'
        return header

    def _extract_boundary(self, content_type: str) -> str:
        if not content_type:
            return ""
        if "boundary=" not in content_type:
            return ""
        # Split content-type parameters
        parts = content_type.split(";")
        for part in parts:
            if "boundary=" in part:
                key, value = part.split("=", 1)
                return value.strip().strip('"')
        return ""


__all__ = ["HikvisionAlertStream"]
