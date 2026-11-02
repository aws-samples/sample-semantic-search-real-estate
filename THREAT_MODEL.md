# Threat model

## Scope

This threat model covers the non-production semantic-search sample, including its browser UI, CloudFront distribution, private VPC origin, internal load balancer, ECS tasks, Cognito pools, OpenSearch domain, Bedrock calls, Location Service calls, logs, and optional demo credentials.

## Assets

- Cognito tokens and temporary AWS credentials
- Optional demo-user password in Secrets Manager
- Search queries and generated model responses
- Fictional indexed records and generated embeddings
- Infrastructure configuration, IAM roles, and service logs

## Actors

- Anonymous internet users reaching CloudFront
- Authenticated sample users
- The sample operator deploying, loading, and removing resources
- AWS managed services acting through granted IAM roles
- An attacker with stolen credentials or the ability to send crafted requests

## Entry points and trust boundaries

1. The public browser-to-CloudFront boundary redirects HTTP to HTTPS. The default generated hostname uses the provider-managed certificate and legacy `TLSv1` viewer policy. The optional customer-managed hostname path uses SNI and `TLSv1.2_2021`.
2. CloudFront crosses a VPC origin boundary to an internal Application Load Balancer over HTTP inside the private origin connection.
3. The load balancer reaches private ECS tasks on the Streamlit port.
4. Cognito authenticates users. The browser session exchanges a Cognito token for temporary, scoped IAM credentials.
5. Authenticated calls cross AWS service boundaries to OpenSearch, Bedrock, and Location Service. OpenSearch enforces HTTPS with TLS 1.2 or later.
6. A separate one-off ECS task crosses the isolated data boundary to create an index and load generated records.
7. CloudFront, ALB, ECS, and OpenSearch write operational data to S3 or CloudWatch logs.

## Threats and implemented mitigations

| Threat | Mitigation in this sample |
|---|---|
| Direct origin access | Internal ALB, private subnets, CloudFront VPC origin, and ALB ingress only from the AWS-managed CloudFront origin-facing prefix list |
| Unauthenticated data access | Cognito user authentication and no unauthenticated identity-pool identities |
| Unintended OAuth sign-in paths | OAuth is disabled on the user pool client, so it carries no OAuth flows, scopes, or callback URLs even if a user pool domain is added later |
| User enumeration and credential disclosure | Cognito prevent-user-existence errors, generic login errors, blank login fields, and a user script that never prints passwords |
| Stolen long-lived application credentials | The frontend uses Cognito-vended temporary credentials and receives no AWS data-plane task-role permissions |
| Excessive data mutation | Authenticated users can only POST to the sample index `_search` endpoint. Index mutation is isolated to the loader role |
| OpenSearch interception or storage disclosure | OpenSearch HTTPS with TLS 1.2 or later, encryption at rest, and node-to-node encryption |
| Malformed request headers | ALB invalid-header dropping |
| Accidental network exposure | Private ECS tasks without public IPs, isolated OpenSearch, source-security-group ingress, and loopback-only local port publishing |
| Sensitive debug output | Debug is false by default. UI errors are generic. Server logs can contain operational details and need appropriate handling |
| Supply-chain drift | Python packages imported directly by the frontend, loader, and CDK app are pinned to exact versions in the requirements files. The requirements files were scanned with `pip-audit` on 2026-10-22. The Python base image (the official image from Amazon ECR Public) is pinned by tag and digest. Transitive dependencies are not locked, no hashed lockfile exists, and the CDK CLI version is not pinned. New advisories can appear continuously, so pins need periodic review (see Residual risks) |
| Links or images injected into AI summaries | Indexed record text reaches the model prompt, so a crafted record could steer the model to emit a link or image for other users. The app renders every AI summary with `st.text`, so Markdown is never rendered, and the prompt asks for plain text |
| Real-person or listing data publication | Deterministic fictional records, visible synthetic-data notices, provenance documentation, and a strict validator (`scripts/validate_synthetic_data.py`) that operators run manually as the READMEs describe. Neither `deploy.sh` nor the loader runs it, and the loader only checks the record count |
| Undetected misuse | Logging is enabled for CloudFront, the ALB, ECS containers, and OpenSearch, with finite retention on the log groups and log bucket this app declares. The sample adds no alarms, metric filters, or WAF logs. OpenSearch slow logs need index thresholds that this sample does not set, so they are likely empty |
| Broad cleanup damage | Cleanup targets only the two exact CloudFormation stacks and does not delete shared bootstrap resources |

