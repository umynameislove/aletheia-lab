FROM cgr.dev/chainguard/wolfi-base@sha256:1f6b749929164c3d9e7cad0289254f39b2ba51e1dddaeb0fa57532ea03f2db31 AS runtime
RUN apk add --no-cache python-3.12=3.12.16_git20261030-r0 sqlite-libs=3.54.1-r0 libstdc++=16.2.0-r3 \
 && addgroup -g 10001 aletheia \
 && adduser -D -H -u 10001 -G aletheia aletheia \
 && mkdir -p /var/lib/aletheia \
 && chown 10001:10001 /var/lib/aletheia

FROM runtime AS build
RUN apk add --no-cache py3.12-pip=26.2.2-r2
COPY requirements.lock aletheia_lab-0.1.0-py3-none-any.whl /build/
RUN cd /build \
 && echo '1a1331f4e170e87a45db325be4291f79c3ad320411410deeb564d1b980b7e880  aletheia_lab-0.1.0-py3-none-any.whl' | sha256sum -c - \
 && python3.12 -m venv --without-pip /opt/venv \
 && python3.12 -m pip --python /opt/venv/bin/python install --only-binary=:all: --require-hashes -r requirements.lock \
 && python3.12 -m pip --python /opt/venv/bin/python install --no-deps aletheia_lab-0.1.0-py3-none-any.whl \
 && python3.12 -m pip --python /opt/venv/bin/python check

FROM runtime
ARG ALETHEIA_RELEASE_ID=65c87fdb0a43e1ef465a1eb27901373a0f4f3d3a4a56da13e5b7b34341b56c58
LABEL org.opencontainers.image.revision=$ALETHEIA_RELEASE_ID
ENV ALETHEIA_RELEASE_ID=$ALETHEIA_RELEASE_ID PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
COPY --from=build /opt/venv /opt/venv
USER 10001:10001
WORKDIR /var/lib/aletheia
EXPOSE 18766
ENTRYPOINT ["/opt/venv/bin/python", "-m", "aletheia_lab.hosted.container_entrypoint"]
CMD ["api"]
