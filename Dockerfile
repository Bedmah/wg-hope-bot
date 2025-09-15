FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    wireguard-tools iproute2 qrencode ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/wg-bot

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src
COPY run.sh ./run.sh
RUN chmod +x run.sh

VOLUME ["/opt/wg-bot/clients"]

CMD ["./run.sh"]
