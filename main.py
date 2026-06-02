from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from starlette.exceptions import HTTPException as StarletteHTTPException
from api import auth, voices, payments
from db.database import engine, Base

Base.metadata.create_all(bind=engine)
app = FastAPI(title="Voice clone App")

app.mount("/storage", StaticFiles(directory="storage"), name="storage")
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(voices.router, prefix="/api/voices", tags=["Voice Profiles"])
app.include_router(payments.router, prefix="/api/payments", tags=["Payments"])

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Thay vì trả về {"detail": "..."}, ta ép nó trả về {"message": "..."}
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail}
    )

@app.get("/")
def root():
    return {"Message": "Hệ thống Backend đang hoạt động"}