# kics-scan ignore-block - Build-time CDK deployment container; pinning apk/npm/uv versions is impractical for ephemeral build containers
#
# Node.js comes from the official image, pinned by version and digest, not from Alpine's nodejs
# package: Alpine's package lagged the 2026-10-21 Node.js security release (24.18.1, without the
# Brotli fix CVE-2026-111497 or the eMudhra CA removal; fixed in 24.22.1). A Node bump is a new tag
# and digest here (docker buildx imagetools inspect <tag>). Both stages are on the same Alpine
# release (3.24), so the node binary finds the musl and libstdc++ it was built against. Both
# stages are multi-arch indexes, so the build takes the variant of the task's platform.
FROM public.ecr.aws/docker/library/node:24.22.1-alpine3.24@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS node

FROM public.ecr.aws/docker/library/python:3.12-alpine3.24
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm

COPY .cdk.staging /.cdk.staging
COPY requirements.txt /.cdk.staging/requirements.txt
WORKDIR /.cdk.staging

# libstdc++ and libgcc are node's runtime libraries (what the official image installs). No apk
# nodejs or npm: Alpine's npm package would pull its own nodejs back in.
RUN apk add --update bash libgcc libstdc++ rsync && \
    ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    node --version && npm --version
RUN mv -f cdk-usecase.json cdk.json
RUN npm install --location=global aws-cdk
RUN uv pip install --system --requirement requirements.txt

# Run as non-root user for security
RUN addgroup -S appgroup && adduser -S appuser -G appgroup && \
    chown -R appuser:appgroup /.cdk.staging
USER appuser
