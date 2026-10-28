# v0.9.1: guided rootless Podman developer tooling image.
# Build-time apt/npm/git downloads use the explicitly forwarded native khroxy
# HTTP proxy via rootless pasta TCP port forwarding; runtime rules stay separate.
FROM node:24-trixie-slim
ARG AGENT_UID=1000
ARG CODEX_VERSION=0.164.0
ARG CAVEMAN_REF=v4.0.1
ARG CAVEMAN_SUITE=none
RUN set -eux; \
    case "$AGENT_UID" in *[!0-9]*|'') exit 1;; esac; \
    test "$AGENT_UID" -ge 1000; test "$AGENT_UID" -ne 12000; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3 curl ca-certificates \
      iptables iproute2 gosu tini git procps bash util-linux less; \
    rm -rf /var/lib/apt/lists/*; \
    userdel -r node 2>/dev/null || true; \
    groupdel node 2>/dev/null || true; \
    groupadd -g 12000 khroxy; \
    useradd -u 12000 -g 12000 -m -s /bin/bash khroxy; \
    groupadd -g "$AGENT_UID" agent; \
    useradd -u "$AGENT_UID" -g "$AGENT_UID" -m -s /bin/bash agent; \
    chmod 0755 /home /home/khroxy; \
    install -d -m 0755 -o khroxy -g khroxy /home/khroxy/.config; \
    install -d -m 0700 -o khroxy -g khroxy /home/khroxy/.config/khroxy /home/khroxy/.local/bin; \
    install -d -m 0755 /workspace
# Codex is pinned; upgrades happen by rebuilding the image, not mutating a live container.
RUN npm install -g --no-audit --no-fund "@openai/codex@${CODEX_VERSION}"
# Caveman, Megacave and Ultracave are MODES of one extension, not 3 npm apps.
# The old upstream installer enabled a session hook and defaultMode=ultracave.
# Copy selected skills into the agent-owned persistent home explicitly at startup.
# Never run that installer or rely on volume copy-up for skill upgrades.
RUN case "$CAVEMAN_SUITE" in none) ;; essentials|full) \
      git clone --depth 1 --branch "$CAVEMAN_REF" https://github.com/JuliusBrussee/caveman.git /opt/caveman \
      && rm -rf /opt/caveman/.git ;; *) exit 1 ;; esac
# Keep defaults explicit. The upstream installer is never executed; no hooks are added.
ENV KHROXY_CAVEMAN_SUITE=${CAVEMAN_SUITE}
COPY khroxy_lib /home/khroxy/.local/lib/khroxy/khroxy_lib
COPY --chmod=0755 khroxy /home/khroxy/.local/bin/khroxy
# Locally supplied, user-approved Xray binary (setup.py copies; intentionally gitignored).
COPY --chmod=0755 docker/xray /home/khroxy/.local/bin/xray
COPY --chmod=0755 docker/network-guard.sh docker/entrypoint.sh docker/scheduler.sh docker/verify-isolation.py docker/caveman.py docker/runtime-paths.py /usr/local/bin/
RUN chown khroxy:khroxy /home/khroxy/.local/bin/khroxy /home/khroxy/.local/bin/xray \
    && ln -s /home/khroxy/.local/bin/khroxy /usr/local/bin/khroxy
ENV KHROXY_CONTAINER=1 \
    HOME=/home/agent \
    CODEX_HOME=/home/agent/.codex \
    HTTP_PROXY=http://127.0.0.1:20819 \
    HTTPS_PROXY=http://127.0.0.1:20819 \
    ALL_PROXY=http://127.0.0.1:20819 \
    http_proxy=http://127.0.0.1:20819 \
    https_proxy=http://127.0.0.1:20819 \
    all_proxy=http://127.0.0.1:20819 \
    NO_PROXY=localhost,127.0.0.1,::1 \
    no_proxy=localhost,127.0.0.1,::1 \
    NODE_USE_ENV_PROXY=1
EXPOSE 20819/tcp
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini","--","/usr/local/bin/entrypoint.sh"]
