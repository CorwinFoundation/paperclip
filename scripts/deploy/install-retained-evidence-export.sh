#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root" >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -o root -g root -m 0755 "$SCRIPT_DIR/retained_evidence_export.py" /usr/local/sbin/backbond-retained-evidence-export
cat > /etc/sudoers.d/backbond-retained-evidence-export <<'EOF'
are-deploy ALL=(root) NOPASSWD: /usr/local/sbin/backbond-retained-evidence-export *
EOF
chmod 0440 /etc/sudoers.d/backbond-retained-evidence-export
visudo -cf /etc/sudoers.d/backbond-retained-evidence-export
