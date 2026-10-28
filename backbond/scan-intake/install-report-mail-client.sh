#!/bin/sh
# Source-only BAC client packaging. Does not install dependencies or change units.
set -eu
if [ "$(id -u)" -ne 0 ] || [ "$#" -ne 1 ]; then
  echo 'usage (root): install-report-mail-client.sh /absolute/existing/python' >&2
  exit 1
fi
REPORT_MAIL_PYTHON=$1
case "$REPORT_MAIL_PYTHON" in
  /*) ;;
  *) echo 'existing absolute Python path required' >&2; exit 1 ;;
esac
case "$REPORT_MAIL_PYTHON" in
  *[!a-zA-Z0-9/_.-]*) echo 'unsupported Python path' >&2; exit 1 ;;
esac
"$REPORT_MAIL_PYTHON" -c 'import httpx'
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -d -o root -g root -m 0755 /opt/backbond/report-mail
install -o root -g root -m 0644 "$HERE/report_mail_client.py" /opt/backbond/report-mail/report_mail_client.py
REPORT_MAIL_WRAPPER=$(mktemp)
trap 'rm -f "$REPORT_MAIL_WRAPPER"' EXIT HUP INT TERM
printf '#!/bin/sh\nexec "%s" /opt/backbond/report-mail/report_mail_client.py "$@"\n' "$REPORT_MAIL_PYTHON" > "$REPORT_MAIL_WRAPPER"
install -o root -g root -m 0755 "$REPORT_MAIL_WRAPPER" /usr/local/bin/backbond-report-mail
