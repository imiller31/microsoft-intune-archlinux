#!/bin/bash
set -euo pipefail

if [[ $EUID -ne 0 ]] || [[ ! -f /etc/arch-intune-e2e ]] \
    || [[ $(cat /etc/arch-intune-e2e) != disposable-github-actions-vm ]] \
    || ! systemd-detect-virt --vm --quiet; then
    echo "Provisioning is restricted to the disposable test VM." >&2
    exit 1
fi

pacman -Syu --noconfirm --needed \
    base-devel git python sudo openssl nss opensc lsb-release \
    xorg-server-xvfb xorg-xauth xorg-xsetroot xdotool mesa \
    xfce4-session xfwm4 gnome-keyring dbus

uid=$(id -u tester)
loginctl enable-linger tester
systemctl start "user@$uid.service"

as_user() {
    runuser -u tester -- env HOME=/home/tester \
        "XDG_RUNTIME_DIR=/run/user/$uid" \
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus" \
        DISPLAY=:99 XDG_CURRENT_DESKTOP=XFCE "$@"
}

install -d -o tester -g tester -m 700 \
    /home/tester/.pki /home/tester/.pki/nssdb /home/tester/.e2e-fixture \
    /home/tester/.config /home/tester/.config/environment.d
cat > /home/tester/.config/environment.d/arch-e2e.conf <<'EOF'
DISPLAY=:99
XDG_CURRENT_DESKTOP=XFCE
EOF
chown tester:tester /home/tester/.config/environment.d/arch-e2e.conf

cat > /etc/opensc.conf <<'EOF'
# Synthetic pre-existing policy: setup must preserve this file.
app default {
    framework pkcs15 {
        use_pin_cache = false;
    }
}
EOF
install -d /etc/systemd/user/microsoft-identity-broker.service.d
cat > /etc/systemd/user/microsoft-identity-broker.service.d/preserve.conf <<'EOF'
[Service]
Environment=ARCH_E2E_SENTINEL=preserve
EOF

as_user certutil -N --empty-password -d sql:/home/tester/.pki/nssdb
as_user openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
    -subj '/CN=Disposable VM synthetic fixture' \
    -keyout /home/tester/.e2e-fixture/private.key \
    -out /home/tester/.e2e-fixture/certificate.pem 2>/dev/null
as_user certutil -A -n arch-e2e-fixture -t C,, \
    -d sql:/home/tester/.pki/nssdb -i /home/tester/.e2e-fixture/certificate.pem

cat > /etc/systemd/system/arch-e2e-display.service <<'EOF'
[Unit]
Description=Disposable test X11 display
[Service]
User=tester
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x800x24 -nolisten tcp -ac
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/user/arch-e2e-desktop.service <<'EOF'
[Unit]
Description=Disposable test desktop
BindsTo=graphical-session.target
Before=graphical-session.target
StartLimitIntervalSec=0
[Service]
Environment=DISPLAY=:99
Environment=XDG_CURRENT_DESKTOP=XFCE
ExecStart=/usr/bin/xfce4-session
Restart=on-failure
RestartSec=2
[Install]
WantedBy=default.target
EOF

systemctl daemon-reload
systemctl enable --now arch-e2e-display.service
as_user systemctl --user daemon-reload
as_user systemctl --user set-environment DISPLAY=:99 XDG_CURRENT_DESKTOP=XFCE
as_user dbus-update-activation-environment --systemd DISPLAY XDG_CURRENT_DESKTOP
as_user systemctl --user enable --now arch-e2e-desktop.service
