FROM python:3.13-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/Yumiiiiiiiiii/pterodactyl" \
      org.opencontainers.image.title="Endstone Fork Wings Runtime"

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=UTF-8 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 HOME=/home/container USER=container \
    ENDSTONE_SOURCE=custom-fork ENDSTONE_FORK=Yumiiiiiiiiii/endstone \
    ENDSTONE_RELEASE=stable UPDATE_CHANNEL=stable BDS_VERSION=1.26.60 \
    AUTO_UPDATE_CHECK=true AUTO_UPDATE_PLUGINS=true BACKUP_BEFORE_UPDATE=true \
    ROLLBACK_ON_FAILURE=true UPDATE_REQUIRE_APPROVAL=true DEBUG=false

RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends libcurl4 ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --home-dir /home/container --shell /bin/bash container

COPY runtime/manager.py /opt/endstone-fork/manager.py
COPY runtime/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh /opt/endstone-fork/manager.py

USER container
WORKDIR /home/container
EXPOSE 19132/udp 19133/udp
STOPSIGNAL SIGTERM
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python /opt/endstone-fork/manager.py start"]
