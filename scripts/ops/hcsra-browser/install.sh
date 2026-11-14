#!/bin/sh
# BEAAA-46811: Ari-only interactive browser for www.hcsra.co.il (Radware hCaptcha solved by hand).
# Idempotent. Run as root on AriClaw. Everything listens on 127.0.0.1 only.
#   hcsra-xvfb.service     Xvfb :77 (no TCP)
#   hcsra-browser.service  headed Chrome, persistent profile, CDP on 127.0.0.1:9333
#   hcsra-vnc.service      x11vnc on 127.0.0.1:5977, password file auth (stop after Ari's solve)
set -eu
U=beai-agent
BASE=/home/$U/.hcsra-browser
DISPLAY_NUM=77
CDP_PORT=9333
VNC_PORT=5977
TARGET=https://www.hcsra.co.il/

command -v Xvfb >/dev/null && command -v x11vnc >/dev/null || \
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends xvfb x11vnc >/dev/null

install -d -m 0700 -o $U -g $U "$BASE" "$BASE/profile"
if [ ! -s "$BASE/vnc.txt" ]; then
  umask 077
  openssl rand -hex 6 > "$BASE/vnc.txt"
  x11vnc -storepasswd "$(cat "$BASE/vnc.txt")" "$BASE/vnc.pass" >/dev/null 2>&1
  chown $U:$U "$BASE/vnc.txt" "$BASE/vnc.pass"; chmod 0600 "$BASE/vnc.txt" "$BASE/vnc.pass"
fi

cat > /etc/systemd/system/hcsra-xvfb.service <<UNIT
[Unit]
Description=BEAAA-46811 Xvfb display :$DISPLAY_NUM for hcsra browser
[Service]
User=$U
ExecStart=/usr/bin/Xvfb :$DISPLAY_NUM -screen 0 1366x900x24 -nolisten tcp -nolisten unix -nolisten local -listen unix
Restart=on-failure
UNIT
# Xvfb rejects some -nolisten combos on older builds; keep the plain safe form.
sed -i "s|^ExecStart=.*|ExecStart=/usr/bin/Xvfb :$DISPLAY_NUM -screen 0 1366x900x24 -nolisten tcp|" /etc/systemd/system/hcsra-xvfb.service

cat > /etc/systemd/system/hcsra-browser.service <<UNIT
[Unit]
Description=BEAAA-46811 headed Chrome for hcsra (CDP 127.0.0.1:$CDP_PORT)
Requires=hcsra-xvfb.service
After=hcsra-xvfb.service
[Service]
User=$U
Environment=DISPLAY=:$DISPLAY_NUM
ExecStartPre=/bin/sleep 1
ExecStart=/usr/bin/google-chrome --user-data-dir=$BASE/profile --remote-debugging-address=127.0.0.1 --remote-debugging-port=$CDP_PORT --no-first-run --no-default-browser-check --password-store=basic --window-position=0,0 --window-size=1366,900 $TARGET
Restart=on-failure
UNIT

cat > /etc/systemd/system/hcsra-vnc.service <<UNIT
[Unit]
Description=BEAAA-46811 Ari-only VNC view of display :$DISPLAY_NUM (127.0.0.1:$VNC_PORT)
Requires=hcsra-xvfb.service
After=hcsra-xvfb.service
[Service]
User=$U
ExecStart=/usr/bin/x11vnc -display :$DISPLAY_NUM -localhost -rfbport $VNC_PORT -rfbauth $BASE/vnc.pass -forever -shared -noxdamage -quiet
Restart=on-failure
UNIT

systemctl daemon-reload
systemctl enable --now hcsra-xvfb.service hcsra-browser.service >/dev/null 2>&1
[ "${NO_VNC:-0}" = 1 ] || systemctl start hcsra-vnc.service
