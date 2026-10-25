from astrbot.api.message_components import Image

from . import filter as filter


class Result:
    def __init__(self, kind, *, text="", chain=None, event=None):
        self.kind = kind
        self.text = text
        self.chain = chain or []
        self._event = event

    def stop_event(self):
        if self._event is not None:
            self._event.stopped = True
        return self


class AstrMessageEvent:
    def __init__(
        self, message_str="", messages=None, sender_id="10001", admin=False, umo="platform:1:1"
    ):
        self.message_str = message_str
        self.message_obj = type("Obj", (), {"message": messages or []})()
        self._messages = messages or []
        self._sender_id = sender_id
        self._admin = admin
        self.unified_msg_origin = umo
        self.sent = []
        self.stopped = False

    def get_messages(self):
        return self._messages

    def get_message_str(self):
        return self.message_str

    def get_sender_id(self):
        return self._sender_id

    def is_admin(self):
        return self._admin

    def plain_result(self, text):
        return Result("plain", text=text, event=self)

    def chain_result(self, chain):
        return Result("chain", chain=chain, event=self)

    def image_result(self, path):
        return Result("chain", chain=[Image(path)], event=self)

    async def send(self, result):
        self.sent.append(result)

    def stop_event(self):
        self.stopped = True
