from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import agent, auth

app = FastAPI(title="Forge — AI Software Engineering Team")

# Vite's default dev server port. Add your deployed frontend origin
# here too once this goes beyond local dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(agent.router)


@app.get("/health")
async def health():
    return {"status": "ok"}