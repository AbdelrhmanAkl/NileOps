FROM python:3.13-slim

# Tesseract (Arabic + English) and Poppler for OCR of scanned documents.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts
COPY knowledge ./knowledge
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh

RUN useradd --create-home --uid 10001 nileops \
    && mkdir -p /app/data /app/storage && chown -R nileops /app
USER nileops
EXPOSE 8000
ENTRYPOINT ["sh", "/app/deploy/entrypoint.sh"]
