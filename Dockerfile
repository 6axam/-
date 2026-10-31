FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/data/model-cache \
    TRANSFORMERS_CACHE=/app/data/model-cache

WORKDIR /app

COPY requirements-runtime.txt ./requirements-runtime.txt
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY app ./app
COPY prompts ./prompts

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin anya
USER 1000:1000

CMD ["python", "-m", "app.main"]
