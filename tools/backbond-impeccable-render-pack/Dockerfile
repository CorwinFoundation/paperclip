FROM mcr.microsoft.com/playwright@sha256:b27e719ecbfef153e13fd24e8341736733bf2658b229677eb21ff57ff5d7fb29

WORKDIR /pack
COPY package.json package-lock.json ./
COPY vendor ./vendor
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY LICENSE UPSTREAM-NOTICE.md MODIFICATIONS.md THREAT-MODEL.md COMMAND-ALLOWLIST.json ENABLED-FILES.md SCORING-RUBRIC.md BUILD-MANIFEST.json SOURCE-MANIFEST.json ./
COPY bin ./bin
COPY lib ./lib
RUN chown -R root:root /pack \
    && find /pack -type d -exec chmod 0555 {} + \
    && find /pack -type f -exec chmod 0444 {} + \
    && chmod 0555 /pack/bin/backbond-impeccable-phase-two.mjs

USER 65532:65532
ENTRYPOINT ["node", "/pack/bin/backbond-impeccable-phase-two.mjs"]
CMD ["render", "--input", "/input", "--output", "/output"]
