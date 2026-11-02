# Real Estate Semantic Search

This is a full-stack, non-production sample for searching fictional property records with natural language, vector similarity, and geospatial filters.

This is sample code for non-production use. Work with your security and legal teams to meet your organizational security, regulatory, and compliance requirements before deployment.

Users initially see 80 fictional records across seven cities. A visible application notice identifies them as synthetic demonstration data. Do not use this sample or its records for property identification or valuation decisions.

## Architecture and authentication

```text
Browser
  | HTTPS, viewer policy depends on certificate path
  v
CloudFront distribution, no caching
  | private VPC origin connection over HTTP
  v
Internal Application Load Balancer
  | private application subnets
  v
Streamlit on ECS Fargate, no public IP
  | Cognito-vended temporary AWS credentials
  +--> Cognito user and identity pools
  +--> OpenSearch in isolated subnets over HTTPS with TLS 1.2+
  +--> Bedrock: Titan Text Embeddings V2 and Nova 2 Lite
  +--> Amazon Location Service

One-off private ECS loader task --> OpenSearch and Bedrock Titan V2
```

CloudFront is the only public application entry point. The Application Load Balancer is internal and accepts port 80 only from the AWS-managed CloudFront origin-facing prefix list. CloudFront VPC origins provide the private origin boundary. The private CloudFront-to-ALB hop uses HTTP. That origin transport is distinct from browser viewer TLS and OpenSearch HTTPS.

Streamlit CORS and XSRF protections remain enabled. The ECS task receives the exact public hostname as Streamlit's browser server address, which supports CloudFront WebSocket upgrades without trusting a viewer-provided host.

Cognito is the user authentication mechanism. The Streamlit task has no AWS data-plane permissions. After login, the app exchanges the Cognito token for temporary credentials scoped to search, the exact Bedrock models, and the Location index. It does not load the optional demo password.

OpenSearch enforces HTTPS with TLS 1.2 or later, encryption at rest, node-to-node encryption, and security-group access only from frontend and loader tasks. The loader owns index mutation permissions. Authenticated users can only POST to the sample index `_search` endpoint.

## Security considerations

