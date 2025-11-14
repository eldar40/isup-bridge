cat > /opt/isup_bridge/src/isapi_server.py << 'EOF'
"""
ISAPI Webhook Server for Hikvision Terminals
"""

from aiohttp import web
import logging
from typing import Dict, Any
from isapi_client import ISAPIWebhookHandler, ISAPITerminalManager


class ISAPIWebhookServer:
    """HTTP Server for ISAPI webhook events"""
    
    def __init__(self, webhook_handler: ISAPIWebhookHandler, config: Dict[str, Any], logger: logging.Logger):
        self.webhook_handler = webhook_handler
        self.config = config
        self.logger = logger
        self.app = web.Application()
        self._setup_routes()
    
    def _setup_routes(self):
        """Setup webhook routes"""
        self.app.router.add_post('/isapi/webhook', self.handle_webhook)
        self.app.router.add_get('/isapi/health', self.health_check)
        self.app.router.add_get('/isapi/terminals', self.list_terminals)
    
    async def handle_webhook(self, request):
        """Handle ISAPI webhook"""
        result = await self.webhook_handler.handle_webhook(request)
        status = 200 if result['status'] in ['success', 'skipped'] else 400
        return web.json_response(result, status=status)
    
    async def health_check(self, request):
        """Health check for ISAPI webhook"""
        return web.json_response({
            'status': 'healthy', 
            'service': 'isapi_webhook',
            'timestamp': __import__('datetime').datetime.now().isoformat()
        })
    
    async def list_terminals(self, request):
        """List all configured terminals"""
        terminal_manager = ISAPITerminalManager(self.config)
        terminals = terminal_manager.get_all_terminals()
        
        terminal_list = []
        for terminal in terminals:
            terminal_list.append({
                'terminal_id': terminal.terminal_id,
                'ip_address': terminal.ip_address,
                'description': terminal.description,
                'type': terminal.terminal_type,
                'direction': terminal.direction,
                'location': terminal.location,
                'object_name': terminal.object_name
            })
        
        return web.json_response({
            'terminals': terminal_list,
            'count': len(terminal_list)
        })
    
    async def start(self):
        """Start ISAPI webhook server"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        
        isapi_config = self.config.get('isapi', {})
        port = isapi_config.get('port', 8082)
        host = isapi_config.get('host', '0.0.0.0')
        
        site = web.TCPSite(runner, host, port)
        await site.start()
        
        self.logger.info(f"🌐 ISAPI Webhook сервер запущен на http://{host}:{port}")
        self.logger.info(f"📮 Webhook endpoint: http://{host}:{port}/isapi/webhook")


class ISAPIDeviceManager:
    """Manager for automatic device configuration"""
    
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger
    
    async def auto_configure_terminals(self, webhook_base_url: str):
        """Automatically configure all terminals"""
        self.logger.info("🔄 Автонастройка терминалов...")
        return []
EOF