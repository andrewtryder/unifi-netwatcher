FROM node:20-slim AS assets
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY tailwind.config.js postcss.config.js ./
COPY scripts ./scripts/
COPY app/web/static/tailwind.input.css ./app/web/static/tailwind.input.css
COPY app/web/templates ./app/web/templates
RUN npm run build:css

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=assets /app/app/web/static/app.css ./app/web/static/app.css
COPY --from=assets /app/app/web/static/fonts ./app/web/static/fonts

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
