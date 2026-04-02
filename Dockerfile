FROM alpine:3.22

RUN apk add --no-cache \
    ffmpeg \
    python3 \
    py3-pip \
    wget \
    curl \
    bash \
    gcc \
    musl-dev \
    python3-dev \
    libffi-dev \
    openssl-dev \
    cargo \
    ttf-dejavu \
    fontconfig

RUN fc-cache -f

RUN pip3 install --break-system-packages \
    flask \
    requests \
    cryptography

WORKDIR /app
COPY server.py .
EXPOSE 8080
CMD ["python3", "server.py"]
