import os
import uuid
import json
import redis
import boto3
from botocore.exceptions import ClientError
from botocore.client import Config
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_

from db.database import get_db
from db import models
from api.auth import get_current_user
from schemas.voice import GenerateAudioRequest
from pydantic import BaseModel
from core.config import settings
from sqlalchemy import or_ 

router = APIRouter()

# Khởi tạo kết nối Redis
try:
    redis_client = redis.Redis(
        host=settings.REDIS_HOST, 
        port=settings.REDIS_PORT, 
        password=settings.REDIS_PASSWORD,
        db=0, 
        decode_responses=True
    )
    redis_client.ping()
except Exception as e:
    redis_client = None
    print("⚠️ Cảnh báo: Chưa kết nối được Redis.")

# Khởi tạo kết nối S3 (Tương thích cả MinIO Local và AWS thật)
s3_client = boto3.client(
    's3',
    endpoint_url=settings.AWS_ENDPOINT_URL if settings.AWS_ENDPOINT_URL else None,
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION,
    config=Config(signature_version='s3v4')
)

# 2. THÊM ĐOẠN NÀY: Khởi tạo S3 Client Public (Chuyên dùng sinh link cho Mobile)
public_s3_client = boto3.client(
    's3',
    endpoint_url=settings.CLIENT_MINIO_URL, # Ép dùng link Public
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION,
    config=Config(signature_version='s3v4')
)

class ConfirmUploadRequest(BaseModel):
    voice_name: str
    ref_text: str
    file_key: str  # Đường dẫn file trên S3 (VD: voices/1/recording.wav)

# @router.get("/get-presigned-url")
# def get_presigned_url(file_name: str, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
#     """API sinh đường link tải lên trực tiếp (Có hiệu lực 5 phút)"""
#     user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
    
#     unique_filename = f"{uuid.uuid4().hex[:8]}_{file_name}"
#     object_key = f"voices/{user.id}/{unique_filename}"
    
#     try:
#         presigned_url = s3_client.generate_presigned_url(
#             'put_object',
#             Params={
#                 'Bucket': settings.AWS_BUCKET_NAME,
#                 'Key': object_key,
#                 'ContentType': 'audio/wav' 
#             },
#             ExpiresIn=300
#         )
#         client_upload_url = presigned_url.replace(settings.AWS_ENDPOINT_URL, settings.CLIENT_MINIO_URL)
#         client_public_url = f"{settings.CLIENT_MINIO_URL}/{settings.AWS_BUCKET_NAME}/{object_key}"

#         print(f"Generated presigned URL for user: {client_upload_url}")
#         return {
#             "upload_url": client_upload_url,
#             "file_key": object_key,
#             "public_url": client_public_url
#         }
#     except ClientError as e:
#         raise HTTPException(status_code=500, detail=str(e))

