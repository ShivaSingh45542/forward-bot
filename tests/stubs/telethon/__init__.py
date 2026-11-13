"""Tiny fake of Telethon, just enough to import forward_bot_multi.py and test its logic without Telegram."""
class _B:
    def __init__(s, kind, text, val): s.kind, s.text, s._v = kind, text, val
    def __getitem__(s, i): return (s.kind, s.text, s._v)[i]
    def __repr__(s): return repr((s.kind, s.text, s._v))
class _KB(_B):
    def __init__(s, text, data): super().__init__("inline", text, data); s.data = data
class _UB(_B):
    def __init__(s, text, url): super().__init__("url", text, url); s.url = url
class Button:
    @staticmethod
    def inline(text, data): return _KB(text, data)
    @staticmethod
    def url(text, url): return _UB(text, url)
class _Ev:
    class CallbackQuery: pass
    class NewMessage:
        def __init__(self, **k): pass
    class Album: pass
    class MessageEdited: pass
    class MessageDeleted: pass
events = _Ev
class utils:
    @staticmethod
    def get_display_name(x): return getattr(x, "title", None) or getattr(x, "first_name", "?")
    @staticmethod
    def get_peer_id(x): return getattr(x, "id", 0)
class TelegramClient:
    def __init__(self, *a, **k): pass
    def on(self, ev): return lambda f: f
    def add_event_handler(self, *a): pass
