# One image for the C2 Board app, the failover controller and the prober.
FROM python:3.13-slim
ARG KUBECTL_VERSION=v1.31.2
ARG TARGETARCH
ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "psycopg[binary]==3.2.3" \
 && apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH:-amd64}/kubectl" \
 && chmod +x /usr/local/bin/kubectl && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home c2 && mkdir /data && chown c2 /data
WORKDIR /app
COPY c2board/app.py c2board.py
COPY failover/controller.py controller.py
COPY prober/prober.py prober.py
USER c2
EXPOSE 8080 8090
CMD ["python", "c2board.py"]
