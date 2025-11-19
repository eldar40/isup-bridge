# -*- coding: utf-8 -*-
"""
ISUP TCP Server — принимает бинарные пакеты Hikvision ISUP v5,
передаёт их процессору, отправляет корректный ACK.
"""

import asyncio
import logging
from typing import Optional


class ISUPTCPServer:

    def __init__(self, host: str, port: int, event_processor, metrics, parser, logger: Optional[logging.Logger] = None):
        self.host = host
        self.port = port
        self.processor = event_processor
        self.metrics = metrics
        self.parser = parser
        self.log = logger or logging.getLogger(self.__class__.__name__)
        self.server: Optional[asyncio.base_events.Server] = None

    # =====================================================================
    # START SERVER
    # =====================================================================

    async def start(self):
        """Запуск TCP-сервера."""
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port
        )

        addr = self.server.sockets[0].getsockname()
        self.log.info(f"🚀 ISUP TCP server listening on {addr[0]}:{addr[1]}")

        async with self.server:
            await self.server.serve_forever()

    # =====================================================================
    # STOP SERVER
    # =====================================================================

    async def stop(self):
        """Остановка сервера."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.log.info("🛑 ISUP TCP server stopped")

    # =====================================================================
    # HANDLE CLIENT
    # =====================================================================

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Обработка подключения контроллера."""
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else "unknown"

        self.metrics.connections_total += 1
        self.log.info(f"🔌 New ISUP connection from {ip}")

        try:
            while True:
                try:
                    data = await asyncio.wait_for(reader.read(4096), timeout=30)
                except asyncio.TimeoutError:
                    self.log.info(f"⏳ ISUP timeout from {ip}, closing.")
                    break

                if not data:
                    self.log.info(f"🔌 Connection from {ip} closed by client")
                    break

                self.metrics.events_received += 1
                self.metrics.last_event_time = __import__("datetime").datetime.utcnow()

                self.log.debug(f"📥 Received {len(data)} bytes from {ip}")

                # Parse packet
                event = self.parser.parse(data)
                if event:
                    self.metrics.events_parsed += 1
                    await self.processor.process_isup_packet(data, ip)

                # Send ACK response (always required by ISUP)
                ack = self._make_ack(event)
                if ack:
                    writer.write(ack)
                    await writer.drain()
                    self.log.debug(f"📤 ACK sent to {ip}")

        except Exception as e:
            self.log.error(f"❌ Error handling client {ip}: {e}")

        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass

            self.log.info(f"🔌 Connection with {ip} closed")

    # =====================================================================
    # ACK GENERATION
    # =====================================================================

    def _make_ack(self, event) -> Optional[bytes]:
        """
        Формирует бинарный ответ ISUP ACK.
        Если event == None → heartbeat ACK.
        """

        try:
            if not event:
                # heartbeat response "##" + version=5 + 0x01 + len=0 + zeros + crc
                return self.parser.make_heartbeat_ack()

            return self.parser.make_ack(event.header.sequence_number)

        except Exception as e:
            self.log.error(f"ACK build error: {e}")
            return None