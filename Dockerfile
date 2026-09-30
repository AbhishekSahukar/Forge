# Forge — single combined image: builds the React frontend, then runs all 4
# backend processes (main-api + 3 agent servers) in one container, with
# main-api serving the built frontend directly. One Render service, one
# port, no CORS between frontend and backend since they're now the same
# origin.
#
# Build context must be the REPO ROOT (the folder containing both
# backend/ and frontend/), not backend/ alone — this Dockerfile copies
# from both.

# ---- Stage 1: build the React frontend ----
FROM node:20-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./
# VITE_API_URL="" bakes in a relative API base at build time, so the
# built app calls /agent/..., /auth/..., /github/... on its own origin
# instead of a separate backend domain — this only works because the
# backend is about to serve these exact same static files itself.
ENV VITE_API_URL=""
RUN npm run build
# Output: /frontend/dist/ (index.html, assets/*.js, assets/*.css, ...)

# ---- Stage 2: the Python backend, serving the built frontend too ----
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app/ ./app/
COPY backend/run_all.py .

# The built frontend lands at /app/static — main.py's StaticFiles mount
# and root route both expect it exactly here.
COPY --from=frontend-build /frontend/dist/ ./static/

EXPOSE 8000

# No .env copied into the image — GITHUB_PAT, OPENROUTER_API_KEY, and
# JWT_SECRET must be set as Render environment variables instead.

CMD ["python", "run_all.py"]