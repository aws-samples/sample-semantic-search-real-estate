#!/usr/bin/env python3
"""Read-only preflight for a customer-managed CloudFront certificate."""

from __future__ import annotations

import argparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def parse_certificate_arn(certificate_arn: str) -> tuple[str, str]:
    parts = certificate_arn.split(":", 5)
    if len(parts) != 6 or parts[2] != "acm" or not parts[5].startswith("certificate/"):
        raise ValueError("certificate ARN must identify an ACM certificate")
    region, account = parts[3], parts[4]
    if region != "us-east-1":
        raise ValueError("CloudFront certificate must be in us-east-1")
    return region, account


def hostname_is_covered(hostname: str, certificate_name: str) -> bool:
    hostname = hostname.rstrip(".").lower()
    certificate_name = certificate_name.rstrip(".").lower()
    if hostname == certificate_name:
        return True
    if not certificate_name.startswith("*."):
        return False
    suffix = certificate_name[2:]
    return (
        hostname.endswith(f".{suffix}")
        and len(hostname.split(".")) == len(suffix.split(".")) + 1
    )


def validate_certificate(
    domain_name: str,
    certificate_arn: str,
    sts_client,
    acm_client,
) -> None:
    _, certificate_account = parse_certificate_arn(certificate_arn)
    active_account = sts_client.get_caller_identity()["Account"]
    if certificate_account != active_account:
        raise ValueError("certificate ARN must belong to the active AWS account")

    certificate = acm_client.describe_certificate(
        CertificateArn=certificate_arn
    )["Certificate"]
    if certificate.get("Status") != "ISSUED":
        raise ValueError("CloudFront certificate status must be ISSUED")

    names = {
        certificate.get("DomainName", ""),
        *certificate.get("SubjectAlternativeNames", []),
    }
    if not any(hostname_is_covered(domain_name, name) for name in names if name):
        raise ValueError("certificate does not cover the configured domain name")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain-name", required=True)
    parser.add_argument("--certificate-arn", required=True)
    args = parser.parse_args()

    try:
        parse_certificate_arn(args.certificate_arn)
        validate_certificate(
            args.domain_name,
            args.certificate_arn,
            boto3.client("sts"),
            boto3.client("acm", region_name="us-east-1"),
        )
    except (BotoCoreError, ClientError, ValueError, KeyError) as error:
        raise SystemExit(f"ERROR: {error}") from error
    print("CloudFront certificate preflight passed")


if __name__ == "__main__":
    main()
