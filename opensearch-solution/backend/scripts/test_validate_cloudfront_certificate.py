#!/usr/bin/env python3
"""Unit tests for the read-only CloudFront certificate preflight."""

from __future__ import annotations

import unittest

from validate_cloudfront_certificate import validate_certificate


ACCOUNT = "111122223333"
ARN = f"arn:aws:acm:us-east-1:{ACCOUNT}:certificate/00000000-0000-0000-0000-000000000000"


class FakeSts:
    def __init__(self, account: str = ACCOUNT) -> None:
        self.account = account

    def get_caller_identity(self) -> dict[str, str]:
        return {"Account": self.account}


class FakeAcm:
    def __init__(self, names, status: str = "ISSUED") -> None:
        self.names = names
        self.status = status

    def describe_certificate(self, CertificateArn: str) -> dict:
        return {
            "Certificate": {
                "DomainName": self.names[0],
                "SubjectAlternativeNames": self.names,
                "Status": self.status,
            }
        }


class CertificatePreflightTest(unittest.TestCase):
    def validate(self, domain: str, names, arn: str = ARN, account: str = ACCOUNT, status: str = "ISSUED") -> None:
        validate_certificate(domain, arn, FakeSts(account), FakeAcm(names, status))

    def test_exact_name(self) -> None:
        self.validate("search.example.com", ["search.example.com"])

    def test_one_label_wildcard(self) -> None:
        self.validate("search.example.com", ["*.example.com"])

    def test_wrong_depth_wildcard(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not cover"):
            self.validate("deep.search.example.com", ["*.example.com"])

    def test_wrong_account(self) -> None:
        with self.assertRaisesRegex(ValueError, "active AWS account"):
            self.validate("search.example.com", ["search.example.com"], account="444455556666")

    def test_wrong_region(self) -> None:
        wrong_arn = ARN.replace(":us-east-1:", ":us-west-2:")
        with self.assertRaisesRegex(ValueError, "us-east-1"):
            self.validate("search.example.com", ["search.example.com"], arn=wrong_arn)

    def test_non_issued_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "ISSUED"):
            self.validate("search.example.com", ["search.example.com"], status="PENDING_VALIDATION")

    def test_absent_hostname_coverage(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not cover"):
            self.validate("search.example.com", ["other.example.com"])


if __name__ == "__main__":
    unittest.main()
