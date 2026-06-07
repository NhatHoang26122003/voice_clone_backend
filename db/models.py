from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean
from sqlalchemy.sql import func
from .database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    token_balance = Column(Integer, default=500)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    account_type = Column(String(50), default='basic') # basic/pro
    pro_expires_at = Column(DateTime, nullable=True)

class VoiceProfile(Base):
    __tablename__ = "voice_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    voice_name = Column(String, index=True, nullable=False)
    ref_audio_path = Column(String, nullable=False) 
    ref_text = Column(String, nullable=False)       
    
    # 2 CỘT MỚI THÊM VÀO CHO CƠ CHẾ CACHING V2.0
    ref_codes_path = Column(String, nullable=True)  # Đường dẫn file codes.pt trên S3
    ref_phones = Column(String, nullable=True)      # Chuỗi âm vị của câu text mẫu

    embedding_path = Column(String, nullable=True)  
    status = Column(String, default="processing")   # verifying | ready | rejected
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_system_voice = Column(Boolean, default=False)


class GeneratedAudio(Base):
    __tablename__ = "generated_audios"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    voice_id = Column(Integer, ForeignKey("voice_profiles.id"), nullable=False)
    text = Column(String, nullable=False)           # Text đầu vào yêu cầu sinh giọng
    audio_path = Column(String, nullable=True)      # Đường dẫn file kết quả sau inference
    status = Column(String, default="queued")       # queued | processing | done | failed
    created_at = Column(DateTime(timezone=True), server_default=func.now())