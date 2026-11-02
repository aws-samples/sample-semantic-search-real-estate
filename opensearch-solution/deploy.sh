#!/bin/bash
set -euo pipefail

DEPLOY_BACKEND=true
DEPLOY_FRONTEND=true
SKIP_SETUP=false
SKIP_BOOTSTRAP=false
REGION="${AWS_DEFAULT_REGION:-}"
PROFILE="${AWS_PROFILE:-}"
DOMAIN_NAME=""
CLOUDFRONT_CERTIFICATE_ARN=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --region) REGION="${2:?--region requires a value}"; shift 2 ;;
        --profile) PROFILE="${2:?--profile requires a value}"; shift 2 ;;
        --domain-name) DOMAIN_NAME="${2:?--domain-name requires a value}"; shift 2 ;;
        --cloudfront-certificate-arn) CLOUDFRONT_CERTIFICATE_ARN="${2:?--cloudfront-certificate-arn requires a value}"; shift 2 ;;
        --backend-only) DEPLOY_FRONTEND=false; shift ;;
        --frontend-only) DEPLOY_BACKEND=false; shift ;;
        --deploy-frontend) DEPLOY_FRONTEND=true; shift ;;
        --skip-setup) SKIP_SETUP=true; shift ;;
        --skip-bootstrap) SKIP_BOOTSTRAP=true; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
if [[ -z "$REGION" ]]; then
    echo "ERROR: provide --region or set AWS_DEFAULT_REGION" >&2
    exit 2
fi
if { [[ -n "$DOMAIN_NAME" ]] && [[ -z "$CLOUDFRONT_CERTIFICATE_ARN" ]]; } || \
   { [[ -z "$DOMAIN_NAME" ]] && [[ -n "$CLOUDFRONT_CERTIFICATE_ARN" ]]; }; then
    echo "ERROR: --domain-name and --cloudfront-certificate-arn must be supplied together" >&2
    exit 2
fi
# Expanded as ${TLS_CONTEXT[@]+...} below: an empty array is "unbound" under set -u in Bash 3.2 (macOS).
TLS_CONTEXT=()
if [[ -n "$DOMAIN_NAME" ]]; then
    TLS_CONTEXT=(
        --context "domain_name=$DOMAIN_NAME"
        --context "cloudfront_certificate_arn=$CLOUDFRONT_CERTIFICATE_ARN"
    )
fi
if [[ -n "$PROFILE" ]]; then
    export AWS_PROFILE="$PROFILE"
fi
export AWS_DEFAULT_REGION="$REGION"

MIN_CDK_CLI_VERSION="2.1144.0"
PYTHON_VERSION_CHECK='import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c "$PYTHON_VERSION_CHECK"; then
    echo "ERROR: Python 3.11 or later is required as python3 (found: $(python3 --version 2>&1 || echo none))" >&2
    exit 1
fi
if ! aws --version 2>&1 | grep -q '^aws-cli/2\.'; then
    echo "ERROR: AWS CLI v2 is required (found: $(aws --version 2>&1 || echo none))" >&2
    exit 1
fi
if ! command -v cdk >/dev/null 2>&1; then
    echo "ERROR: AWS CDK CLI $MIN_CDK_CLI_VERSION or later is required" >&2
    exit 1
fi
CDK_CLI_VERSION=$(cdk --version 2>/dev/null | awk '{print $1}')
if ! python3 - "$CDK_CLI_VERSION" "$MIN_CDK_CLI_VERSION" <<'PY'
import sys
def parse(version):
    return tuple(int(part) for part in version.split("."))
try:
    sys.exit(0 if parse(sys.argv[1]) >= parse(sys.argv[2]) else 1)
except ValueError:
    sys.exit(1)
PY
then
    echo "ERROR: AWS CDK CLI $MIN_CDK_CLI_VERSION or later is required (found: ${CDK_CLI_VERSION:-unknown})." >&2
    echo "       Upgrade with: npm install -g aws-cdk@latest" >&2
    exit 1
fi

# Create a virtualenv, or rebuild one left over from an older Python.
ensure_venv() {
    if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c "$PYTHON_VERSION_CHECK"; then
        rm -rf .venv
        python3 -m venv .venv
    fi
}

cd backend/infrastructure
ensure_venv
source .venv/bin/activate
pip install --no-cache-dir -r requirements.txt
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
if [[ -n "$DOMAIN_NAME" ]]; then
    python ../scripts/validate_cloudfront_certificate.py \
        --domain-name "$DOMAIN_NAME" \
        --certificate-arn "$CLOUDFRONT_CERTIFICATE_ARN"
fi
echo "Deploying non-production sample to account $ACCOUNT in $REGION"
if [[ "$SKIP_BOOTSTRAP" == true ]]; then
    echo "Skipping cdk bootstrap. The account and Region must already be bootstrapped."
else
    cdk bootstrap "aws://${ACCOUNT}/${REGION}"
fi

if [[ "$DEPLOY_BACKEND" == true && "$DEPLOY_FRONTEND" == true ]]; then
    cdk deploy RealEstateSearchStack RealEstateECSFargateStack \
        --context deploy_backend=true --context deploy_frontend=true \
        ${TLS_CONTEXT[@]+"${TLS_CONTEXT[@]}"}
elif [[ "$DEPLOY_BACKEND" == true ]]; then
    cdk deploy RealEstateSearchStack \
        --context deploy_backend=true --context deploy_frontend=false \
        ${TLS_CONTEXT[@]+"${TLS_CONTEXT[@]}"}
elif [[ "$DEPLOY_FRONTEND" == true ]]; then
    cdk deploy RealEstateECSFargateStack \
        --context deploy_backend=false --context deploy_frontend=true \
        ${TLS_CONTEXT[@]+"${TLS_CONTEXT[@]}"}
fi
cd ../..

if [[ "$SKIP_SETUP" == false && "$DEPLOY_FRONTEND" == true ]]; then
    ensure_venv
    source .venv/bin/activate
    pip install --no-cache-dir -r requirements.txt

    read -r -p "Run the private synthetic-data loader task? (y/N): " reply
    if [[ "$reply" =~ ^[Yy]$ ]]; then
        python backend/scripts/run_data_loader.py --region "$REGION"
    fi
    read -r -p "Create or reset the optional demo user? (y/N): " reply
    if [[ "$reply" =~ ^[Yy]$ ]]; then
        python backend/scripts/create_demo_user_secure.py --region "$REGION"
    fi
fi

if [[ "$DEPLOY_FRONTEND" == true ]]; then
    application_url=$(aws cloudformation describe-stacks \
        --stack-name RealEstateECSFargateStack \
        --query "Stacks[0].Outputs[?OutputKey=='RealEstateSemanticSearchApplicationURL'].OutputValue" \
        --output text)
    distribution_target=$(aws cloudformation describe-stacks \
        --stack-name RealEstateECSFargateStack \
        --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionDomainName'].OutputValue" \
        --output text)
    echo "Application HTTPS URL: $application_url"
    echo "CloudFront DNS target: $distribution_target"
fi
cat <<'EOF'
Deployment command completed. This is a non-production sample.
The internal load balancer and private tasks are reachable publicly only through CloudFront.
Retrieve the optional demo password from the Secrets Manager secret printed by the user setup script.
Run ./teardown.sh --region <region> when finished to avoid ongoing charges.
EOF
