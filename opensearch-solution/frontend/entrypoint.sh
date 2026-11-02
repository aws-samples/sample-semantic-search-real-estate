#!/bin/bash
set -euo pipefail

required_vars=(
    OPENSEARCH_ENDPOINT
    AWS_REGION
    COGNITO_USER_POOL_ID
    COGNITO_APP_CLIENT_ID
    COGNITO_IDENTITY_POOL_ID
    PLACE_INDEX_NAME
)
for variable in "${required_vars[@]}"; do
    if [[ -z "${!variable:-}" ]]; then
        echo "ERROR: Required environment variable ${variable} is not set" >&2
        exit 1
    fi
done

if [[ "$(id -u)" -eq 0 ]]; then
    echo "ERROR: Refusing to run the application as root" >&2
    exit 1
fi

browser_server_args=()
if [[ -n "${STREAMLIT_BROWSER_SERVER_ADDRESS:-}" ]]; then
    browser_server_args+=(
        --browser.serverAddress="${STREAMLIT_BROWSER_SERVER_ADDRESS}"
    )
fi

exec streamlit run app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=true \
    --server.enableXsrfProtection=true \
    --server.maxUploadSize=10 \
    --browser.gatherUsageStats=false \
    "${browser_server_args[@]}" \
    --logger.level=info
