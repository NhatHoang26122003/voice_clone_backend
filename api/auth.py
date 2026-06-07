from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from fastapi import Depends, HTTPException, status
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from datetime import datetime, timedelta

# Import db và models
from db.database import get_db
from db import models
from schemas.user import UserRegister
from core.security import get_password_hash, verify_password, create_access_token, SECRET_KEY, ALGORITHM

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# ĐÃ XÓA fake_users_db

@router.post("/register")
def register(user: UserRegister, db: Session = Depends(get_db)):
    # 1. Kiểm tra xem email có trong SQLite chưa
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email đã tồn tại")
    
    # 2. Lưu user mới vào SQLite
    hashed_password = get_password_hash(user.password)
    new_user = models.User(
        username=user.username,
        email=user.email,
        password_hash=hashed_password
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "Đăng ký thành công", "username": new_user.username}

@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user_email = form_data.username 
    
    # 3. Tìm user trong SQLite
    db_user = db.query(models.User).filter(models.User.email == user_email).first()
    
    if not db_user or not verify_password(form_data.password, db_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai email hoặc mật khẩu",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(data={"sub": user_email})
    return {"access_token": access_token, "token_type": "bearer"}

def get_current_user(token: str = Depends(oauth2_scheme)):

    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        email = payload.get("sub")

        if email is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid token"
            )

        return {"email": email}

    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")

    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

@router.get("/me")
def get_current_user_info(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User không tồn tại")
        
    if user.account_type == 'pro' and user.pro_expires_at:
        if datetime.utcnow() > user.pro_expires_at:
            user.account_type = 'basic' 
            db.commit()
            
    expires_str = user.pro_expires_at.strftime("%d/%m/%Y") if user.pro_expires_at else None
        
    return {
        "status": 200,
        "data": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "token_balance": user.token_balance,
            "account_type": user.account_type,
            "pro_expires_at": expires_str
        }
    }