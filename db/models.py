from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, DateTime, Text
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
Base = declarative_base()

from datetime import datetime
class EarthEngineKey(Base):
    __tablename__ = 'earth_engine_keys'

    id = Column(Integer, primary_key=True)
    encoded_jwt = Column(Text, nullable=False)  # Store encoded JWT as Text
    createdAt = Column(DateTime, default=datetime.utcnow)  # Record creation time
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)

    # Relationship to the User model
    user = relationship("User", back_populates="earth_engine_keys")