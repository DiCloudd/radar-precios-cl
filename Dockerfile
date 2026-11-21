# Imagen oficial de Playwright (Ubuntu 24.04, multi-arquitectura: funciona en x86 y en ARM de Oracle).
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Santiago \
    TZ_NAME=America/Santiago \
    DATA_DIR=/app/data

RUN apt-get update && apt-get install -y --no-install-recommends git tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python godzilla.py && useradd -m monitor && mkdir -p /app/data && chown -R monitor /app
USER monitor

# Si el proceso deja de escribir su log por 15 min, Docker lo marca unhealthy y lo reinicia el compose.
HEALTHCHECK --interval=5m --timeout=10s --start-period=10m \
  CMD python -c "import os,time,sys; p='/app/data/monitor.log'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<900 else 1)"

CMD ["python", "main.py", "--loop"]
