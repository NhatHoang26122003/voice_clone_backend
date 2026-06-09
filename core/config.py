import os
from dotenv import load_dotenv

# Tải các biến môi trường từ file .env vào hệ thống
load_dotenv()

class Settings:
    # 1. Database
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./voice_clone.db")
    
    # 2. Redis
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
    
    # 3. AWS S3 / MinIO
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "minioadmin")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "minioadminpassword")
    AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-1")
    AWS_BUCKET_NAME = os.getenv("AWS_BUCKET_NAME", "vietnamese-voice-clone")
    # Endpoint URL rất quan trọng. Khi chạy local nó trỏ về localhost:9000, khi lên AWS S3 thật thì phải xóa trống.
    AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL", "http://localhost:9000") 
    CLIENT_MINIO_URL = os.getenv("CLIENT_MINIO_URL", "http://e1.chiasegpu.vn:21163")
    
    # 4. Modal AI
    MODAL_TOKEN_ID = os.getenv("MODAL_TOKEN_ID")
    MODAL_TOKEN_SECRET = os.getenv("MODAL_TOKEN_SECRET")
    
    # 5. Auth
    SECRET_KEY = os.getenv("SECRET_KEY", "fallback_secret_key_123")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

    # 6. Stripe
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

    # 7. Server Domain
    SERVER_DOMAIN = os.getenv("SERVER_DOMAIN")

    # 8. OPENAI API KEY
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    
# Khởi tạo một đối tượng duy nhất để import ở các file khác
settings = Settings()