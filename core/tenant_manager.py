class TenantManager:
    def __init__(self, cfg: dict):
        self.tenants = {}
        for obj in cfg.get("objects", []):
            self.tenants[obj["object_id"]] = obj

    def get_tenant(self, object_id: str):
        return self.tenants.get(object_id)
