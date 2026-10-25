class Context:
    pass


class StarTools:
    _data_root = None

    @classmethod
    def get_data_dir(cls, plugin_name=None):
        from pathlib import Path

        root = cls._data_root or Path("/tmp/astrbot-stub")
        path = Path(root) / plugin_name
        path.mkdir(parents=True, exist_ok=True)
        return path


class Star:
    _kv: dict = {}

    def __init__(self, context, config=None):
        self.context = context
        self.config = config
        import logging

        self.logger = logging.getLogger("astrbot-stub.plugin")

    async def initialize(self):
        return None

    async def terminate(self):
        return None

    async def get_kv_data(self, key, default=None):
        return self._kv.get(key, default)

    async def put_kv_data(self, key, value):
        self._kv[key] = value


def register(*_args, **_kwargs):
    def wrap(cls):
        return cls

    return wrap
