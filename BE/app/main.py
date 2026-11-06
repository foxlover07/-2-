import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import properties, recommend

load_dotenv()

# 쉼표로 구분해 여러 개 지정 가능. 기본값은 Vite 개발 서버.
CORS_ORIGINS = [
    o.strip()
    for o in (os.getenv("CORS_ORIGINS") or "http://localhost:5173").split(",")
    if o.strip()
]

app = FastAPI(title="Housing Recommendation API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(properties.router)
app.include_router(recommend.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
