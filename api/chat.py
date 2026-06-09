import httpx
import openai
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from db.database import get_db
from db import models
from api.auth import get_current_user
from core.config import settings
from core.prompts import CHATBOT_SYSTEM_PROMPT
from schemas.chat import SendMessageRequest

router = APIRouter()

custom_http_client = httpx.Client()
client = openai.OpenAI(
    api_key=settings.OPENAI_API_KEY,
    base_url=settings.OPENAI_BASE_URL,  
    http_client=custom_http_client
)

@router.get("/sessions")
def get_chat_sessions(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
    sessions = db.query(models.ChatSession).filter(models.ChatSession.user_id == user.id)\
                 .order_by(models.ChatSession.updated_at.desc()).all()
    return {"data": sessions}

@router.get("/sessions/{session_id}/messages")
def get_session_messages(session_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
    session = db.query(models.ChatSession).filter(
        models.ChatSession.id == session_id,
        models.ChatSession.user_id == user.id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Phiên chat không tồn tại")
    messages = db.query(models.ChatMessage).filter(models.ChatMessage.session_id == session_id).order_by(models.ChatMessage.created_at.asc()).all()
    return {"data": messages}

@router.post("/send")
def send_chat_message(request: SendMessageRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
    
    if user.account_type != 'pro':
        raise HTTPException(status_code=403, detail="Tài khoản Basic không được phép dùng API này. Vui lòng nâng cấp VIP.")

    session_id = request.session_id

    # 1. SINH TIÊU ĐỀ BẰNG LLM CHO PHIÊN MỚI
    if not session_id:
        try:
            title_res = client.chat.completions.create(
                model="cd/gpt-5.5",
                messages=[{"role": "user", "content": f"Viết một tiêu đề siêu ngắn (tối đa 5 từ) tóm tắt yêu cầu sau. Không dùng ngoặc kép: {request.message}"}],
                max_tokens=15
            )
            title = title_res.choices[0].message.content.strip()
        except Exception:
            title = request.message[:30] + "..."

        new_session = models.ChatSession(user_id=user.id, title=title)
        db.add(new_session)
        db.commit()
        db.refresh(new_session)
        session_id = new_session.id
    else:
        session = db.query(models.ChatSession).filter(models.ChatSession.id == session_id).first()
        if session:
            session.updated_at = db.func.now()
            db.commit()

    # Lưu tin nhắn người dùng
    user_msg = models.ChatMessage(session_id=session_id, is_user=True, content=request.message)
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    # 2 & 3. THÊM LỊCH SỬ VÀ TỐI ƯU PROMPT THUẦN VĂN BẢN + ĐÁNH GIÁ QUERY
    history_msgs = db.query(models.ChatMessage).filter(models.ChatMessage.session_id == session_id)\
                    .order_by(models.ChatMessage.created_at.asc()).limit(10).all()

    messages_payload = [{"role": "system", "content": CHATBOT_SYSTEM_PROMPT}]
    for msg in history_msgs:
        messages_payload.append({"role": "user" if msg.is_user else "assistant", "content": msg.content})

    # 4. GỌI LLM XỬ LÝ
    try:
        response = client.chat.completions.create(
            model="cd/gpt-5.5",
            messages=messages_payload,
            temperature=0.7
        )
        raw_ai_content = response.choices[0].message.content.strip()
        
        # Lọc cờ CLARIFY nếu LLM đánh giá thiếu ngữ cảnh
        if raw_ai_content.startswith("CLARIFY:"):
            ai_response_content = raw_ai_content.replace("CLARIFY:", "").strip()
        else:
            ai_response_content = raw_ai_content

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi cổng kết nối LLM: {str(e)}")

    ai_msg = models.ChatMessage(session_id=session_id, is_user=False, content=ai_response_content)
    db.add(ai_msg)
    db.commit()
    db.refresh(ai_msg)

    return {
        "session_id": session_id,
        "user_msg": {"id": user_msg.id, "content": user_msg.content, "is_user": True},
        "ai_msg": {"id": ai_msg.id, "content": ai_msg.content, "is_user": False}
    }