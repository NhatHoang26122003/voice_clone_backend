import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from db.database import get_db
from db import models
from api.auth import get_current_user
from core.config import settings
from datetime import datetime, timedelta
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
    server_domain = settings.SERVER_DOMAIN or "http://localhost:8000"

    try:
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
                "tokens_to_add": str(package["tokens"]),
                "package_id": request.package_id 
            },
            success_url=f"{server_domain}/api/payments/success",
            cancel_url=f"{server_domain}/api/payments/cancel",
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
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid webhook")

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        metadata = getattr(session, 'metadata', None)

        if metadata:
            # 2. LẤY THÔNG TIN USER (Dùng getattr để chống lỗi)
            user_id = getattr(metadata, 'user_id', None)
            tokens_to_add = getattr(metadata, 'tokens_to_add', None)
            package_id = getattr(metadata, 'package_id', None)

            # (Fallback an toàn phòng khi Stripe ngầm ép metadata về kiểu Dict)
            if not user_id and hasattr(metadata, 'get'):
                user_id = metadata.get('user_id')
                tokens_to_add = metadata.get('tokens_to_add')
                package_id = metadata.get('package_id')
            
            if user_id and tokens_to_add:
                user = db.query(models.User).filter(models.User.id == int(user_id)).first()
                if user:
                    # 1. Luôn cộng dồn số dư Token (Ký tự) cho mọi gói nạp
                    user.token_balance += int(tokens_to_add)
                    
                    # 2. XỬ LÝ LOGIC THỜI HẠN VÀ GIỮ HẠNG VIP
                    now = datetime.utcnow() 
                    
                    if package_id == 'pro':
                        user.account_type = 'pro'
                        
                        if user.pro_expires_at and user.pro_expires_at > now:
                            user.pro_expires_at = user.pro_expires_at + timedelta(days=30)
                        else:
                            user.pro_expires_at = now + timedelta(days=30)
                            
                    elif package_id == 'basic':
                        if getattr(user, 'account_type', 'basic') == 'pro':
                            if not user.pro_expires_at or user.pro_expires_at < now:
                                user.account_type = 'basic'
                        else:
                            user.account_type = 'basic'
                    
                    db.commit()
                    print(f"💰 WEBHOOK SUCCESS: User {user.username} nạp {tokens_to_add} ký tự. Hạng: {user.account_type}, Hạn VIP: {user.pro_expires_at}")                    
    return {"status": "success"}


# ==========================================
# GIAO DIỆN HTML KHI THANH TOÁN XONG
# ==========================================
# @router.get("/success", response_class=HTMLResponse)
# def payment_success():
#     return """
#     <html>
#         <head>
#             <meta name="viewport" content="width=device-width, initial-scale=1">
#             <style>
#                 body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; padding: 50px 20px; background-color: #f8f9fa; }
#                 .card { background: white; padding: 40px 20px; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); max-width: 400px; margin: auto; }
#                 .checkmark { font-size: 80px; color: #4CAF50; margin-bottom: 20px; }
#                 h2 { color: #2c3e50; margin-bottom: 10px; }
#                 p { color: #7f8c8d; line-height: 1.6; margin-bottom: 30px; }
#                 .btn { display: inline-block; padding: 12px 30px; background-color: #FF9800; color: white; text-decoration: none; border-radius: 30px; font-weight: bold; }
#             </style>
#         </head>
#         <body>
#             <div class="card">
#                 <div class="checkmark">✅</div>
#                 <h2>Thanh toán thành công!</h2>
#                 <p>Cảm ơn bạn đã tin tưởng. Ký tự đã được cộng vào tài khoản của bạn trên hệ thống.</p>
#                 <a href="#" class="btn" onclick="window.close();">Quay lại Ứng dụng</a>
#                 <p style="font-size: 12px; margin-top: 15px;">(Bạn có thể đóng trình duyệt này an toàn)</p>
#             </div>
#         </body>
#     </html>
#     """


# @router.get("/cancel", response_class=HTMLResponse)
# def payment_cancel():
#     return """
#     <html>
#         <head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
#         <body style="text-align:center; padding:50px; font-family:sans-serif; background-color:#f8f9fa;">
#             <div style="font-size:80px; color:#F44336;">❌</div>
#             <h2 style="color:#333;">Thanh toán đã bị hủy</h2>
#             <p style="color:#666;">Bạn chưa bị trừ tiền. Vui lòng đóng trang này để quay lại ứng dụng.</p>
#         </body>
#     </html>
#     """


# ==========================================
# GIAO DIỆN HTML KHI THANH TOÁN XONG (ĐÃ TỐI ƯU UI)
# ==========================================
@router.get("/success", response_class=HTMLResponse)
def payment_success():
    return """
    <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { font-family: '-apple-system', BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; text-align: center; padding: 60px 20px; background-color: #fcfcfd; margin: 0; }
                .card { background: white; padding: 45px 30px; border-radius: 24px; box-shadow: 0 15px 35px rgba(0,0,0,0.05); max-width: 360px; margin: auto; border: 1px solid #f1f1f5; }
                .icon-box { width: 80px; height: 80px; background: #e8f5e9; color: #2e7d32; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 24px auto; font-size: 40px; font-weight: bold; }
                h2 { color: #1c1c1e; font-size: 24px; font-weight: 700; margin: 0 0 12px 0; }
                p { color: #636366; font-size: 15px; line-height: 1.6; margin: 0 0 32px 0; padding: 0 10px; }
                .btn { display: block; padding: 16px 20px; background: linear-gradient(135deg, #FFD54F 0%, #FF9800 100%); color: white; text-decoration: none; border-radius: 16px; font-weight: bold; font-size: 16px; box-shadow: 0 6px 20px rgba(255, 152, 0, 0.3); transition: transform 0.2s; }
                .btn:active { transform: scale(0.98); }
            </style>
        </head>
        <body>
            <div class="card">
                <div class="icon-box">✓</div>
                <h2>Thanh toán thành công</h2>
                <p>Cảm ơn bạn! Hệ thống đã ghi nhận giao dịch và tự động cộng thêm ký tự vào tài khoản của bạn.</p>
                <a href="app://payment/success" class="btn">Quay lại ứng dụng</a>
            </div>
        </body>
    </html>
    """

@router.get("/cancel", response_class=HTMLResponse)
def payment_cancel():
    return """
    <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { font-family: '-apple-system', sans-serif; text-align: center; padding: 60px 20px; background-color: #fcfcfd; }
                .card { background: white; padding: 45px 30px; border-radius: 24px; box-shadow: 0 15px 35px rgba(0,0,0,0.05); max-width: 360px; margin: auto; }
                .icon-box { width: 80px; height: 80px; background: #ffebee; color: #c62828; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 24px auto; font-size: 40px; }
                h2 { color: #1c1c1e; font-size: 24px; margin-bottom: 12px; }
                p { color: #636366; font-size: 15px; margin-bottom: 32px; }
                .btn-cancel { display: block; padding: 16px 20px; background: #8e8e93; color: white; text-decoration: none; border-radius: 16px; font-weight: bold; }
            </style>
        </head>
        <body>
            <div class="card">
                <div class="icon-box">✕</div>
                <h2>Giao dịch đã hủy</h2>
                <p>Bạn chưa bị trừ tiền trong tài khoản. Vui lòng nhấn nút bên dưới để quay về.</p>
                <a href="app://payment/cancel" class="btn-cancel">Quay lại trang nạp</a>
            </div>
        </body>
    </html>
    """
