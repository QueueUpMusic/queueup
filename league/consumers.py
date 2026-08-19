from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer


@database_sync_to_async
def can_access_league_realtime(user):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.profile.approved


class LeagueConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        if not await can_access_league_realtime(self.scope['user']):
            await self.close()
            return
        await self.channel_layer.group_add('league_live', self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard('league_live', self.channel_name)

    async def league_event(self, event):
        await self.send_json(event['payload'])
