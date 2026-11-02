# Real Estate Semantic Search on AWS

This repository contains a non-production sample for natural-language property search. It combines Amazon OpenSearch Service vector and geospatial search, Amazon Bedrock, Amazon Location Service, Amazon Cognito, Amazon ECS on AWS Fargate, an internal Application Load Balancer, and Amazon CloudFront VPC origins.

This is sample code for non-production use. Work with your security and legal teams to meet your organizational security, regulatory, and compliance requirements before deployment.

Users initially see 80 deterministic, fictional property records across Atlanta, Chicago, Cupertino, Frisco, Miami, Oakland, and Seattle. The records are synthetic demonstration data. They are not sourced from an MLS or listing portal and are not suitable for property identification or valuation decisions.

See [the solution guide](opensearch-solution/README.md) for architecture, prerequisites, both deployment paths, costs, bring-your-own-data guidance, and cleanup. See [the threat model](THREAT_MODEL.md) for trust boundaries and production hardening gaps.

## CloudFront viewer TLS security consideration

The default deployment uses the generated `*.cloudfront.net` hostname and redirects browser HTTP requests to HTTPS. Its provider-managed default certificate forces CloudFront's legacy `TLSv1` viewer security policy, so this path does not enforce TLS 1.2 at the browser boundary. This is a non-passing residual security consideration, not a TLS 1.2 control. Certificate and DNS management remain customer-controlled.

Organizations requiring TLS 1.2 or later at the viewer boundary must supply both an authorized hostname and an issued ACM certificate from `us-east-1`. The optional deployment path configures the alias, SNI, and `TLSv1.2_2021` policy. Customers create the DNS record with Route 53 or another provider. The sample does not create a certificate, hosted zone, or DNS record.

This viewer consideration is distinct from the private CloudFront-to-ALB origin hop over HTTP and OpenSearch HTTPS with TLS 1.2 or later. Review the solution guide and threat model before deployment.

Before publishing a copy of this sample, create a fresh repository snapshot rather than transferring development history. Scan that snapshot for secrets, account identifiers, internal references, and customer information before making it public.

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for security issue reporting.

## License

This library is licensed under the MIT-0 License. See [LICENSE](LICENSE).
