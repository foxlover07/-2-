import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.deploy_bundle import unpack_if_configured

# 배포: 암호화한 데이터 묶음을 라우터(데이터 경로를 읽는 모듈)보다 먼저 푼다. 키가 없으면 아무것도 안 함
unpack_if_configured()

from app.routers import agent, analysis, market, properties, recommend, timetable  # noqa: E402

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
app.include_router(analysis.router)
app.include_router(market.router)
app.include_router(agent.router)
app.include_router(timetable.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
