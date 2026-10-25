import logging

logger = logging.getLogger("astrbot-stub")


class AstrBotConfig(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def save_config(self) -> None:
        return None
