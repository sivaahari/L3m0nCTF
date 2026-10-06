# syntax=docker/dockerfile:1.7
# The L3m0nCTF platform image: the official CTFd 3.8.8 image (pinned by digest), with upgraded dependencies,
# our plugins and our theme. No CTFd source is edited. Build from the repository root:
#   docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .
FROM ghcr.io/ctfd/ctfd@sha256:07cc9788d58b2a18ea4dc2103ff385c053ffc506f57d0c769a99387849aedd01

USER root
COPY docker/ctfd/requirements.overrides.txt /tmp/overrides.txt
RUN /opt/venv/bin/pip install --no-cache-dir --upgrade -r /tmp/overrides.txt \
    && /opt/venv/bin/pip check \
    && rm -f /tmp/overrides.txt

# Our plugins and theme. Owned by root and read-only for the CTFd user (1001), so a bug in the web process can never
# change the code it runs. The bytecode is compiled here because the process cannot write it later.
COPY --chown=root:root plugins/ /opt/CTFd/CTFd/plugins/
COPY --chown=root:root ui/theme/l3mon/ /opt/CTFd/CTFd/themes/l3mon/
RUN find /opt/CTFd/CTFd/plugins/l3mon_core /opt/CTFd/CTFd/themes/l3mon -type d -exec chmod 755 {} + \
    && find /opt/CTFd/CTFd/plugins/l3mon_core /opt/CTFd/CTFd/themes/l3mon -type f -exec chmod 644 {} + \
    && /opt/venv/bin/python -m compileall -q /opt/CTFd/CTFd/plugins/l3mon_core

# Reads secrets from files before starting CTFd, and the container health check.
COPY --chown=root:root --chmod=644 docker/ctfd/l3mon-env.sh /usr/local/lib/l3mon-env.sh
COPY --chown=root:root --chmod=755 docker/ctfd/l3mon-entrypoint.sh /usr/local/bin/l3mon-entrypoint
COPY --chown=root:root --chmod=755 docker/ctfd/l3mon-run.sh /usr/local/bin/l3mon-run
COPY --chown=root:root --chmod=644 docker/ctfd/healthcheck.py /usr/local/lib/l3mon-healthcheck.py
ENTRYPOINT ["/usr/local/bin/l3mon-entrypoint"]
ENV PYTHONDONTWRITEBYTECODE=1

USER 1001
HEALTHCHECK --interval=15s --timeout=6s --start-period=90s --retries=5 \
  CMD ["/opt/venv/bin/python", "/usr/local/lib/l3mon-healthcheck.py"]
