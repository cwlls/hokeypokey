# hokeypokey — HKP/HKPS keyserver
#
# Multi-stage build: builder installs dependencies, final image is lean.

# ---- Builder stage ----
FROM python:3.13-slim AS builder

WORKDIR /build

# Install uv
RUN pip install --no-cache-dir uv

# Copy project files
COPY pyproject.toml uv.lock LICENSE README.md ./
COPY src/ ./src/

# Install into a virtual environment at /app/.venv — its FINAL path in the
# runtime image. Entry-point scripts embed the venv path in their shebang
# (#!/app/.venv/bin/python3), so building at /build/.venv and copying to
# /app/.venv would leave broken interpreter paths ("no such file or directory").
# Non-editable so the venv is self-contained and can be copied without the
# source tree.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
RUN uv sync --no-dev --frozen --no-editable

# ---- Final stage ----
FROM python:3.13-slim

LABEL org.opencontainers.image.title="hokeypokey" \
      org.opencontainers.image.description="A read-only HKP/HKPS keyserver that federates GPG keys from pluggable sources" \
      org.opencontainers.image.licenses="Apache-2.0"

# Create a non-root user
RUN useradd --system --no-create-home --shell /sbin/nologin hokeypokey

WORKDIR /app

# Copy the virtual environment from the builder (same path as it was built at,
# keeping script shebangs valid)
COPY --from=builder /app/.venv /app/.venv

# Make the venv's binaries available
ENV PATH="/app/.venv/bin:$PATH"

# Standard HKP port
EXPOSE 11371

# Run as non-root
USER hokeypokey

ENTRYPOINT ["hokeypokey"]
CMD ["--config", "/etc/hokeypokey/hokeypokey.toml"]
