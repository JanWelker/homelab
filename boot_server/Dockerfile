# Same interpreter the lint workflows run on, so a pylint pass locally says
# something about what actually serves.
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

# The request log is the whole diagnostic value of this thing during a PXE boot.
# Buffered, it arrives in 4 KB lumps, long after the node it describes gave up.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OUTPUT_DIR=/output

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY serve.py ./

# Documentation only: both are served in the host's network namespace, because
# TFTP answers from an ephemeral port that no port mapping reverses. See
# docs/boot_server/index.md.
EXPOSE 69/udp
EXPOSE 8000/tcp

# No USER directive, deliberately. TFTP has to bind port 69, and a non-root
# process cannot without ambient capabilities, which neither Docker nor
# Kubernetes hands a container. What runs as root here reads files and nothing
# else, with a read-only root filesystem and every capability dropped but
# NET_BIND_SERVICE -- see the `serve` target in the Makefile.
ENTRYPOINT ["python", "/app/serve.py"]
