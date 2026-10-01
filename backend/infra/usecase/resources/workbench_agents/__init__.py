"""Workbench agents delivered outside the image.

The spoke's SSM document installs these files on every workbench (State Manager association, by
tag). Changing a file here and re-onboarding the spoke (onboardingRevision) updates running
workbenches within the association's schedule - no new image. A copy in a workbench image is the
bootstrap fallback only; the association writes the same paths and wins.
"""

import base64
import hashlib
import pathlib

HERE = pathlib.Path(__file__).parent

# (source file, target path, mode)
IDLE_AGENT_FILES = [
    ("idle/vew-idle-agent.sh", "/usr/local/sbin/vew-idle-agent.sh", "0755"),
    ("idle/autostop.conf", "/etc/vew/autostop.conf", "0644"),
    ("idle/vew-idle-agent.service", "/etc/systemd/system/vew-idle-agent.service", "0644"),
    ("idle/vew-idle-agent.timer", "/etc/systemd/system/vew-idle-agent.timer", "0644"),
]


def content_version() -> str:
    """A short hash of every delivered file; written to /etc/vew/agents.version on the workbench."""
    digest = hashlib.sha256()
    for source, target, mode in IDLE_AGENT_FILES:
        digest.update(f"{target}:{mode}\n".encode())
        digest.update((HERE / source).read_bytes())
    return digest.hexdigest()[:12]


def install_script() -> list[str]:
    """The aws:runShellScript lines: write each file (base64, so content needs no quoting), then
    enable the timer. Idempotent: an unchanged file is not rewritten and no unit is restarted.

    POSIX sh: SSM runs the script with /bin/sh (dash on Ubuntu), which has no `pipefail`."""
    lines = [
        "set -eu",
        "install -d -m 0755 /etc/vew /var/lib/vew /var/log/vew /usr/local/sbin",
        "changed=0",
    ]
    for source, target, mode in IDLE_AGENT_FILES:
        encoded = base64.b64encode((HERE / source).read_bytes()).decode()
        lines += [
            f"tmp=$(mktemp); echo '{encoded}' | base64 -d > \"$tmp\"",
            f"if ! cmp -s \"$tmp\" '{target}'; then install -m {mode} \"$tmp\" '{target}'; changed=1; fi",
            'rm -f "$tmp"',
        ]
    lines += [
        f"echo '{content_version()}' > /etc/vew/agents.version",
        '[ "$changed" = 1 ] && systemctl daemon-reload',
        "systemctl enable --now vew-idle-agent.timer",
        'echo "workbench agents $(cat /etc/vew/agents.version) (changed=$changed)"',
    ]
    return lines
