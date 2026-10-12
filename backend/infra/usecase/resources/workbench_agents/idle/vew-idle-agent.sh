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
# No DCV CLI is an optional, absent signal source. An installed CLI that cannot observe its
# sessions/connections is unknown: omit its metric, never turn a probe failure into zero clients.
dcv_connections=0 dcv_probe=absent
if command -v dcv >/dev/null 2>&1; then
  dcv_probe=ok
  if session_output=$(dcv list-sessions 2>/dev/null); then
    # Support both the quoted session ids used by older CLIs and the documented unquoted format.
    # Reject other nonempty output instead of silently treating an unrecognized listing as idle.
    if sessions=$(printf '%s\n' "$session_output" | awk -v quote="'" '
      NF {
        line=$0
        sub(/^[[:space:]]+/, "", line)
        if (line !~ /^Session:[[:space:]]+/) exit 1
        sub(/^Session:[[:space:]]+/, "", line)
        if (substr(line, 1, 1) == quote) {
          closing_quote=index(substr(line, 2), quote)
          if (!closing_quote) exit 1
          id=substr(line, 2, closing_quote-1)
          rest=substr(line, closing_quote+2)
        } else {
          if (!match(line, /^[^[:space:]()]+/)) exit 1
          id=substr(line, RSTART, RLENGTH)
          rest=substr(line, RLENGTH+1)
        }
        if (!length(id) || rest !~ /^[[:space:]]+\(owner:/) exit 1
        print id
      }'); then
      while IFS= read -r session; do
        [ -z "$session" ] && continue
        if connections=$(dcv list-connections "$session" 2>/dev/null); then
          # Empty successful output proves zero. Any nonempty connection output is conservatively
          # active (including unfamiliar output), so changes in CLI formatting cannot prove idle.
          n=$(printf '%s\n' "$connections" | awk 'NF {n++} END {print n+0}')
          dcv_connections=$((dcv_connections + n))
        else
          dcv_probe=failed
          break
        fi
      done <<< "$sessions"
    else
      dcv_probe=failed
    fi
  else
    dcv_probe=failed
  fi
  [ "$dcv_probe" = failed ] && dcv_connections=unknown
fi
# Interactive shells: SSH/console logins and SSM Session Manager workers, never RunCommand.
interactive_sessions=unknown
if logins_output=$(who 2>/dev/null); then
  logins=$(printf '%s\n' "$logins_output" | awk 'NF {n++} END {print n+0}')
  # Linux comm is limited to 15 characters; the 18-character worker name requires full-command
  # matching. Anchor the executable token so pgrep itself, shell probes and document workers cannot
  # count as interactive sessions. pgrep's exit 1 means a healthy zero; other failures are unknown.
  pgrep_status=0
  ssm_sessions=$(pgrep -fc '^([^[:space:]]*/)?ssm-session-worker([[:space:]]|$)' 2>/dev/null) || pgrep_status=$?
  if [ "$pgrep_status" -le 1 ] && [[ "$ssm_sessions" =~ ^[0-9]+$ ]]; then
    interactive_sessions=$((logins + ssm_sessions))
  fi
fi
cpus=$(nproc)
load5=$(cut -d' ' -f2 "${VEW_LOADAVG:-/proc/loadavg}")
load_per_cpu=$(awk -v l="$load5" -v c="$cpus" 'BEGIN{printf "%.3f", l/c}')

busy_reason=""
if [ "$dcv_connections" = unknown ]; then
  busy_reason="DCV observation unavailable"
elif [ "$dcv_connections" -gt 0 ]; then
  busy_reason="dcv clients: $dcv_connections"
fi
if [ -z "$busy_reason" ]; then
  if [ "$interactive_sessions" = unknown ]; then
    busy_reason="interactive observation unavailable"
  elif [ "$interactive_sessions" -gt 0 ]; then
    busy_reason="interactive sessions: $interactive_sessions"
  fi
fi
if [ -z "$busy_reason" ] && awk -v l="$load_per_cpu" -v t="$LOAD_THRESHOLD" 'BEGIN{exit !(l >= t)}'; then
  busy_reason="load per cpu $load_per_cpu"
fi

idle=$(cat "$state" 2>/dev/null || echo 0)
if [ -n "$busy_reason" ]; then idle=0; else idle=$((idle + interval_min)); fi
echo "$idle" > "$state"
log "dcv=$dcv_connections dcv_probe=$dcv_probe sessions=$interactive_sessions load/cpu=$load_per_cpu idle=${idle}m action=$ACTION ${busy_reason:+busy: $busy_reason}"

if command -v aws >/dev/null 2>&1 && [ -z "${VEW_IDLE_DRY_RUN:-}" ]; then
  instance_id=$(imds instance-id)
  if [ -n "$instance_id" ]; then
    dims="[{\"Name\":\"InstanceId\",\"Value\":\"$instance_id\"}]"
    data=""
    add_metric() {
      data="${data}${data:+,}{\"MetricName\":\"$1\",\"Value\":$2,\"Unit\":\"$3\",\"Dimensions\":$dims}"
    }
    [ "$dcv_connections" != unknown ] && add_metric DcvConnections "$dcv_connections" Count
    [ "$interactive_sessions" != unknown ] && add_metric InteractiveSessions "$interactive_sessions" Count
    add_metric LoadPerCpu "$load_per_cpu" None
    if [ "$dcv_connections" != unknown ] && [ "$interactive_sessions" != unknown ]; then
      add_metric IdleMinutes "$idle" None
    fi
    data="[$data]"
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
