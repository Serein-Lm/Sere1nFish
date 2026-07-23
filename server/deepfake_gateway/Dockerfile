ARG BASE_IMAGE=sere1nfish/facefusion:3.7.1-cuda12.8-py312
FROM ${BASE_IMAGE}

USER root
ARG TENSORRT_VERSION=10.9.0.34
RUN python3 -m pip install --no-cache-dir "tensorrt-cu12==${TENSORRT_VERSION}"
COPY . /opt/gateway/deepfake_gateway
ENV PYTHONPATH=/opt/facefusion:/opt/gateway \
    FACEFUSION_CONFIG_PATH=/opt/facefusion/facefusion.ini \
    LD_LIBRARY_PATH=/opt/venv/lib/python3.12/site-packages/tensorrt_libs:/usr/local/cuda/lib64
WORKDIR /opt/facefusion

ENTRYPOINT ["python3", "-m", "uvicorn", "deepfake_gateway.app:app"]
CMD ["--host", "0.0.0.0", "--port", "8443", "--workers", "1", "--ssl-keyfile", "/run/secrets/tls.key", "--ssl-certfile", "/run/secrets/tls.crt", "--no-access-log"]
