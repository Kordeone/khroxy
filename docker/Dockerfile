# Build locally with a trusted, tested Xray binary copied into docker/xray.
# This image is a SINGLE container with separate khroxy and agent Unix users.
FROM node:24-trixie-slim
ARG AGENT_UID=1000
ARG INSTALL_AI_TOOLS=1
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3 curl ca-certificates \
      iptables iproute2 gosu tini git procps bash util-linux; \
    rm -rf /var/lib/apt/lists/*; \
    userdel -r node 2>/dev/null || true; \
    groupdel node 2>/dev/null || true; \
    groupadd -g 12000 khroxy; \
    useradd -u 12000 -g 12000 -m -s /bin/bash khroxy; \
    groupadd -g "$AGENT_UID" agent; \
    useradd -u "$AGENT_UID" -g "$AGENT_UID" -m -s /bin/bash agent; \
    install -d -m 0700 -o khroxy -g khroxy /home/khroxy/.config/khroxy /home/khroxy/.local/bin; \
    install -d -m 0755 /workspace
# Install both terminal AI clients at image-build time only. No in-place
# npm updates on live workspaces; rebuild image to upgrade them.
RUN if [ "$INSTALL_AI_TOOLS" = 1 ]; then \
      npm install -g --no-audit --no-fund @openai/codex @kilocode/cli; \
    fi
COPY --chmod=0755 khroxy /home/khroxy/.local/bin/khroxy
COPY --chmod=0755 docker/xray /home/khroxy/.local/bin/xray
COPY --chmod=0755 docker/network-guard.sh docker/entrypoint.sh docker/scheduler.sh /usr/local/bin/
RUN chown khroxy:khroxy /home/khroxy/.local/bin/khroxy /home/khroxy/.local/bin/xray \
    && ln -s /home/khroxy/.local/bin/khroxy /usr/local/bin/khroxy
ENV KHROXY_CONTAINER=1 \
    HOME=/home/khroxy \
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
