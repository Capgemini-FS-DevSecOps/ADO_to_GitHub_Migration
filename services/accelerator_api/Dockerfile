FROM python:3.11-slim

WORKDIR /app

# gh-ado2gh bundles .NET — invariant mode avoids ICU; libicu-dev pulls the distro ICU runtime.
ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git git-lfs ca-certificates curl gnupg libicu-dev \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 gh extension install github/gh-gei \
    && DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 gh extension install github/gh-ado2gh \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt pyproject.toml ./
COPY ado2gh ./ado2gh
COPY services/accelerator_api ./services/accelerator_api
COPY migration.yaml ./migration.yaml

RUN pip install --no-cache-dir -e ".[api,postgres,redis]"

ENV ADO2GH_CONFIG=/app/migration.yaml
EXPOSE 8080
CMD ["uvicorn", "services.accelerator_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
