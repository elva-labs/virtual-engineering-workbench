# Spoke capacity: quotas, the capacity pages and quota requests

Off by default (`spoke-capacity` in `backend/infra/config.py`). When enabled, the hub reads every
enrolled spoke account's EC2 vCPU and gp3 quotas, and what uses them, every `everyMinutes` (10). It uses
the spoke's provisioning role, which the spoke app grants the Service Quotas reads,
`RequestServiceQuotaIncrease`, `ec2:DescribeInstanceTypes` and `ec2:DescribeVolumes`. Accounts onboarded
before this change need a re-onboarding for those permissions.

- **Launch check:** a launch whose instance type would not fit the account's remaining quota (minus
  `headroomVcpus`) is refused with a message that names the quota. It fails open: with no snapshot, a
  snapshot older than `staleAfterMinutes`, or an unreadable quota, the launch goes ahead.
- **Launch form:** next to the instance type, how many more workbenches of that size still fit.
- **Administration → Capacity (all programs):** platform admins see every account's quotas, use and
  workbenches, and can request an increase.
- **Administration → Capacity (the program):** program owners see their own program's accounts,
  read-only.

## The quotas

| Quota | What it counts |
|---|---|
| `L-1216C47A` Standard vCPU | pending and running On-Demand instances in the a, c, d, h, i, m, r, t, z families |
| `L-DB2E81BA` GPU vCPU (G, VT) | the g and vt families (0 by default in new accounts) |
| `L-417A185B` GPU vCPU (P) | the p family |
| `L-7A658B76` gp3 storage (TiB) | gp3 volumes |

Any instance in the account counts, not only workbenches. Stopped workbenches don't count, but need room
when they are started. The list is configurable (`quotas`).

## Requesting more

On the all-programs Capacity page, choose **Request increase** on an account's quota and enter the new
total. VEW files it with AWS Service Quotas through the spoke's provisioning role and records who asked and
when. Asking again for the same or less returns the open request; a value at or below the current quota is
refused (409). The status is refreshed by the collector. GPU requests usually open an AWS support case.

Over S2S (an operator action, no Terraform resource): scope `clients/provisioning/capacity.quota_increase`,
`PUT /provisioning/capacity/accounts/{awsAccountId}/quotas/{quotaCode}/increase` with
`{"desiredValue": 16, "region": "<region>"}`. `capacity.read` gives `GET /provisioning/capacity`.

## Alarms

`<prefix>-capacity-l-<code>` goes to ALARM when any spoke uses `alarmUsedPercent` (80) % or more of that
quota; the alarms join the provisioning stack's system-health composite alarm. `CapacityReadFailed`
(type `Capacity`) counts accounts whose EC2 or quotas could not be read.
