# Build the existing scripts/ci/Containerfile as localhost/backbond-ci:2.337.0
# first. Both profiles load the same immutable image into separate UID stores.
FROM docker.io/library/docker:29.9.2-dind@sha256:9e79b879b177fbd72c34376a3dd3d91747da5db49e1f560f76aea59561471977 AS docker
FROM localhost/backbond-ci:2.337.0
USER root
RUN apt-get update && apt-get install -y --no-install-recommends gettext-base nginx openssh-client openssl python-is-python3 \
    && rm -rf /var/lib/apt/lists/* \
    && mv /home/backbond-ci/runner /opt/runner
COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY build_pool_policy.py build-job-start.py build-job-start.sh /opt/backbond/
COPY build-runner-entrypoint.sh /opt/backbond/entrypoint.sh
RUN chmod 555 /opt/backbond/*.py /opt/backbond/*.sh
ENV ACTIONS_RUNNER_HOOK_JOB_STARTED=/opt/backbond/build-job-start.sh \
    DOCKER_HOST=unix:///docker-socket/docker.sock
USER 1001:1001
WORKDIR /home/backbond-ci
ENTRYPOINT ["/opt/backbond/entrypoint.sh"]
