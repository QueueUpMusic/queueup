from channels.generic.websocket import AsyncJsonWebsocketConsumer

class LeagueConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        if not self.scope['user'].is_authenticated:
            await self.close(); return
        await self.channel_layer.group_add('league_live', self.channel_name)
        await self.accept()
    async def disconnect(self, code):
        await self.channel_layer.group_discard('league_live', self.channel_name)
    async def league_event(self, event):
        await self.send_json(event['payload'])
