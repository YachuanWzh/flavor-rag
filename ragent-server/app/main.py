from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.auth import router as auth_router

app = FastAPI(title="flavor-rag API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
