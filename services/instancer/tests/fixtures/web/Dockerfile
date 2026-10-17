FROM python:3.11-slim

# Create non-root user (uid 10001) as required by instancer spec
RUN groupadd -g 10001 chall && useradd -u 10001 -g chall -m -s /bin/sh chall

WORKDIR /app
COPY server.py /app/server.py

USER 10001:10001
EXPOSE 8080

CMD ["python3", "-u", "/app/server.py"]
