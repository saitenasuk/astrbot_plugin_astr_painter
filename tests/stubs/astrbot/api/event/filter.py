"""装饰器桩：只记录元数据，返回原函数。"""


def _marker(kind):
    def decorator(*args, **kwargs):
        def wrap(func):
            func.__handler__ = kind
            func.__handler_args__ = (args, kwargs)
            return func

        return wrap

    return decorator


command = _marker("command")
command_group = _marker("command_group")
llm_tool = _marker("llm_tool")
on_llm_request = _marker("on_llm_request")
on_llm_response = _marker("on_llm_response")
event_message_type = _marker("event_message_type")
permission_type = _marker("permission_type")