- The private CloudFront-to-ALB VPC-origin HTTP hop is a non-production trade-off. End-to-end TLS requires a private domain, ACM or private certificate, HTTPS listener, and HTTPS-only origin policy.
- The default `*.cloudfront.net` certificate uses the legacy `TLSv1` viewer policy and does not enforce TLS 1.2. See [Default generated hostname](#default-generated-hostname).
- The app uses Cognito `USER_PASSWORD_AUTH`. SRP is a stronger production choice.

## CloudFront viewer TLS paths

### Default generated hostname

With no hostname or certificate inputs, the application uses the generated `*.cloudfront.net` hostname. CloudFront redirects HTTP viewer requests to HTTPS. Its provider-managed default certificate forces the legacy `TLSv1` viewer security policy, so the default endpoint does **not** enforce TLS 1.2. This is a non-passing residual security consideration, not a successful TLS 1.2 control. Certificate management is customer-controlled.

### Customer-managed hostname and certificate

Organizations requiring TLS 1.2 or later at the viewer boundary must supply both:

- a hostname they are authorized to use
- an issued ACM certificate in `us-east-1` that covers that hostname and belongs to the active AWS account

The paired path configures the CloudFront alias, SNI, and `TLSv1.2_2021` policy. The deploy script performs read-only STS and ACM preflight checks for Region, account ownership, `ISSUED` status, and exact or one-label wildcard hostname coverage before bootstrap or deployment.

The sample does not request a certificate, create a hosted zone, or create DNS. After deployment, point the authorized hostname to the `CloudFrontDistributionDomainName` output with Route 53 or another DNS provider. The output is the distribution target. `RealEstateSemanticSearchApplicationURL` is the URL users open.

## Data provenance

The `data/` directory contains exactly 80 generated fictional records:

| City | Records |
|---|---:|
| Atlanta | 15 |
| Chicago | 15 |
| Cupertino | 2 |
| Frisco | 3 |
| Miami | 15 |
| Oakland | 15 |
| Seattle | 15 |

Addresses use obvious fictional markers. Coordinates are coarse deterministic jitter near city centroids. The data is not from an MLS, property portal, broker, or person, and must not be used for valuation decisions.

Regenerate and validate it with:

```bash
python3 scripts/generate_synthetic_data.py
python3 scripts/validate_synthetic_data.py
```

The JSON excludes embeddings. The private loader generates 1024-dimensional Titan Text Embeddings V2 vectors at load time. See [the data guide](data/README.md) before using an authorized replacement dataset. It covers the schema, provenance responsibilities, `lat,lon` coordinates, separate index loading, validation, and the sample's inability to verify factual authenticity.

## Prerequisites

- Python 3.11 or later
- Node.js and the AWS CDK CLI 2.1144.0 or later
- AWS CLI v2
- Docker for building the two ECS images
- AWS CLI credentials with permission to bootstrap and deploy the listed services
- Access to `amazon.titan-embed-text-v2:0` in the deployment Region
- Access to the `us.amazon.nova-2-lite-v1:0` cross-Region inference profile
- For the customer-managed viewer path, an authorized hostname and issued matching ACM certificate in `us-east-1`

Nova 2 Lite uses a US geographic cross-Region inference profile. Confirm that the profile and all destination Regions it may route to meet your data residency requirements. Service and model availability varies by Region.

## Deploy

From this directory, choose one path.

Default generated hostname with the documented viewer-TLS residual:

```bash
./deploy.sh --region us-east-1
```

Customer-managed viewer hostname with TLS 1.2 or later enforced by `TLSv1.2_2021`:

```bash
./deploy.sh --region us-east-1 \
  --domain-name search.example.com \
  --cloudfront-certificate-arn arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000
```

Exactly one of the paired options is rejected before synthesis. The second path runs read-only certificate preflight before any bootstrap or deployment command. Both paths output the application URL and generated CloudFront DNS target. Create customer-managed DNS separately when using a custom hostname.

The script deploys `RealEstateSearchStack` and `RealEstateECSFargateStack`. It can then run the private ECS data loader and create an optional Cognito demo user. The demo-user script prints the Secrets Manager secret ARN and a retrieval command, but never displays the password. Enter both login fields manually.

Useful options:

```bash
./deploy.sh --region us-east-1 --backend-only
./deploy.sh --region us-east-1 --frontend-only
./deploy.sh --region us-east-1 --skip-setup
./deploy.sh --region us-east-1 --skip-bootstrap
```

By default the script runs `cdk bootstrap`, which can update the shared CDK bootstrap stack in the account and Region. Use `--skip-bootstrap` when the environment is already bootstrapped and those shared resources should not be touched.

Deployments created from earlier versions of this sample cannot be updated in place, because CloudFormation cannot move an existing public OpenSearch domain into a VPC. Run `./teardown.sh` before deploying this version.

The frontend-only option still synthesizes the backend stack because the frontend references its resources. CDK deploys only the selected stack and its required dependencies.

## Local frontend

Local development requires identifiers and an OpenSearch endpoint from a deployed sandbox. Cognito still handles login and issues temporary credentials. The local runner binds only to loopback:

```bash
cd frontend
export AWS_REGION=us-east-1
export OPENSEARCH_ENDPOINT=https://<private-endpoint>
export COGNITO_USER_POOL_ID=<user-pool-id>
export COGNITO_APP_CLIENT_ID=<client-id>
export COGNITO_IDENTITY_POOL_ID=<identity-pool-id>
export PLACE_INDEX_NAME=<place-index-name>
./run-dev.sh
```

A workstation normally cannot reach private OpenSearch. Use local mode only from an approved private network path. Docker Compose publishes `127.0.0.1:8080` and does not mount host AWS credentials.

## Costs and operational limits

Charges can accrue for OpenSearch, NAT Gateway hourly and data processing, ECS Fargate, the Application Load Balancer, CloudFront, S3 logs, CloudWatch logs, Bedrock inference, Cognito, and Location Service. The sample uses one NAT gateway to limit demo cost. That is a single-Availability-Zone egress trade-off and not a high-availability design. Consider VPC endpoints and an egress design suited to production requirements.

CloudFront and ALB access logs expire after 30 days. OpenSearch application and slow logs, plus ECS logs, are retained for one week. The sample drops malformed ALB headers. It does not deploy AWS WAF because fixed cost, rule tuning, and CloudFront scope requirements are disproportionate for the default demonstration. Production designs should evaluate WAF rate limits, threat protection, MFA, alarms, longer governed retention, multi-AZ egress, restricted outbound access, penetration testing, and organizational review.

Validate WebSocket and proxy behavior in a sandbox after deployment.

## Third-party services and software

The map uses OpenStreetMap tiles through Folium. Use is subject to the [OpenStreetMap copyright and license terms](https://www.openstreetmap.org/copyright) and the [OpenStreetMap tile usage policy](https://operations.osmfoundation.org/policies/tiles/). Tile availability and terms are controlled by that third-party service. Review them before use and do not assume a production service-level commitment.

`streamlit-folium` is MIT licensed. Python dependencies are installed from their package distributions. No third-party source or assets are vendored, so this repository does not include a separate `THIRD-PARTY-LICENSES` file.

## Cleanup

Cleanup is destructive. Confirm the account and Region shown by the script:

```bash
./teardown.sh --region us-east-1
```

The script deletes only `RealEstateECSFargateStack`, then `RealEstateSearchStack`, and waits for each exact stack. It does not remove shared CDK bootstrap repositories or asset buckets.

## Publication check

Publish from a fresh, single-commit repository snapshot. Do not transfer development Git history. Before making the snapshot public, scan the working tree and full snapshot history for credentials, account identifiers, internal links, and customer information.

## License and contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md), [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md), and the [MIT-0 license](../LICENSE).
