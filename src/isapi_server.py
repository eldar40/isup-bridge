"""
ISAPI Webhook Server for Hikvision Terminals
HTTP сервер для приема событий от терминалов Hikvision
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
        self.app.router.add_post('/ISAPI/Event/notification/alertStream', self.handle_webhook)  # Альтернативный endpoint
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
        self.logger.info(f"📋 Список терминалов: http://{host}:{port}/isapi/terminals")
        self.logger.info(f"❤️  Health check: http://{host}:{port}/isapi/health")


class ISAPIDeviceManager:
    """Manager for automatic device configuration"""
    
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.terminal_manager = ISAPITerminalManager(config)
    
    async def auto_configure_terminals(self, webhook_base_url: str):
        """Automatically configure all terminals"""
        terminals = self.terminal_manager.get_all_terminals()
        results = []
        
        self.logger.info(f"🔄 Автонастройка {len(terminals)} терминалов...")
        
        for terminal in terminals:
            result = await self._configure_terminal(terminal, webhook_base_url)
            results.append(result)
        
        success_count = sum(1 for r in results if r['success'])
        self.logger.info(f"✅ Автонастройка завершена: {success_count}/{len(terminals)} успешно")
        
        return results
    
    async def _configure_terminal(self, terminal: Any, webhook_base_url: str) -> Dict[str, Any]:
        """Configure individual terminal"""
        try:
            # Создаем клиент для терминала
            from isapi_client import ISAPIClient
            
            # Используем учетные данные из конфига или default
            isapi_config = self.config.get('isapi', {})
            username = isapi_config.get('username', 'admin')
            password = isapi_config.get('password', 'admin123')
            
            client = ISAPIClient(
                base_url=f"http://{terminal.ip_address}",
                username=username,
                password=password
            )
            
            # Проверяем активацию
            is_activated = await client.check_activation_status()
            if not is_activated:
                return {
                    'terminal_id': terminal.terminal_id,
                    'ip_address': terminal.ip_address,
                    'success': False,
                    'error': 'Device not activated'
                }
            
            # Настраиваем webhook
            webhook_url = f"{webhook_base_url}/isapi/webhook"
            webhook_configured = await client.configure_webhook(webhook_url)
            
            return {
                'terminal_id': terminal.terminal_id,
                'ip_address': terminal.ip_address,
                'success': webhook_configured,
                'webhook_url': webhook_url
            }
            
        except Exception as e:
            self.logger.error(f"❌ Ошибка настройки терминала {terminal.ip_address}: {e}")
            return {
                'terminal_id': terminal.terminal_id,
                'ip_address': terminal.ip_address,
                'success': False,
                'error': str(e)
            }