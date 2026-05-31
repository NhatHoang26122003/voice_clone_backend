import sqlite3

# Kết nối trực tiếp vào file database của em
conn = sqlite3.connect('voice_clone.db')
cursor = conn.cursor()

try:
    # Chạy lệnh SQL thuần để thêm cột is_system_voice, mặc định là 0 (False)
    # cursor.execute("ALTER TABLE voice_profiles ADD COLUMN is_system_voice BOOLEAN DEFAULT 0;")
    cursor.execute("DELETE FROM generated_audios WHERE status = 'failed';")
    conn.commit()
    print("✅ Đã xóa thành công!")
except Exception as e:
    print("⚠️ Lỗi:", e)
finally:
    conn.close()