@router.get("/get-presigned-url")
def get_presigned_url(file_name: str, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """API sinh đường link tải lên trực tiếp (Có hiệu lực 5 phút)"""
    user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
    
    unique_filename = f"{uuid.uuid4().hex[:8]}_{file_name}"
    object_key = f"voices/{user.id}/{unique_filename}"
    
    try:
        client_upload_url = public_s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': settings.AWS_BUCKET_NAME,
                'Key': object_key,
                'ContentType': 'audio/wav' 
            },
            ExpiresIn=300
        )
        
        client_public_url = f"{settings.CLIENT_MINIO_URL}/{settings.AWS_BUCKET_NAME}/{object_key}"

        print(f"✅ Generated presigned URL for user: {client_upload_url}")
        return {
            "upload_url": client_upload_url,
            "file_key": object_key,
            "public_url": client_public_url
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm-upload")
def confirm_upload(
    request: ConfirmUploadRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """API được Mobile gọi SAU KHI đã tải file lên Storage thành công"""
    user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
    
    # 1. Khởi tạo VoiceProfile với trạng thái verifying (Đang xác thực bằng Whisper)
    db_voice = models.VoiceProfile(
        user_id=user.id,
        voice_name=request.voice_name,
        ref_audio_path=request.file_key, 
        ref_text=request.ref_text,
        status="verifying" 
    )
    db.add(db_voice)
    db.commit()
    db.refresh(db_voice)

    # 2. Đẩy Task "Trích xuất & Xác thực" vào Redis
    task_payload = {
        "task_type": "extract_profile", # <--- DÁN NHÃN LOẠI TASK
        "voice_id": db_voice.id,
        "ref_audio_path": db_voice.ref_audio_path,
        "ref_text": db_voice.ref_text
    }
    
    if redis_client:
        redis_client.rpush("voice_clone_tasks", json.dumps(task_payload))
        print(f"🚀 Đã đẩy task EXTRACT PROFILE cho Voice ID: {db_voice.id}")

    return {
        "message": "Đang xác thực và trích xuất đặc trưng giọng nói...",
        "id": db_voice.id,
        "voice_name": db_voice.voice_name,
        "status": db_voice.status
    }


@router.get("/")
def get_user_voices(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try: 
        user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

        voices = (
            db.query(models.VoiceProfile)
            .filter(
                or_(
                    models.VoiceProfile.user_id == user.id,
                    models.VoiceProfile.is_system_voice == True
                )
            )
            .order_by(models.VoiceProfile.created_at.desc())
            .all()
        )

        result = []
        for v in voices:
            audio_url = None
            if v.ref_audio_path:
                # Backend tạo Presigned GET URL cho phép Mobile đọc file trong 1 tiếng
                try:
                    audio_url = public_s3_client.generate_presigned_url(
                        'get_object',
                        Params={
                            'Bucket': settings.AWS_BUCKET_NAME,
                            'Key': v.ref_audio_path
                        },
                        ExpiresIn=3600 # 3600 giây = 1 giờ
                    )
                except Exception as e:
                    print(f"⚠️ Lỗi tạo link S3: {e}")
                    audio_url = None

            result.append({
                "id": v.id,
                "voice_name": v.voice_name,
                "ref_audio_path": audio_url, # Mobile sẽ nhận được 1 link S3 hoặc MinIO dài ngoằng để play luôn
                "ref_text": v.ref_text,
                "status": v.status,
                "is_system_voice": v.is_system_voice,
                "created_at": v.created_at.isoformat() if v.created_at else None
            })

        return {"data": result, "total": len(result)}
    except Exception as e:
        print("🔥 API ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))
    

@router.get("/generated")
def get_generated_audios(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

        audios = (
            db.query(models.GeneratedAudio)
            .filter(models.GeneratedAudio.user_id == user.id)
            .order_by(models.GeneratedAudio.created_at.desc())
            .all()
        )

        result = []
        for a in audios:
            audio_url = None
            if a.audio_path:
                # Tạo Link đọc từ S3 cho Audio kết quả
                try:
                    audio_url = public_s3_client.generate_presigned_url(
                        'get_object',
                        Params={
                            'Bucket': settings.AWS_BUCKET_NAME,
                            'Key': a.audio_path
                        },
                        ExpiresIn=3600
                    )
                except Exception as e:
                    print(f"⚠️ Lỗi tạo link S3: {e}")
                    audio_url = None

            result.append({
                "id": a.id,
                "text": a.text,
                "audio_path": audio_url,
                "status": a.status, 
                "created_at": a.created_at.strftime("%d/%m/%Y %H:%M") if a.created_at else None
            })

        return {"data": result}

    except Exception as e:
        print("🔥 API GET GENERATED ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))
    

# @router.post("/generate")
# def generate_cloned_audio(
#     request: GenerateAudioRequest,
#     db: Session = Depends(get_db),
#     current_user: dict = Depends(get_current_user)
# ):
#     try:
#         print("🔥 Tiếp nhận yêu cầu sinh giọng nói mới siêu tốc")
        
#         user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
#         if not user:
#             raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

#         # 1. Kiểm tra quyền sở hữu giọng nói
#         voice = db.query(models.VoiceProfile).filter(
#             models.VoiceProfile.id == request.voice_id,
#             or_(
#                 models.VoiceProfile.user_id == user.id,
#                 models.VoiceProfile.is_system_voice == True
#             )
#         ).first()
        
#         if not voice:
#             raise HTTPException(status_code=404, detail="Cấu hình giọng nói không tồn tại")
            
#         # 2. CHẶN NGAY NẾU GIỌNG CHƯA ĐƯỢC XÁC THỰC THÀNH CÔNG
#         if voice.status != "ready":
#             raise HTTPException(status_code=400, detail="Giọng mẫu chưa sẵn sàng hoặc đã bị từ chối do không khớp văn bản.")

#         # 3. Lưu log vào DB
#         db_audio = models.GeneratedAudio(
#             user_id=user.id,
#             voice_id=request.voice_id,
#             text=request.text,
#             status="queued"
#         )
#         db.add(db_audio)
#         db.commit()
#         db.refresh(db_audio)

#         # 4. Đóng gói Task Sinh âm thanh siêu tốc (Chỉ gửi text, codes và phones)
#         task_payload = {
#             "task_type": "generate_audio", # <--- DÁN NHÃN LOẠI TASK
#             "audio_id": db_audio.id,
#             "text": request.text,               
#             "ref_codes_path": voice.ref_codes_path, # S3 Key chứa file codes.pt
#             "ref_phones": voice.ref_phones          # Chuỗi âm vị đã lưu sẵn
#         }
        
#         if redis_client:
#             redis_client.rpush("voice_clone_tasks", json.dumps(task_payload))
#             print(f"🚀 Đã đẩy task GENERATE AUDIO siêu tốc cho Audio ID: {db_audio.id}")

#         return {
#             "status": 200,
#             "message": "Đã tiếp nhận yêu cầu sinh giọng siêu tốc",
#             "data": {
#                 "audio_id": db_audio.id,
#                 "status": db_audio.status
#             }
#         }

#     except HTTPException as he:
#         raise he
#     except Exception as e:
#         print("🔥 API GENERATE ERROR:", e)
#         raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")

@router.post("/generate")
def generate_cloned_audio(
    request: GenerateAudioRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        print("🔥 Tiếp nhận yêu cầu sinh giọng nói mới siêu tốc")
        
        user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

        # ==========================================
        # BƯỚC 1: LOGIC KIỂM TRA VÀ TRỪ TOKEN (KÝ TỰ)
        # ==========================================
        text_length = len(request.text.strip())
        
        if user.token_balance < text_length:
            raise HTTPException(
                status_code=402,
                detail=f"Số dư không đủ! Cần {text_length} ký tự, nhưng bạn chỉ còn {user.token_balance} ký tự."
            )
            
        user.token_balance -= text_length

        # ==========================================
        # BƯỚC 2: KIỂM TRA QUYỀN VÀ TRẠNG THÁI GIỌNG
        # ==========================================
        voice = db.query(models.VoiceProfile).filter(
            models.VoiceProfile.id == request.voice_id,
            or_(
                models.VoiceProfile.user_id == user.id,
                models.VoiceProfile.is_system_voice == True
            )
        ).first()
        
        if not voice:
            user.token_balance += text_length
            raise HTTPException(status_code=404, detail="Cấu hình giọng nói không tồn tại")
            
        if voice.status != "ready":
            user.token_balance += text_length
            raise HTTPException(status_code=400, detail="Giọng mẫu chưa sẵn sàng hoặc đã bị từ chối do không khớp văn bản.")

        # ==========================================
        # BƯỚC 3: LƯU DATABASE VÀ ĐẨY VÀO REDIS
        # ==========================================
        db_audio = models.GeneratedAudio(
            user_id=user.id,
            voice_id=request.voice_id,
            text=request.text,
            status="queued"
        )
        db.add(db_audio)
        db.commit()
        db.refresh(db_audio)

        task_payload = {
            "task_type": "generate_audio", 
            "audio_id": db_audio.id,
            "text": request.text,               
            "ref_codes_path": voice.ref_codes_path, 
            "ref_phones": voice.ref_phones          
        }
        
        if redis_client:
            redis_client.rpush("voice_clone_tasks", json.dumps(task_payload))
            print(f"🚀 Đã đẩy task GENERATE AUDIO cho Audio ID: {db_audio.id}")
            print(f"💰 User {user.username} đã bị trừ {text_length} ký tự. Số dư mới: {user.token_balance}")

        return {
            "status": 200,
            "message": "Đã tiếp nhận yêu cầu sinh giọng siêu tốc",
            "data": {
                "audio_id": db_audio.id,
                "status": db_audio.status,
                "remaining_tokens": user.token_balance
            }
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        print("🔥 API GENERATE ERROR:", e)
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")


@router.delete("/{voice_id}")
def delete_voice_profile(voice_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
    voice = db.query(models.VoiceProfile).filter(
        models.VoiceProfile.id == voice_id,
        models.VoiceProfile.user_id == user.id,
        models.VoiceProfile.is_system_voice == False
    ).first()

    if not voice:
        raise HTTPException(status_code=404, detail="Không tìm thấy giọng mẫu")

    # Xóa file trên S3
    if voice.ref_audio_path:
        try:
            s3_client.delete_object(Bucket=settings.AWS_BUCKET_NAME, Key=voice.ref_audio_path)
        except Exception as e:
            print(f"⚠️ Lỗi xóa S3: {e}")

    db.delete(voice)
    db.commit()
    return {"status": 200, "message": "Đã xóa giọng mẫu"}


@router.delete("/generated/{audio_id}")
def delete_generated_audio(
    audio_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

        audio = db.query(models.GeneratedAudio).filter(
            models.GeneratedAudio.id == audio_id,
            models.GeneratedAudio.user_id == user.id
        ).first()

        if not audio:
            raise HTTPException(
                status_code=404, 
                detail="Không tìm thấy bản thu hoặc bạn không có quyền xóa."
            )

        # XÓA FILE TRÊN CLOUD STORAGE (S3/MinIO)
        if audio.audio_path:
            try:
                # Gọi lệnh xóa Object trên Bucket
                s3_client.delete_object(
                    Bucket=settings.AWS_BUCKET_NAME, 
                    Key=audio.audio_path
                )
                print(f"🗑️ Đã xóa file trên Storage: {audio.audio_path}")
            except Exception as e:
                print(f"⚠️ Không thể xóa file trên Storage: {e}")

        # Xóa bản ghi trong DB
        db.delete(audio)
        db.commit()

        return {"status": 200, "message": "Đã xóa bản thu thành công"}

    except HTTPException as he:
        raise he
    except Exception as e:
        print("🔥 API DELETE GENERATED AUDIO ERROR:", e)
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")
    
