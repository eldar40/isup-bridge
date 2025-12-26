# ISUP-Bridge AI Coding Instructions

## Architecture Overview
ISUP-Bridge is an async Python service that bridges Hikvision access control events to 1C ERP systems. It unifies events from multiple protocols (ISUP TCP, ISAPI HTTP webhooks, Hikvision callbacks) into JSON format, maps them to multi-tenant configurations, and sends to 1C with retry logic.

**Core Components** (`core/`):
- `processor.py`: Central event processing, unification, and routing
- `tenant_manager.py`: Manages tenant (object) configurations and 1C integrations
- `storage.py`: Local SQLite storage for failed events with auto-retry
- `metrics.py`: Prometheus-style metrics collection

**Protocol Handlers**:
- `isup/`: TCP server for ISUP v5 binary protocol from turnstiles
- `isapi/`: HTTP webhook server for ISAPI XML events from terminals
- `hikvision/`: Callback listener for Hikvision XML notifications

**Key Data Flow**:
1. Events parsed from protocols → unified JSON in `processor.py`
2. Mapped to tenant by MAC/device_id from `config/config.yaml`
3. Enriched with tenant metadata
4. HTTP POST to 1C with Basic Auth
5. Failures stored in `./storage/` for retry

## Critical Workflows
- **Run Service**: `python main.py` (loads `config/config.yaml` and `config/hikvision.yaml`)
- **Test**: `pytest` (async tests marked with `@pytest.mark.asyncio`)
- **Debug Events**: Check logs for event unification; use `/health` endpoint for status
- **Add Tenant**: Edit `config/config.yaml` objects array with c1 settings and terminals/devices
- **Retry Logic**: Failed events auto-retry every 10s; stored in SQLite (`storage/events.db`)

## Project Conventions
- **Async First**: All I/O uses `asyncio` + `aiohttp`; avoid blocking calls
- **Event Unification**: Convert all events to dict with keys: `timestamp`, `device_id`, `direction`, `success`, `card_number`, `employee_number`, `event_source`
- **Tenant Mapping**: ISAPI uses `mac_address`, ISUP uses `device_id`; fallback to `default_object`
- **Error Handling**: Log errors, increment metrics, save to storage on send failures
- **Config Structure**: YAML with `objects` (tenants), each having `c1` (1C API), `terminals` (ISAPI), `devices` (ISUP)
- **Logging**: Use `logger` from `utils/logging_setup.py`; levels INFO/ERROR for ops

## Specific Patterns
- **Send to 1C**: `aiohttp.ClientSession().post()` with JSON payload, Basic Auth from config
- **Parse ISUP**: Use `isup_parser.parse()` in `processor.py` for binary packets
- **Normalize Timestamps**: ISO format strings; use `datetime.now().isoformat()` for missing
- **IP Security**: Check `client_ip` against `security.allowed_ips` in config
- **Metrics**: Increment `metrics.events_ok/failed/retried_*` on outcomes

## Examples
- **Add ISUP Device**: In `config/config.yaml`, add to object's `devices` array: `device_id: "HEX_ID", description: "Turnstile"`
- **Handle New Event Type**: Extend `_unify_isup_event()` or `_normalize_callback_event()` in `processor.py`
- **Custom 1C Endpoint**: Modify `tenant_manager.py` `send_to_1c()` for different auth/payload

Reference: `main.py` for startup, `processor.py` for logic, `config/config.yaml` for structure.