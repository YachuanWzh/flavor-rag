from dotenv import load_dotenv
from pathlib import Path

# .env takes priority over system env vars for local dev
_ENV_FILE = str(Path(__file__).resolve().parent / ".env")
load_dotenv(_ENV_FILE, override=True)

import uvicorn
from app.config.settings import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.server_port,
        reload=False,
    )
