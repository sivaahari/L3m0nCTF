FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

# Create non-root user (uid 10001) as required by instancer spec
RUN groupadd -g 10001 chall && useradd -u 10001 -g chall -m -s /bin/sh chall

WORKDIR /app
COPY server.py /app/server.py

USER 10001:10001
EXPOSE 8080

CMD ["python3", "-u", "/app/server.py"]
