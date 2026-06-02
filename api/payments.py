import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel

from db.database import get_db
from db import models
from api.auth import get_current_user
from core.config import settings

router = APIRouter()

# Cấu hình API Key cho Stripe
stripe.api_key = settings.STRIPE_SECRET_KEY

# Định nghĩa các gói nạp
PACKAGES = {
    "basic": {"price_usd": 2, "tokens": 50000, "name": "Gói Cơ Bản (50.000 ký tự)"},
    "pro": {"price_usd": 5, "tokens": 150000, "name": "Gói Nâng Cao (150.000 ký tự)"}
}

class CheckoutRequest(BaseModel):
    package_id: str  # 'basic' hoặc 'pro'

@router.post("/create-checkout-session")
def create_checkout_session(
    request: CheckoutRequest, 
    db: Session = Depends(get_db), 
    current_user: dict = Depends(get_current_user)
):
    """Mobile gọi API này để lấy URL thanh toán của Stripe"""
    user = db.query(models.User).filter(models.User.email == current_user["email"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="Người dùng không tồn tại")

    if request.package_id not in PACKAGES:
        raise HTTPException(status_code=400, detail="Gói nạp không hợp lệ")

    package = PACKAGES[request.package_id]

    try:
        # Tạo phiên thanh toán trên Stripe
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': package["name"],
                        'description': f"Nạp {package['tokens']} ký tự vào tài khoản {user.email}",
                    },
                    'unit_amount': package["price_usd"] * 100, 
                },
                'quantity': 1,
            }],
            mode='payment',
            metadata={
                "user_id": str(user.id),
                "tokens_to_add": str(package["tokens"])
            },
            success_url="http://localhost:8000/docs",
            cancel_url="http://localhost:8000/docs",
        )
        
        return {"checkout_url": checkout_session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Stripe sẽ gọi API này để báo cáo kết quả thanh toán"""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        # Xác minh chữ ký: Đảm bảo request này thực sự đến từ Stripe
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(status_code=400, detail="Invalid signature")

    print(f"🔔 STRIPE GỬI TỚI SỰ KIỆN: {event['type']}")
    
    # Xử lý sự kiện "Thanh toán thành công"
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        
        # 1. LẤY METADATA (Dùng getattr thay vì .get)
        metadata = getattr(session, 'metadata', None)

        if metadata:
            # 2. LẤY THÔNG TIN USER (Dùng getattr để chống lỗi)
            user_id = getattr(metadata, 'user_id', None)
            tokens_to_add = getattr(metadata, 'tokens_to_add', None)
            
            # (Fallback an toàn phòng khi Stripe ngầm ép metadata về kiểu Dict)
            if not user_id and hasattr(metadata, 'get'):
                user_id = metadata.get('user_id')
                tokens_to_add = metadata.get('tokens_to_add')

            if user_id and tokens_to_add:
                # 3. TÌM USER VÀ CỘNG TIỀN
                user = db.query(models.User).filter(models.User.id == int(user_id)).first()
                if user:
                    user.token_balance += int(tokens_to_add)
                    db.commit()
                    print(f"💰 WEBHOOK: Đã cộng {tokens_to_add} token cho User ID {user_id}. Số dư mới: {user.token_balance}")
    return {"status": "success"}