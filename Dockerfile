# syntax=docker/dockerfile:1.7

FROM node:24-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_API_BASE_URL=/api
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend \
    SMP_DEPLOYMENT_MODE=anonymous \
    SMP_SERVE_FRONTEND=true \
    SMP_FRONTEND_DIST_DIR=/app/frontend/dist \
    SMP_DATA_DIR=/var/lib/saville \
    SMP_SESSION_COOKIE_SECURE=true \
    SMP_SESSION_COOKIE_SAMESITE=lax \
    WEB_CONCURRENCY=1 \
    PORT=8000

WORKDIR /app
COPY backend/requirements-prod.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip && python -m pip install -r /tmp/requirements.txt

COPY backend/app/ ./backend/app/
COPY scripts/start_hosted.py ./scripts/start_hosted.py
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

RUN addgroup --system --gid 10001 saville \
    && adduser --system --uid 10001 --ingroup saville --home /app saville \
    && mkdir -p /var/lib/saville \
    && chown -R saville:saville /var/lib/saville /app

USER saville
EXPOSE 8000
VOLUME ["/var/lib/saville"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/api/ready', timeout=4).read()"

CMD ["python", "scripts/start_hosted.py"]
