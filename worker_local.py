import json
import redis
import time
import modal # Thêm thư viện modal
import os
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from db import models

# XÓA DÒNG IMPORT: from ai_engine.neutts_model import VietnameseVoiceCloneEngine

SQLALCHEMY_DATABASE_URL = "sqlite:///./voice_clone.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

try:
    redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    redis_client.ping()
    print("🤖 Worker (Client) đã kết nối Redis thành công!")
except Exception as e:
    print("❌ Lỗi kết nối Redis:", e)
    exit(1)

def process_voice_clone(task_data):
    audio_id = task_data.get("audio_id")
    text = task_data.get("text")
    ref_audio = task_data.get("ref_audio_path")
    ref_audio_text = task_data.get("ref_text")

    print(f"\n[🔄] Đang xử lý Audio ID: {audio_id}")
    print(" ├─ Gửi dữ liệu lên GPU Modal...")
    
    output_relative_path = f"/storage/voices/generated/ai_output_{audio_id}.wav"
    output_physical_path = f"./storage/voices/generated/ai_output_{audio_id}.wav"
    
    db = SessionLocal()
    try:
        # 1. Đọc file âm thanh từ ổ cứng em thành Bytes
        with open(ref_audio, "rb") as f:
            ref_audio_bytes = f.read()
            
        # 2. Gọi hàm trên Modal (Hành động này sẽ đánh thức GPU trên đám mây)
        RemoteEngine = modal.Cls.from_name("vietnamese-voice-clone", "VietnameseVoiceCloneEngine")
        engine = RemoteEngine()
        output_bytes = engine.process.remote(
            text=text, 
            ref_audio_bytes=ref_audio_bytes, 
            ref_text=ref_audio_text
        )
        
        # 3. Modal trả về âm thanh dạng Bytes, lưu xuống máy em
        os.makedirs(os.path.dirname(output_physical_path), exist_ok=True)
        with open(output_physical_path, "wb") as f:
            f.write(output_bytes)
            
        # 4. Cập nhật DB
        db_audio = db.query(models.GeneratedAudio).filter(models.GeneratedAudio.id == audio_id).first()
        if db_audio and db_audio.status != 'cancelled':
            db_audio.status = 'ready'
            db_audio.audio_path = output_relative_path 
            db.commit()
            print(f" └─ ✅ Xong! Đã cập nhật status thành 'ready'.")

    except Exception as e:
        print(f"❌ Lỗi khi chạy AI: {e}")
        db.rollback()
        db_audio = db.query(models.GeneratedAudio).filter(models.GeneratedAudio.id == audio_id).first()
        if db_audio:
            db_audio.status = 'failed'
            db.commit()
    finally:
        db.close()

def start_worker():
    print("🎧 Worker đang lắng nghe hàng đợi 'voice_clone_tasks'...")
    while True:
        try:
            queue_name, message = redis_client.blpop("voice_clone_tasks", 0)
            task_data = json.loads(message)
            process_voice_clone(task_data)
        except Exception as e:
            print("Lỗi vòng lặp Worker:", e)
            time.sleep(5)

if __name__ == "__main__":
    start_worker()