FROM aletheia-hosted:r418-65c87fdb0a43 AS build
USER 0:0
ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH PYTHONDONTWRITEBYTECODE=1
WORKDIR /k14-build
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY src ./src
RUN /opt/venv/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir=/wheels . \
 && /opt/venv/bin/python -m pip install --no-deps --no-index --force-reinstall /wheels/*.whl

FROM aletheia-hosted:r418-k16-wolfi-offline
USER 0:0
RUN /opt/venv/bin/python -c "import shutil; shutil.rmtree('/opt/venv/lib/python3.12/site-packages/aletheia_lab'); shutil.rmtree('/opt/venv/lib/python3.12/site-packages/aletheia_lab-0.1.0.dist-info')"
COPY --from=build /opt/venv/lib/python3.12/site-packages/aletheia_lab /opt/venv/lib/python3.12/site-packages/aletheia_lab
COPY --from=build /opt/venv/lib/python3.12/site-packages/aletheia_lab-0.1.0.dist-info /opt/venv/lib/python3.12/site-packages/aletheia_lab-0.1.0.dist-info
ARG ALETHEIA_RELEASE_ID
ARG GIT_HEAD
ENV ALETHEIA_RELEASE_ID=$ALETHEIA_RELEASE_ID
LABEL org.opencontainers.image.revision=$GIT_HEAD com.aletheia.k14.purpose="isolated-local-staging"
USER 10001:10001
