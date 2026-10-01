#!/usr/bin/env bash
# VEW idle agent: one run every 5 minutes (vew-idle-agent.timer). It reports the
# workbench's activity signals to CloudWatch (namespace VEW/Workbench, dimension InstanceId); the hub
# decides idleness from them and stops the workbench through VEW. The agent does not stop anything
# itself unless ACTION=stop (the old in-image behaviour, kept as a fallback switch).
#
# Delivered by the spoke's SSM State Manager association (<prefix>-provisioning-enablement-workbench-agents-<env>); the copy
# in a workbench image (if any) is only the bootstrap fallback. Safe to run by hand:
#   VEW_IDLE_DRY_RUN=1 vew-idle-agent.sh
set -euo pipefail
conf=${VEW_AUTOSTOP_CONF:-/etc/vew/autostop.conf}
state=${VEW_IDLE_STATE:-/var/lib/vew/idle-minutes}
interval_min=5
ENABLED=true IDLE_MINUTES=60 LOAD_THRESHOLD=0.15 ACTION=report METRICS_NAMESPACE=VEW/Workbench
# shellcheck source=autostop.conf
[ -f "$conf" ] && . "$conf"
log() { echo "$(date -u +%FT%TZ) $*"; }

imds() {  # IMDSv2 metadata path; empty output when IMDS (or the path) is unavailable
  local token
  token=$(curl -fsS -m 2 -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token 2>/dev/null) || return 0
  curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $token" "http://169.254.169.254/latest/meta-data/$1" 2>/dev/null || true
}

# The hub's effective timeout (vew:autostop) only matters for the local fallback.
override=$(imds "tags/instance/vew:autostop")
case "$override" in
  off|false|disabled) ENABLED=false ;;
  ''|*[!0-9]*) ;;
  *) IDLE_MINUTES=$override ;;
esac

# Signals ---------------------------------------------------------------------------------------
dcv_connections=0
if command -v dcv >/dev/null 2>&1; then
  for s in $(dcv list-sessions 2>/dev/null | sed -n "s/^Session: '\([^']*\)'.*/\1/p"); do
    n=$(dcv list-connections "$s" 2>/dev/null | grep -c . || true)
    dcv_connections=$((dcv_connections + n))
  done
fi
# Interactive shells: SSH/console logins and SSM Session Manager sessions (not RunCommand).
logins=$(who 2>/dev/null | grep -c . || true)
ssm_sessions=$(pgrep -cx ssm-session-worker 2>/dev/null || true)
interactive_sessions=$((logins + ${ssm_sessions:-0}))
cpus=$(nproc)
load5=$(cut -d' ' -f2 "${VEW_LOADAVG:-/proc/loadavg}")
load_per_cpu=$(awk -v l="$load5" -v c="$cpus" 'BEGIN{printf "%.3f", l/c}')

busy_reason=""
[ "$dcv_connections" -gt 0 ] && busy_reason="dcv clients: $dcv_connections"
[ -z "$busy_reason" ] && [ "$interactive_sessions" -gt 0 ] && busy_reason="interactive sessions: $interactive_sessions"
if [ -z "$busy_reason" ] && awk -v l="$load_per_cpu" -v t="$LOAD_THRESHOLD" 'BEGIN{exit !(l >= t)}'; then
  busy_reason="load per cpu $load_per_cpu"
fi

idle=$(cat "$state" 2>/dev/null || echo 0)
if [ -n "$busy_reason" ]; then idle=0; else idle=$((idle + interval_min)); fi
echo "$idle" > "$state"
log "dcv=$dcv_connections sessions=$interactive_sessions load/cpu=$load_per_cpu idle=${idle}m action=$ACTION ${busy_reason:+busy: $busy_reason}"

if command -v aws >/dev/null 2>&1 && [ -z "${VEW_IDLE_DRY_RUN:-}" ]; then
  instance_id=$(imds instance-id)
  if [ -n "$instance_id" ]; then
    dims="[{\"Name\":\"InstanceId\",\"Value\":\"$instance_id\"}]"
    data="[
      {\"MetricName\":\"DcvConnections\",\"Value\":$dcv_connections,\"Unit\":\"Count\",\"Dimensions\":$dims},
      {\"MetricName\":\"InteractiveSessions\",\"Value\":$interactive_sessions,\"Unit\":\"Count\",\"Dimensions\":$dims},
      {\"MetricName\":\"LoadPerCpu\",\"Value\":$load_per_cpu,\"Unit\":\"None\",\"Dimensions\":$dims},
      {\"MetricName\":\"IdleMinutes\",\"Value\":$idle,\"Unit\":\"None\",\"Dimensions\":$dims}
    ]"
    aws cloudwatch put-metric-data --namespace "$METRICS_NAMESPACE" --metric-data "$data" >/dev/null 2>&1 \
      || log "could not publish the signals"
  fi
fi

# Local fallback only: the hub decides.
if [ "$ACTION" = stop ] && [ "$ENABLED" = true ] && [ "$idle" -ge "$IDLE_MINUTES" ]; then
  log "idle for ${idle} minutes - stopping the instance (local fallback, ACTION=stop)"
  [ -n "${VEW_IDLE_DRY_RUN:-}" ] && { log "dry run: not stopping"; exit 0; }
  echo 0 > "$state"
  shutdown -h +1 "VEW autostop: idle for ${idle} minutes" || true
fi
