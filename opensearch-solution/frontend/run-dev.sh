#!/bin/bash
set -euo pipefail

echo "Starting the local Streamlit server on http://127.0.0.1:8501"
if [[ ! -d .venv ]]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --no-cache-dir -r requirements.txt

exec streamlit run app.py \
    --server.address=127.0.0.1 \
    --server.port=8501 \
    --server.enableCORS=true \
    --server.enableXsrfProtection=true \
    --browser.gatherUsageStats=false
