import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Reading, Thresholds, EventLog
from django.forms.models import model_to_dict


class MonitoringConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("monitoring", self.channel_name)
        await self.accept()
        
        # Send initial data
        await self.send_status_update()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("monitoring", self.channel_name)

    async def receive(self, text_data):
        # Handle incoming messages if needed
        pass

    async def monitoring_update(self, event):
        # Send monitoring update to WebSocket
        await self.send(text_data=json.dumps(event['data']))

    @database_sync_to_async
    def get_status_data(self):
        latest = Reading.objects.order_by('-created_at').first()
        thresholds = Thresholds.objects.order_by('-updated_at').first()
        events = EventLog.objects.order_by('-created_at')[:10]
        
        state = 'SAFE'
        if latest and thresholds:
            if latest.current > thresholds.max_current or latest.current < thresholds.min_current:
                state = 'FAULT'
            elif latest.voltage > thresholds.max_voltage or latest.voltage < thresholds.min_voltage:
                state = 'FAULT'
            elif latest.temperature > thresholds.max_temperature:
                state = 'FAULT'
        
        return {
            'latest_reading': model_to_dict(latest) if latest else None,
            'thresholds': model_to_dict(thresholds) if thresholds else None,
            'state': state,
            'events': [
                {
                    'id': e.id,
                    'event_type': e.event_type,
                    'details': e.details,
                    'response': e.response,
                    'created_at': e.created_at.isoformat(),
                }
                for e in events
            ]
        }

    async def send_status_update(self):
        data = await self.get_status_data()
        await self.send(text_data=json.dumps({
            'type': 'status_update',
            'data': data
        }))