## Residual risks

- **Default viewer TLS is a non-passing security residual.** The generated `*.cloudfront.net` hostname uses CloudFront's provider-managed default certificate, which forces the legacy `TLSv1` viewer security policy. HTTPS redirection does not make this a TLS 1.2 minimum. Organizations requiring TLS 1.2 or later at the viewer boundary must use an authorized hostname, an issued `us-east-1` ACM certificate, and the paired deployment inputs. That path selects `TLSv1.2_2021`. Certificate authorization, issuance, and DNS remain customer responsibilities. Release security review must explicitly assess this residual.
- CloudFront-to-origin HTTP is accepted only within the VPC origin boundary. Organizations requiring end-to-end TLS need a private certificate and domain design. This origin consideration is separate from viewer TLS and OpenSearch TLS.
- The sample provides one OpenSearch data node and one NAT gateway, so it is not highly available. The ECS service has a desired count of 1 at rest, but autoscaling can raise it to 10 tasks on CPU, memory, and request count while OpenSearch stays at one node. Heavy or abusive traffic can therefore raise cost and load on that single node.
- The `maxTokens` caps on Nova 2 Lite calls are set in the app only, not in IAM. An authenticated user who calls Cognito directly with the app client and identity pool IDs receives the same temporary credentials outside the app. Those credentials can invoke Nova 2 Lite and Titan Text Embeddings V2 with any request size and rate up to account quotas, so Bedrock cost is bounded only by Bedrock service quotas and credential lifetime.
- Generated model responses can be inaccurate. Property data is fictional and unsuitable for identification or valuation decisions.
- Cognito password authentication remains exposed to online guessing through the public UI.
- The default outbound path uses NAT and is not restricted to exact service endpoints.
- Logs can contain search terms. The sample does not implement production log classification, redaction, archival, or alerting.
- OpenStreetMap tiles are an external availability and terms dependency.
- Streamlit WebSocket and proxy behavior requires live sandbox validation after deployment.
- The requirements files were scanned with `pip-audit` on 2026-10-22 with no known vulnerabilities in their resolved trees. The dependency set is not fully locked. Direct dependencies are pinned, but transitive dependencies resolve at build time, no hashed lockfile exists, and `deploy.sh` uses whichever AWS CDK CLI version is installed. New advisories can appear continuously, so pins need periodic review.
- The frontend signs in with the Cognito `USER_PASSWORD_AUTH` flow. The password reaches the Streamlit server as clear text inside the session and crosses the CloudFront-to-ALB HTTP hop inside the VPC origin. SRP is enabled on the user pool client but the app does not use it.
- Folium loads Leaflet, jQuery, Bootstrap, and Awesome Markers from public CDNs, and the map HTML that Folium 0.20.0 emits carries no integrity attributes for them.
- `deploy.sh` runs `cdk bootstrap` by default, which can touch shared bootstrap resources in the target account and Region. Operators can pass `--skip-bootstrap` for an already-bootstrapped environment.
- Authenticated users can search any OpenSearch index whose name starts with `real-estate-properties`. A bring-your-own index on that prefix is searchable by every authenticated user, as `opensearch-solution/data/README.md` describes.
- Until `create_demo_user_secure.py` runs, the demo-user secret holds a generated placeholder value that is not a Cognito credential.

## Production hardening gaps

Before adapting this sample for production, evaluate AWS WAF managed rules and rate limiting, Cognito MFA and threat protection, multi-AZ ECS and OpenSearch capacity, multi-AZ NAT or VPC endpoints, restricted egress, customer-managed encryption keys, backup and recovery, alarms and metric filters on application and access logs, WAF logging, OpenSearch slow-log thresholds on the index, incident response, governed log retention and redaction, a hashed dependency lockfile with a pinned CDK CLI version, image scanning in CI, running the synthetic-data validator from the build or loader, a lower autoscaling ceiling or request rate limits, SRP-based sign-in, self-hosted or integrity-checked map assets, a scoped bootstrap policy, vulnerability management, penetration testing, model safety controls, accessibility, privacy review, data authorization and provenance, and complete security, legal, regulatory, and compliance review.
