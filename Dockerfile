# One image: React build served by FastAPI.   docker compose up --build   →   http://localhost:8000
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY data_generator data_generator
COPY backend backend
COPY --from=web /web/dist frontend/dist
COPY docker/entrypoint.sh /entrypoint.sh
ENV COFORCE_DB=/app/data/coforce.db PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["sh", "/entrypoint.sh"]
