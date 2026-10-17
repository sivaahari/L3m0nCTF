FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

WORKDIR /app

RUN groupadd -g 10002 router && useradd -u 10002 -g router -m -s /bin/sh router

COPY --chown=root:root services/instancer /app/services/instancer

RUN chmod -R 755 /app

USER 10002

EXPOSE 8090 30000-30200

ENTRYPOINT ["python", "-m", "services.instancer.router.main"]
