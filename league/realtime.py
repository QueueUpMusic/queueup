from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

def broadcast(event, **payload):
    layer = get_channel_layer()
    async_to_sync(layer.group_send)('league_live', {'type': 'league.event', 'payload': {'event': event, **payload}})
