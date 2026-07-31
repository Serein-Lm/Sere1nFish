ARG BASE_IMAGE=nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
FROM ${BASE_IMAGE}

USER root
ARG DEBIAN_FRONTEND=noninteractive
ARG MEANVC_COMMIT=b07024579284975bc8a6a9aa72201d6279b417ab
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu124

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        libsndfile1 \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install \
        --no-cache-dir \
        --index-url "${PYTORCH_INDEX_URL}" \
        "torch==2.5.1" \
        "torchaudio==2.5.1"
RUN git clone https://github.com/ASLP-lab/MeanVC.git /opt/meanvc \
    && cd /opt/meanvc \
    && git checkout "${MEANVC_COMMIT}" \
    && rm -rf .git

COPY requirements.txt /tmp/meanvc-runtime-requirements.txt
RUN /opt/venv/bin/python -m pip install \
    --no-cache-dir \
    --index-url "${PIP_INDEX_URL}" \
    -r /tmp/meanvc-runtime-requirements.txt

COPY . /opt/runtime/meanvc_runtime
ENV PYTHONPATH=/opt/runtime:/opt/meanvc \
    PATH=/opt/venv/bin:${PATH} \
    MEANVC_SOURCE_ROOT=/opt/meanvc \
    MEANVC_MODEL_DIR=/models \
    TORCH_HOME=/models/torch
WORKDIR /opt/runtime

ENTRYPOINT ["/bin/bash", "-lc"]
CMD ["python -m meanvc_runtime.model_assets && exec python -m uvicorn meanvc_runtime.app:app --host 127.0.0.1 --port 8766 --workers 1 --no-access-log"]
