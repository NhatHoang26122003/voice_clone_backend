import os
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
import json
import redis
from sqlalchemy.orm import Session
from sqlalchemy import or_
from db.database import get_db
from db import models
from api.auth import get_current_user
from schemas.voice import GenerateAudioRequest

router = APIRouter()

redis_client = None
try:
    # Khởi tạo tạm một client
    temp_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    # Bắn thử một lệnh ping để ép nó kết nối thật sự. Nếu Redis tắt, nó sẽ quăng lỗi ngay ở đây!
    temp_client.ping() 
    
    redis_client = temp_client
    print("✅ Đã kết nối Redis thành công!")
except Exception as e:
    print("⚠️ Cảnh báo: Chưa kết nối được Redis cục bộ (Máy chưa bật Redis). Hệ thống sẽ bỏ qua hàng đợi.")

UPLOAD_DIR = "./storage/voices"

@router.post("/upload")
async def upload_voice_profile(
    voice_name: str = Form(...),
    ref_text: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    # 1. Kiểm tra định dạng file âm thanh đầu vào
    if not file.filename.endswith(('.wav', '.mp3')):
        raise HTTPException(status_code=400, detail="Định dạng file không hỗ trợ. Vui lòng gửi file .wav hoặc .mp3")

    # 2. Giả định truy vấn ID người dùng từ DB qua email trong token
    user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

    # 3. Tạo cấu trúc thư mục lưu trữ cục bộ: storage/voices/{user_id}/
    user_storage_path = os.path.join(UPLOAD_DIR, str(user.id))
    os.makedirs(user_storage_path, exist_ok=True)

    # Đặt tên file tránh trùng lặp bằng cách đính kèm tên giọng nói
    safe_filename = f"{voice_name}_{file.filename}"
    full_file_path = os.path.join(user_storage_path, safe_filename)

    # 4. Ghi luồng dữ liệu file xuống ổ cứng
    try:
        with open(full_file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi trong quá trình ghi file: {str(e)}")

    # 5. Khởi tạo bản ghi Metadata vào Database
    db_voice = models.VoiceProfile(
        user_id=user.id,
        voice_name=voice_name,
        ref_audio_path=full_file_path,
        ref_text=ref_text,
        status="ready" # Hiện tại để sẵn sàng, sau này sẽ chuyển thành 'processing' để đợi Worker AI trích xuất Embedding
    )
    db.add(db_voice)
    db.commit()
    db.refresh(db_voice)

    return {
        "message": "Tạo cấu hình giọng nói thành công",
        "id": db_voice.id,
        "voice_name": db_voice.voice_name,
        "ref_audio_path": db_voice.ref_audio_path,
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
                audio_url = v.ref_audio_path.replace("./storage", "/storage").replace("\\", "/")

            result.append({
                "id": v.id,
                "voice_name": v.voice_name,
                "ref_audio_path": audio_url,
                "ref_text": v.ref_text,
                "status": v.status,
                "is_system_voice": v.is_system_voice, # BẮT BUỘC TRẢ VỀ TRƯỜNG NÀY
                "created_at": v.created_at.isoformat() if v.created_at else None
            })

        return {"data": result, "total": len(result)}
    except Exception as e:
        print("🔥 API ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate")
def generate_cloned_audio(
    request: GenerateAudioRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        print("🔥 Tiếp nhận yêu cầu sinh giọng nói mới từ Flutter")
        
        # 1. Xác thực người dùng thông qua Token gửi lên
        user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

        # 2. Kiểm tra tính hợp lệ của cấu hình giọng nói (voice_id)
        voice = db.query(models.VoiceProfile).filter(
            models.VoiceProfile.id == request.voice_id,
            models.VoiceProfile.user_id == user.id
        ).first()
        
        if not voice:
            raise HTTPException(
                status_code=404, 
                detail="Cấu hình giọng nói không tồn tại hoặc không thuộc quyền sở hữu của bạn"
            )

        # 3. Tạo bản ghi log trạng thái vào bảng generated_audios (Trạng thái: 'queued')
        db_audio = models.GeneratedAudio(
            user_id=user.id,
            voice_id=request.voice_id,
            text=request.text,
            status="queued"
        )
        db.add(db_audio)
        db.commit()
        db.refresh(db_audio)

        # 4. Đóng gói thông tin cốt lõi gửi vào hàng đợi Redis để AI Worker bốc ra làm việc
        # Gói đầy đủ ref_audio_path và ref_text từ bảng VoiceProfile để AI Worker đỡ phải query lại DB
        task_payload = {
            "audio_id": db_audio.id,
            "user_id": user.id,
            "voice_id": voice.id,
            "text": request.text,               
            "ref_audio_path": voice.ref_audio_path, 
            "ref_text": voice.ref_text          
        }
        
        # Đẩy gói tin xuống hàng đợi có tên là "voice_clone_tasks" bằng lệnh rpush (Right Push)
        if redis_client:
            redis_client.rpush("voice_clone_tasks", json.dumps(task_payload))
            print(f"🚀 Đã đẩy mã đơn hàng {db_audio.id} vào hàng đợi Redis 'voice_clone_tasks'")
        else:
            print("⚠️ Cảnh báo: Redis chưa được bật, đơn hàng chỉ mới lưu tạm ở DB và chưa được gửi đi xử lý")

        # 5. Trả kết quả về ngay lập tức cho Mobile kèm theo ID của đơn hàng
        return {
            "status": 200,
            "message": "Đã tiếp nhận yêu cầu, hệ thống đang xử lý ngầm",
            "data": {
                "audio_id": db_audio.id,
                "status": db_audio.status
            }
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        print("🔥 API GENERATE ERROR:", e)
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống nội bộ: {str(e)}")
    
@router.get("/generated")
def get_generated_audios(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        # Lấy thông tin user hiện tại
        user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

        # Lấy toàn bộ lịch sử sinh audio của user này, sắp xếp mới nhất lên đầu
        audios = (
            db.query(models.GeneratedAudio)
            .filter(models.GeneratedAudio.user_id == user.id)
            .order_by(models.GeneratedAudio.created_at.desc())
            .all()
        )

        result = []
        for a in audios:
            # Xử lý đường dẫn nếu đã có file
            audio_url = None
            if a.audio_path:
                audio_url = a.audio_path.replace("./storage", "/storage").replace("\\", "/")

            result.append({
                "id": a.id,
                "text": a.text,
                "audio_path": audio_url,
                "status": a.status, # Cực kỳ quan trọng để Mobile biết vẽ vòng quay hay nút Play
                "created_at": a.created_at.strftime("%d/%m/%Y %H:%M") if a.created_at else None
            })

        return {"data": result}

    except Exception as e:
        print("🔥 API GET GENERATED ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{voice_id}")
def delete_voice_profile(
    voice_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        # 1. Xác thực người dùng
        user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

        # 2. Tìm giọng mẫu: Phải đúng ID, đúng user và KHÔNG phải giọng hệ thống
        voice = db.query(models.VoiceProfile).filter(
            models.VoiceProfile.id == voice_id,
            models.VoiceProfile.user_id == user.id,
            models.VoiceProfile.is_system_voice == False
        ).first()

        if not voice:
            raise HTTPException(
                status_code=404, 
                detail="Không tìm thấy giọng mẫu, hoặc bạn không có quyền xóa."
            )

        # 3. Xóa file vật lý trên ổ cứng Local
        if voice.ref_audio_path and os.path.exists(voice.ref_audio_path):
            try:
                os.remove(voice.ref_audio_path)
                print(f"🗑️ Đã xóa file vật lý: {voice.ref_audio_path}")
            except Exception as e:
                print(f"⚠️ Không thể xóa file vật lý: {e}")

        # 4. Xóa bản ghi trong Database
        db.delete(voice)
        db.commit()

        return {"status": 200, "message": "Đã xóa giọng mẫu và file audio thành công"}

    except HTTPException as he:
        raise he
    except Exception as e:
        print("🔥 API DELETE VOICE ERROR:", e)
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")
    

@router.delete("/generated/{audio_id}")
def delete_generated_audio(
    audio_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        # 1. Xác thực người dùng
        user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

        # 2. Tìm audio sinh ra
        audio = db.query(models.GeneratedAudio).filter(
            models.GeneratedAudio.id == audio_id,
            models.GeneratedAudio.user_id == user.id
        ).first()

        if not audio:
            raise HTTPException(
                status_code=404, 
                detail="Không tìm thấy bản thu hoặc bạn không có quyền xóa."
            )

        # 3. Xóa file kết quả trên ổ cứng Local
        if audio.audio_path:
            # Chuyển đổi từ dạng URL ("/storage/...") sang dạng vật lý ("./storage/...")
            physical_path = "." + audio.audio_path if audio.audio_path.startswith("/storage") else audio.audio_path
            
            if os.path.exists(physical_path):
                try:
                    os.remove(physical_path)
                    print(f"🗑️ Đã xóa file vật lý: {physical_path}")
                except Exception as e:
                    print(f"⚠️ Không thể xóa file vật lý: {e}")

        # 4. Xóa bản ghi trong DB
        db.delete(audio)
        db.commit()

        return {"status": 200, "message": "Đã xóa bản thu thành công"}

    except HTTPException as he:
        raise he
    except Exception as e:
        print("🔥 API DELETE GENERATED AUDIO ERROR:", e)
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")