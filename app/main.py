import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routers import agent, auth, github

app = FastAPI(title="Forge — AI Software Engineering Team")

# Local dev origins always allowed. In the combined single-container
# deployment, the frontend and backend share one origin, so CORS
# generally isn't even exercised there — this stays mainly for local
# development, where `npm run dev` serves the frontend separately on
# :5173 while the backend runs on :8000.
_allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_frontend_url = os.environ.get("FRONTEND_URL")
if _frontend_url:
    _allowed_origins.append(_frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(agent.router)
app.include_router(github.router)


@app.get("/health")
async def health():
    return {"status": "ok"}



STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
_assets_dir = os.path.join(STATIC_DIR, "assets")

if os.path.isdir(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")


@app.get("/", include_in_schema=False)
async def serve_frontend_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(
            status_code=404,
            detail="Frontend build not found — this API instance wasn't built with the "
            "combined Dockerfile, or the frontend build step didn't run.",
        )
    return FileResponse(index_path)


@app.get("/favicon.svg", include_in_schema=False)
async def serve_favicon():
    favicon_path = os.path.join(STATIC_DIR, "favicon.svg")
    if os.path.isfile(favicon_path):
        return FileResponse(favicon_path)
    raise HTTPException(status_code=404)