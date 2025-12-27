import asyncio
import logging
from asyncio import IncompleteReadError


class ISUPTCPServer:
    def __init__(self, host, port, processor, metrics, parser, logger: logging.Logger):
        self.host = host
        self.port = port
        self.processor = processor
        self.metrics = metrics
        self.parser = parser
        self.log = logger
        self.server: asyncio.AbstractServer | None = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle_client, host=self.host, port=self.port)
        sockets = self.server.sockets or []
        for sock in sockets:
            self.log.debug("ISUP TCP listening on %s", sock.getsockname())

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if isinstance(peer, tuple) else str(peer)
        self.metrics.connections_total += 1
        self.log.info("New ISUP connection from %s", peer_ip)

        try:
            while True:
                header_bytes = await reader.readexactly(self.parser.HEADER_SIZE)
                header = self.parser._parse_header(header_bytes)  # type: ignore[attr-defined]
                if not header:
                    self.log.warning("Invalid ISUP header from %s", peer_ip)
                    break

                body = await reader.readexactly(header.data_length)
                packet = header_bytes + body
                await self.processor.process_isup_packet(packet, peer_ip)

                if header.data_length == 0:
                    ack = self.parser.make_heartbeat_ack()
                else:
                    ack = self.parser.make_ack(header.sequence_number)

                if ack:
                    writer.write(ack)
                    await writer.drain()
        except IncompleteReadError:
            self.log.info("Connection from %s closed", peer_ip)
        except Exception as exc:  # pragma: no cover - defensive
            self.log.error("ISUP connection error from %s: %s", peer_ip, exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
