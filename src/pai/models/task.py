from sqlalchemy import Column, Integer, String, Boolean, JSON, DateTime, ForeignKey
from pai.models.user import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    goal = Column(String)
    plan = Column(JSON)
    status = Column(String, default="pending")
    created_at = Column(DateTime)
    completed_at = Column(DateTime, nullable=True)