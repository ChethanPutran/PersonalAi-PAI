from sqlalchemy.ext.asyncio import AsyncSession


class SessionManager:
    def __init__(self):
        self.sessions = {
            "default": AsyncSession()
        }

    async def create_session(self, user_id: str) -> AsyncSession:
        # Logic to create a new session for the user
        session = AsyncSession()
        self.sessions[user_id] = session
        return session

    def get_session(self, session_id: str)-> AsyncSession:
        # Logic to retrieve a session by its ID
        if(session_id not in self.sessions):
            raise ValueError(f"Session with ID {session_id} does not exist.")
        return self.sessions[session_id]

    async def delete_session(self, session_id: str):
        # Logic to delete a session by its ID
        session = self.sessions.get(session_id)
        if session:
            await session.close()
        del self.sessions[session_id]

    