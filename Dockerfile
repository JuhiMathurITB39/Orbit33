# API image: Python backend + prebuilt frontend. Authored; not built in the sandbox (no Docker daemon).
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd -m orbit
WORKDIR /app
COPY pyproject.toml ./
COPY orbit ./orbit
RUN pip install .
COPY --from=web /web/dist ./frontend/dist
USER orbit
ENV ORBIT_DATA_DIR=/data ORBIT_HOST=0.0.0.0 ORBIT_PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s CMD python -c "import urllib.request as u;u.urlopen('http://localhost:8000/api/v1/health')"
CMD ["python", "-m", "orbit.api.app"]
