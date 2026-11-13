class Channel:
    def __init__(s, id=0, title="", broadcast=True): s.id, s.title, s.broadcast = id, title, broadcast
class Chat:
    def __init__(s, id=0, title=""): s.id, s.title = id, title
class User:
    def __init__(s, id=0, first_name="", bot=False): s.id, s.first_name, s.bot = id, first_name, bot
class ChannelParticipantBanned: pass
class ChannelParticipantLeft: pass
class ChatInviteAlready: pass
class MessageMediaWebPage: pass
class PeerChannel: pass
