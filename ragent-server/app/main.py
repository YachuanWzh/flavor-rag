from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.cors import CORSMiddleware
from app.api.auth import router as auth_router
from app.api.knowledge import router as knowledge_router
from app.api.search import router as search_router
from app.api.conversation import router as conversation_router
from app.api.chat import router as chat_router
from app.api.admin import router as admin_router

app = FastAPI(title="flavor-rag API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(knowledge_router)
app.include_router(search_router)
app.include_router(conversation_router)
app.include_router(chat_router)
app.include_router(admin_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
