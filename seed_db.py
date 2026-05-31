from db.database import SessionLocal
from db import models

def create_system_voice():
    db = SessionLocal()
    try:
        # Kiểm tra xem đã có giọng hệ thống nào chưa để tránh thêm trùng lặp
        existing_voice = db.query(models.VoiceProfile).filter(models.VoiceProfile.is_system_voice == True).first()
        
        if existing_voice:
            print("Giọng hệ thống đã tồn tại. Không cần thêm nữa.")
            return

        # Tạo Giọng Hệ Thống
        system_voice = models.VoiceProfile(
            user_id=0, # ID = 0 đại diện cho hệ thống
            voice_name="Giọng Nam Chuẩn",
            ref_audio_path="./storage/voices/system/giong_nam_chuan.wav", # Nhớ tạo thư mục và bỏ file này vào nhé
            ref_text="Xin chào, đây là giọng nói mặc định của hệ thống.",
            status="ready",
            is_system_voice=True
        )
        
        db.add(system_voice)
        db.commit()
        print("🎉 Đã thêm Giọng Hệ Thống vào Database thành công!")
        
    except Exception as e:
        print("Lỗi:", e)
    finally:
        db.close()

if __name__ == "__main__":
    create_system_voice()