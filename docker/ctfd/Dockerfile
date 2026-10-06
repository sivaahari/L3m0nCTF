# syntax=docker/dockerfile:1.7
# The L3m0nCTF platform image: the official CTFd 3.8.8 image (pinned by digest), with upgraded dependencies,
# our plugins and our theme. No CTFd source is edited. Build from the repository root:
#   docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .
FROM ghcr.io/ctfd/ctfd@sha256:07cc9788d58b2a18ea4dc2103ff385c053ffc506f57d0c769a99387849aedd01

USER root

# Operating-system fixes the upstream image does not have yet (Trivy, 2026-10-06): libpcre2 and perl-base.
# Only these two are upgraded; nothing else is installed, and the package lists are removed again. The versions are not
# pinned on purpose: the point is to take whatever fixed version Debian's security archive has (verify-image.sh checks
# the minimum).
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade libpcre2-8-0 perl-base \
    && rm -rf /var/lib/apt/lists/*

COPY docker/ctfd/requirements.overrides.txt /tmp/overrides.txt
# The base interpreter keeps its own copy of setuptools (not used by CTFd, which runs from /opt/venv); it gets the same
# fixed version so a scanner finds the bundled wheel and jaraco.context code fixed everywhere.
# Last, pip is removed from both interpreters: nothing uses it at run time, a package manager has no place in a
# production image, and its bundled libraries (msgpack, urllib3, pkg_resources) only ever trigger scanner findings
# that cannot be fixed until pip itself ships a new release. Tests that need pip use `python -m ensurepip`.
RUN /opt/venv/bin/pip install --no-cache-dir --upgrade -r /tmp/overrides.txt \
    && /opt/venv/bin/pip check \
    && /usr/local/bin/python -m pip install --no-cache-dir --upgrade "setuptools==$(sed -n 's/^setuptools==//p' /tmp/overrides.txt)" \
    && rm -f /tmp/overrides.txt \
    && /opt/venv/bin/python -m pip uninstall -y pip \
    && /usr/local/bin/python -m pip uninstall -y pip

# Our plugins and theme. Owned by root and read-only for the CTFd user (1001), so a bug in the web process can never
# change the code it runs. The bytecode is compiled here because the process cannot write it later.
COPY --chown=root:root plugins/ /opt/CTFd/CTFd/plugins/
COPY --chown=root:root ui/theme/l3mon/ /opt/CTFd/CTFd/themes/l3mon/
RUN find /opt/CTFd/CTFd/plugins/l3mon_* /opt/CTFd/CTFd/themes/l3mon -type d -exec chmod 755 {} + \
    && find /opt/CTFd/CTFd/plugins/l3mon_* /opt/CTFd/CTFd/themes/l3mon -type f -exec chmod 644 {} + \
    && /opt/venv/bin/python -m compileall -q /opt/CTFd/CTFd/plugins/l3mon_*

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
