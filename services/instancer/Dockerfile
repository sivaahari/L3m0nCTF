FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

WORKDIR /app

RUN groupadd -g 10001 instancer && useradd -u 10001 -g instancer -m -s /bin/sh instancer

COPY --chown=root:root services/instancer /app/services/instancer
COPY --chown=root:root config/instancer /config

RUN chmod -R 755 /app /config

USER 10001

EXPOSE 8300 8301

ENTRYPOINT ["python", "-m", "services.instancer.main"]
