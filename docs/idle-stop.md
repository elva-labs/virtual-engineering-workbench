# Idle stop: decided in the hub, reported by the workbench

Builds on the project workbench lifecycle policy (`vew_project_workbench_lifecycle`): the effective
inactivity timeout per workbench (deployment default → project → user within the project's bounds,
`alwaysOn`, `AutoStopProtection`).

## How it works

1. **The workbench reports.** An idle agent on the workbench (`infra/usecase/resources/workbench_agents/idle/`)
   publishes every 5 minutes to CloudWatch in the workbench account, namespace `VEW/Workbench`,
   dimension `InstanceId`:
   - `DcvConnections` - connected DCV clients;
   - `InteractiveSessions` - SSH/console logins plus SSM Session Manager sessions;
   - `LoadPerCpu` - 5-minute load average per CPU;
   - `IdleMinutes` - the agent's own counter (informational).

   It does not stop anything (`ACTION=report`); `ACTION=stop` is a local fallback switch.
2. **The hub decides.** The provisioning scheduled job `WorkbenchLifecycleJob` action `idle-stop`
   (rule `<prefix>-provisioning-workbench-idle-stop-<env>`) reads each running workbench's signals
   through the provisioning role it already assumes in the workbench account
   (`cloudwatch:GetMetricData`), and stops the workbench through VEW's stop path once every counted
   signal stayed quiet for the whole timeout window. The stop is recorded with
   `lastUpdatedBy = VEWProvisioningBCIdleStop` and the reason in `statusReason`.
3. **Missing data never stops a workbench.** Gaps keep it running; a running workbench without any
   signal for `missingSignalAlarmMinutes` counts as `IdleSignalMissing` (metric and alarm
   `<prefix>-provisioning-idle-signal-missing-<env>`, part of the system-health composite alarm).
   Unreadable signals count as `IdleSignalReadFailed`.

## Configuration

`infra/config.py`, provisioning `workbench-lifecycle.idleStop` (off by default):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | run the job |
| `dryRun` | `false` | log and count (`IdleStopWouldStop`), stop nothing |
| `everyMinutes` | `5` | job interval |
| `signals` | all three | which signals count |
| `loadPerCpuThreshold` | `0.15` | busy at or above this load per CPU |
| `minCoverage` | `0.8` | share of 5-minute periods in the window that must have data |
| `graceMinutesAfterStart` | `15` | never stop a workbench that started less than this ago |
| `missingSignalAlarmMinutes` | `120` | running this long without any signal raises `IdleSignalMissing` |

## Delivering the agent

Opt-in per deployment: provisioning-enablement config `workbench-agents-enabled`. The account's
provisioning enablement stack then holds an SSM document (`<prefix>-provisioning-enablement-workbench-agents-<env>`,
a new version on every content change) and a State Manager association that runs it on every
workbench (tag `vew:provisionedProduct:productType = WORKBENCH`) at launch and every 30 minutes. It
writes the agent files to fixed paths and enables the timer; a copy baked into a workbench image is
only a bootstrap fallback, the association wins. The workbench instance role needs
`cloudwatch:PutMetricData` (the CloudWatch agent's usual permissions).

Changing the agent: edit the files, deploy, and re-onboard the accounts (project account
`onboardingRevision`). Changing the rules: edit the config and deploy - no image change for either.

## Trying it

Invoke the scheduled jobs function with
`{"jobName": "WorkbenchLifecycleJob", "parameters": {"action": "idle-stop", "dryRun": true}}`: the
response lists `stopped` (would stop), `kept` with the reason per workbench, `missing` and `failed`.
