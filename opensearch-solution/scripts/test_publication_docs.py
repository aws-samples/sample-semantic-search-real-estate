#!/usr/bin/env python3
"""Regression checks for publication-safe documentation and source content."""

from __future__ import annotations

import hashlib
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOLUTION = ROOT / "opensearch-solution"
DOC_PATHS = [
    ROOT / "README.md",
    ROOT / "THREAT_MODEL.md",
    SOLUTION / "README.md",
    SOLUTION / "data" / "README.md",
]
PLACEHOLDER_ACCOUNTS = {"000000000000", "111122223333", "444455556666"}
SCAFFOLD_EMAIL_EXEMPTIONS = {
    "CODE_OF_CONDUCT.md": "34b6c98d5c23127ae6769e95e483e5bf6d3704ae1f0d3ae4e69d15f4ede118b6",
    "CONTRIBUTING.md": "993e7639b93ea44d96be9813b63f5beada0deb2d4677d2e91ea96bc51e15b44b",
}


def has_authoritative_scaffold_checksum(path: Path) -> bool:
    try:
        relative_path = path.relative_to(ROOT).as_posix()
    except ValueError:
        return False
    expected_checksum = SCAFFOLD_EMAIL_EXEMPTIONS.get(relative_path)
    return (
        expected_checksum is not None
        and hashlib.sha256(path.read_bytes()).hexdigest() == expected_checksum
    )


def candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


class PublicationDocumentationTest(unittest.TestCase):
    def test_tls_boundary_language_is_truthful(self) -> None:
        text = "\n".join(path.read_text(encoding="utf-8") for path in DOC_PATHS)
        self.assertNotIn("CloudFront distribution (TLS 1.2 minimum", text)
        for phrase in (
            "provider-managed default certificate",
            "legacy `TLSv1` viewer",
            "does not enforce TLS 1.2",
            "`us-east-1` ACM certificate",
            "`TLSv1.2_2021`",
            "private CloudFront-to-ALB",
            "OpenSearch HTTPS with TLS 1.2",
        ):
            self.assertIn(phrase, text)

    def test_synthetic_data_limitations_are_prominent(self) -> None:
        text = "\n".join(path.read_text(encoding="utf-8") for path in DOC_PATHS)
        self.assertIn("80 fictional records across seven cities", text)
        self.assertIn("not suitable for property identification or valuation decisions", text)
        self.assertIn("cannot verify the factual authenticity", text)

    def test_public_candidate_contains_no_internal_references_or_identifiers(self) -> None:
        internal_url = re.compile(
            r"https?://[^\s)]+(?:w\.amazon\.com|amazon\.dev|aws\.dev|corp\.amazon\.com|issues\.amazon\.com)",
            re.IGNORECASE,
        )
        amazon_email = re.compile(r"\b[A-Z0-9._%+-]+@amazon\.com\b", re.IGNORECASE)
        account_id = re.compile(r"(?<!\d)\d{12}(?!\d)")
        pcsr_link = re.compile(r"https?://[^\s)]*pcsr", re.IGNORECASE)
        this_file = Path(__file__).resolve()

        for path in candidate_files():
            if path.resolve() == this_file or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn(".agents/", text)
                self.assertIsNone(internal_url.search(text))
                if not has_authoritative_scaffold_checksum(path):
                    self.assertIsNone(amazon_email.search(text))
                self.assertIsNone(pcsr_link.search(text))
                unexpected_accounts = {
                    match.group(0) for match in account_id.finditer(text)
                } - PLACEHOLDER_ACCOUNTS
                self.assertEqual(unexpected_accounts, set())


if __name__ == "__main__":
    unittest.main()
