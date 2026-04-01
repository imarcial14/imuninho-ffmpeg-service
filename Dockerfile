FROM alpine:3.22

RUN apk add --no-cache \
    ffmpeg \
    python3 \
    py3-pip \
    wget \
    curl \
    bash

RUN pip3 install --break-system-packages flask google-auth google-auth-httplib2 google-api-python-client requests

WORKDIR /app

COPY server.py .

EXPOSE 8080

CMD ["python3", "server.py"]
