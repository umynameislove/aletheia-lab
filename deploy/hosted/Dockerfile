# Build from repository root: docker build -f deploy/hosted/Dockerfile ... .
FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS build
ARG SOURCE_DATE_EPOCH
ARG ALETHEIA_RELEASE_ID
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY deploy/hosted/requirements.lock deploy/hosted/build-requirements.lock /locks/
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --only-binary=:all: --require-hashes -r /locks/build-requirements.lock \
 && /opt/venv/bin/pip install --only-binary=:all: --require-hashes -r /locks/requirements.lock
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY src ./src
RUN /opt/venv/bin/python -c "import os,re; assert re.fullmatch('[0-9a-f]{64}', os.environ['ALETHEIA_RELEASE_ID']); assert os.environ['SOURCE_DATE_EPOCH'].isdigit()" \
 && /opt/venv/bin/pip wheel --no-deps --no-build-isolation --wheel-dir=/app-wheel . \
 && /opt/venv/bin/pip install --no-deps /app-wheel/*.whl \
 && /opt/venv/bin/pip check

FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ARG ALETHEIA_RELEASE_ID
LABEL org.opencontainers.image.revision=$ALETHEIA_RELEASE_ID
ENV ALETHEIA_RELEASE_ID=$ALETHEIA_RELEASE_ID PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
COPY --from=build /opt/venv /opt/venv
# DSA-6613-1 fixes both zlib write-path CVEs reported for the pinned base.
RUN apt-get update \
 && apt-get install --no-install-recommends -y zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 aletheia \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin aletheia \
 && mkdir -p /var/lib/aletheia \
 && chown 10001:10001 /var/lib/aletheia
USER 10001:10001
WORKDIR /var/lib/aletheia
EXPOSE 18766
ENTRYPOINT ["/opt/venv/bin/python", "-m", "aletheia_lab.hosted.container_entrypoint"]
CMD ["api"]
