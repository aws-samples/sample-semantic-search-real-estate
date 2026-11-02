#!/usr/bin/env python3
"""CDK assertions for default and customer-managed CloudFront TLS paths."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Template

from ecs_fargate_stack import ECSFargateStack
from real_estate_search_stack import RealEstateSearchStack


ACCOUNT = "111122223333"
REGION = "us-east-1"
DOMAIN = "search.example.com"
CERTIFICATE_ARN = f"arn:aws:acm:{REGION}:{ACCOUNT}:certificate/00000000-0000-0000-0000-000000000000"

os.chdir(Path(__file__).resolve().parent)


def create_stacks(domain_name=None, certificate_arn=None):
    app = cdk.App(
        context={
            f"availability-zones:account={ACCOUNT}:region={REGION}": [
                f"{REGION}a",
                f"{REGION}b",
            ]
        }
    )
    env = cdk.Environment(account=ACCOUNT, region=REGION)
    backend = RealEstateSearchStack(app, "Backend", env=env)
    frontend = ECSFargateStack(
        app,
        "Frontend",
        backend_stack=backend,
        domain_name=domain_name,
        cloudfront_certificate_arn=certificate_arn,
        env=env,
    )
    return backend, frontend


class CloudFrontTlsTest(unittest.TestCase):
    def distribution_config(self, template: Template) -> dict:
        resources = template.find_resources("AWS::CloudFront::Distribution")
        self.assertEqual(len(resources), 1)
        return next(iter(resources.values()))["Properties"]["DistributionConfig"]

    def assert_secure_boundaries(self, template: Template) -> None:
        self.assertEqual(len(template.find_resources("AWS::CloudFront::VpcOrigin")), 1)
        load_balancers = template.find_resources(
            "AWS::ElasticLoadBalancingV2::LoadBalancer"
        )
        self.assertEqual(len(load_balancers), 1)
        self.assertEqual(
            next(iter(load_balancers.values()))["Properties"]["Scheme"], "internal"
        )
        for service in template.find_resources("AWS::ECS::Service").values():
            awsvpc = service["Properties"]["NetworkConfiguration"][
                "AwsvpcConfiguration"
            ]
            self.assertEqual(awsvpc["AssignPublicIp"], "DISABLED")
        for ingress in template.find_resources(
            "AWS::EC2::SecurityGroupIngress"
        ).values():
            properties = ingress["Properties"]
            self.assertNotIn(properties.get("CidrIp"), {"0.0.0.0/0"})
            self.assertNotIn(properties.get("CidrIpv6"), {"::/0"})

    def test_default_hostname_omits_custom_viewer_certificate(self) -> None:
        _, frontend = create_stacks()
        template = Template.from_stack(frontend)
        config = self.distribution_config(template)
        self.assertNotIn("Aliases", config)
        self.assertNotIn("ViewerCertificate", config)
        self.assert_secure_boundaries(template)

    def test_domain_without_certificate_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be supplied together"):
            create_stacks(domain_name=DOMAIN)

    def test_certificate_without_domain_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be supplied together"):
            create_stacks(certificate_arn=CERTIFICATE_ARN)

    def test_custom_hostname_configures_tls_1_2_2021(self) -> None:
        _, frontend = create_stacks(DOMAIN, CERTIFICATE_ARN)
        template = Template.from_stack(frontend)
        config = self.distribution_config(template)
        self.assertEqual(config["Aliases"], [DOMAIN])
        self.assertEqual(
            config["ViewerCertificate"],
            {
                "AcmCertificateArn": CERTIFICATE_ARN,
                "MinimumProtocolVersion": "TLSv1.2_2021",
                "SslSupportMethod": "sni-only",
            },
        )
        self.assert_secure_boundaries(template)


class UserPoolClientTest(unittest.TestCase):
    def test_oauth_is_disabled(self) -> None:
        backend, _ = create_stacks()
        clients = Template.from_stack(backend).find_resources(
            "AWS::Cognito::UserPoolClient"
        )
        self.assertEqual(len(clients), 1)
        properties = next(iter(clients.values()))["Properties"]
        for key in ("AllowedOAuthFlows", "AllowedOAuthScopes", "CallbackURLs"):
            self.assertNotIn(key, properties)
        self.assertFalse(properties.get("AllowedOAuthFlowsUserPoolClient", False))
        self.assertIn("ALLOW_USER_PASSWORD_AUTH", properties["ExplicitAuthFlows"])


if __name__ == "__main__":
    unittest.main()
