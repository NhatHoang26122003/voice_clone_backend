import os
from dotenv import load_dotenv

# 1. NẠP BIẾN MÔI TRƯỜNG TRƯỚC
load_dotenv()

import json
import redis
import time
import modal
import boto3
from botocore.client import Config
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from db import models
from core.config import settings

# Khởi tạo kết nối S3
s3_client = boto3.client(
    's3',
    endpoint_url=settings.AWS_ENDPOINT_URL if settings.AWS_ENDPOINT_URL else None,
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION,
    config=Config(signature_version='s3v4')
)

engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

try:
    redis_client = redis.Redis(
        host=settings.REDIS_HOST, 
        port=settings.REDIS_PORT, 
        password=settings.REDIS_PASSWORD,
        db=0, decode_responses=True
    )
    redis_client.ping()
except Exception as e:
    print("❌ Lỗi kết nối Redis:", e)
    exit(1)

def process_voice_clone(task_data):
    audio_id = task_data.get("audio_id")
    text = task_data.get("text")
    ref_audio_key = task_data.get("ref_audio_path") # Lúc này là key trên S3
    ref_text = task_data.get("ref_text")

    print(f"\n[🔄] Đang xử lý Audio ID: {audio_id}")
    db = SessionLocal()
    try:
        # 1. Tải file âm thanh từ S3 vào RAM (dạng Bytes)
        print(" ├─ Đang tải Audio từ Storage...")
        s3_response = s3_client.get_object(Bucket=settings.AWS_BUCKET_NAME, Key=ref_audio_key)
        ref_audio_bytes = s3_response['Body'].read()
            
        # 2. Gọi AI Modal xử lý
        print(" ├─ Gửi dữ liệu lên GPU Modal...")
        RemoteEngine = modal.Cls.from_name("vietnamese-voice-clone", "VietnameseVoiceCloneEngine")
        ai_engine = RemoteEngine()
        output_bytes = ai_engine.process.remote(
            text=text, 
            ref_audio_bytes=ref_audio_bytes, 
            ref_text=ref_text
        )
        
        # 3. Đẩy file kết quả (Bytes) lên thẳng S3
        print(" ├─ Đang lưu kết quả lên Storage...")
        output_key = f"generated_audios/{audio_id}/ai_output.wav"
        s3_client.put_object(
            Bucket=settings.AWS_BUCKET_NAME,
            Key=output_key,
            Body=output_bytes,
            ContentType='audio/wav'
        )
            
        # 4. Cập nhật DB
        db_audio = db.query(models.GeneratedAudio).filter(models.GeneratedAudio.id == audio_id).first()
        if db_audio:
            db_audio.status = 'ready'
            db_audio.audio_path = output_key  # Lưu S3 Key vào Database
            db.commit()
            print(f" └─ ✅ Xong! Đã cập nhật trạng thái.")

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
            print("Lỗi vòng lặp:", e)
            time.sleep(5)

if __name__ == "__main__":
    start_worker()