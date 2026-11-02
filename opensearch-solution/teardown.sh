#!/bin/bash
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-}"
PROFILE="${AWS_PROFILE:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --region) REGION="${2:?--region requires a value}"; shift 2 ;;
        --profile) PROFILE="${2:?--profile requires a value}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
if [[ -z "$REGION" ]]; then
    echo "ERROR: provide --region or set AWS_DEFAULT_REGION" >&2
    exit 2
fi

if ! aws --version 2>&1 | grep -q '^aws-cli/2\.'; then
    echo "ERROR: AWS CLI v2 is required (found: $(aws --version 2>&1 || echo none))" >&2
    exit 1
fi

AWS_ARGS=(--region "$REGION" --no-cli-pager)
if [[ -n "$PROFILE" ]]; then
    AWS_ARGS+=(--profile "$PROFILE")
fi
ACCOUNT=$(aws sts get-caller-identity "${AWS_ARGS[@]}" --query Account --output text)

echo "This deletes only RealEstateECSFargateStack and RealEstateSearchStack."
echo "Account: $ACCOUNT"
echo "Region: $REGION"
read -r -p "Type DELETE to continue: " confirmation
if [[ "$confirmation" != "DELETE" ]]; then
    echo "Teardown cancelled."
    exit 1
fi

stack_exists() {
    aws cloudformation describe-stacks "${AWS_ARGS[@]}" --stack-name "$1" >/dev/null 2>&1
}

delete_stack() {
    local stack_name="$1"
    if ! stack_exists "$stack_name"; then
        echo "Stack not found, nothing to delete: $stack_name"
        return 0
    fi
    echo "Deleting stack: $stack_name"
    aws cloudformation delete-stack "${AWS_ARGS[@]}" --stack-name "$stack_name"
    if aws cloudformation wait stack-delete-complete "${AWS_ARGS[@]}" --stack-name "$stack_name"; then
        echo "Deleted stack: $stack_name"
    else
        echo "ERROR: deletion did not complete for $stack_name" >&2
        return 1
    fi
}

delete_stack "RealEstateECSFargateStack"
delete_stack "RealEstateSearchStack"

remaining=0
for stack_name in RealEstateECSFargateStack RealEstateSearchStack; do
    if stack_exists "$stack_name"; then
        status=$(aws cloudformation describe-stacks "${AWS_ARGS[@]}" \
            --stack-name "$stack_name" --query 'Stacks[0].StackStatus' --output text)
        echo "Remaining stack: $stack_name ($status)" >&2
        remaining=1
    else
        echo "Confirmed absent: $stack_name"
    fi
done

if [[ "$remaining" -ne 0 ]]; then
    exit 1
fi
echo "Sample stack cleanup completed. No shared CDK bootstrap resources were modified."
