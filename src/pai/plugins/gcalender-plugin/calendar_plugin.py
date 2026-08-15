from typing import Dict, Any, List
from datetime import datetime
import os
import pickle
from loguru import logger

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from pai.plugins.base_plugin import BasePlugin


class CalendarPlugin(BasePlugin):
    """Google Calendar integration."""
    name = "calendar"
    SCOPES = ['https://www.googleapis.com/auth/calendar']
    TOKEN_PICKLE = "data/calendar_token.pickle"
    CREDENTIALS_FILE = os.getenv("GOOGLE_CALENDAR_CREDENTIALS")
    
    async def initialize(self) -> None:
        # Load credentials from config or OAuth flow
        self.service = None
        creds = None
        if os.path.exists(self.TOKEN_PICKLE):
            with open(self.TOKEN_PICKLE, 'rb') as token:
                creds = pickle.load(token)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.CREDENTIALS_FILE, self.SCOPES)
                creds = flow.run_local_server(port=0)
            with open(self.TOKEN_PICKLE, 'wb') as token:
                pickle.dump(creds, token)
        self.service = build('calendar', 'v3', credentials=creds)

    async def check_permissions(self, action: str) -> bool:
        # For simplicity, allow all actions. Implement your own permission logic here.
        return True
    
    async def start(self) -> None:
            self._running = True
            # Implement any startup logic here

    async def shutdown(self) -> None:
        pass
    
    def get_capabilities(self) -> List[str]:
        return ["calendar.create_event", "calendar.list_events", "calendar.get_availability"]
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Any:
        if action == "calendar.create_event":
            return await self._create_event(params)
        elif action == "calendar.list_events":
            return await self._list_events(params)
        elif action == "calendar.get_availability":
            return await self._get_availability(params)
        raise ValueError(f"Unknown action: {action}")
    

    async def _create_event(self, params: Dict) -> Dict:
        event = {
            'summary': params['title'],
            'start': {'dateTime': params['start'], 'timeZone': 'UTC'},
            'end': {'dateTime': params['end'], 'timeZone': 'UTC'},
        }

        if self.service:
            event = self.service.events().insert(calendarId='primary', body=event).execute()
            return {'event_id': event['id'], 'link': event.get('htmlLink')}
        return {'mock_id': 'event123', 'title': params['title']}
    
    async def _list_events(self, params: Dict) -> List[Dict]:
        if not self.service:
            return [{'summary': 'Demo meeting', 'start': '2025-01-01T10:00:00'}]
        events = self.service.events().list(calendarId='primary', maxResults=10).execute()
        return events.get('items', [])
    
    async def _get_availability(self, params: Dict) -> Dict:
        return {'available': True, 'free_slots': ['09:00-10:00', '14:00-15:00']}

    async def handle_event(self, event: str, data: Dict[str, Any]) -> None:
        """Handle an event published on the event bus."""
        logger.info(f"Browser plugin received event: {event} with data: {data}")
        # Implement your event handling logic here