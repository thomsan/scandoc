FROM node:24-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SCANDOC_DATA_DIR=/app/data
WORKDIR /app
COPY requirements-web.lock ./
RUN pip install --no-cache-dir -r requirements-web.lock
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY --from=frontend /src/scandoc/web/static/ ./src/scandoc/web/static/
RUN pip install --no-cache-dir --no-deps . && useradd --uid 1000 --create-home scandoc && mkdir /app/data /app/output && chown scandoc:scandoc /app/data /app/output
USER 1000:1000
EXPOSE 8000
CMD ["scandoc", "serve", "--host", "0.0.0.0"]
