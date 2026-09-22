# af-south-1 (Cape Town) constraints

The region is chosen deliberately: it keeps personal health information on
the African continent, which is the conservative reading of the Nigeria Data
Protection Act 2023 for a pilot whose data subjects are Nigerian. The price
of that choice is that af-south-1 is an opt-in region with a smaller service
catalogue than the majors, so every service this stack uses is listed here
with its verification status.

| Service | Used by | Status |
|---|---|---|
| VPC, EC2 (Graviton t4g), ASG, ALB | Network/App stacks | Available; verified by synth. Confirm t4g capacity at deploy. |
| RDS PostgreSQL 16 multi-AZ | Data stack | Available. |
| ElastiCache Redis with failover | Data stack | Available. |
| S3 with Object Lock | Data stack (audit bucket) | Available. |
| KMS, Secrets Manager, SSM Parameter Store | Data/App stacks | Available. |
| ECR | App stack | Available. |
| Cognito user pools | App stack | Available. **The Plus feature plan and FULL_FUNCTION threat protection must be confirmed against the current af-south-1 catalogue at first deploy — downgrade to ESSENTIALS if the deploy rejects it, and record the decision here.** |
| Cognito SMS MFA via SNS | App stack | SNS SMS starts in sandbox with a small spend cap; production access and a registered sender ID for Nigeria must be requested weeks before go-live. See the runbook. |
| WAFv2 REGIONAL with AWS managed rule groups | App stack | Available; managed rule group versions can lag other regions. |
| CloudWatch dashboards, alarms, SNS email | Observability stack | Available. |
| CloudFront | App stack (dashboard) | Global service; works with an af-south-1 bucket origin. A future custom domain needs its ACM certificate in us-east-1 — CloudFront only reads certificates there. |

Verification discipline: anything marked "must be confirmed" is confirmed by
an actual deploy to a scratch account, not by reading a service list, and
the row above is updated with the date and outcome. A constraint discovered
at pilot go-live is an outage; discovered here, it is a table edit.
