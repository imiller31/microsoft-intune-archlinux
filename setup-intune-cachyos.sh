#!/bin/bash
# Microsoft Intune setup script for CachyOS / Arch Linux
# Tested on CachyOS with YubiKey 5 NFC FIPS
# Requires: sudo access, internet connection
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"

echo "=== Microsoft Intune + Azure VPN Setup for CachyOS ==="
echo ""

# ── Step 1: Install system dependencies ──────────────────────────────────────
echo "[1/11] Installing system dependencies..."
sudo pacman -Sy --noconfirm --needed \
    webkit2gtk webkit2gtk-4.1 opensc bubblewrap \
    gnome-keyring libsecret seahorse \
    pcsclite yubikey-manager nss zenity

# ── Step 2: Build and install broker + intune-portal ─────────────────────────
echo "[2/11] Building and installing microsoft-identity-broker..."
(cd "$REPO_DIR/microsoft-identity-broker" && makepkg -si --noconfirm)

echo "       Building and installing intune-portal..."
(cd "$REPO_DIR/intune-portal" && makepkg -si --noconfirm)

# ── Step 3: Create broker storage directories ────────────────────────────────
echo "[3/11] Creating broker storage directories..."
sudo mkdir -p /etc/microsoft/identity-broker/{certs,private}
sudo chmod 700 /etc/microsoft/identity-broker/private
sudo mkdir -p /var/opt/microsoft/identity-broker

# ── Step 4: Fake Ubuntu os-release (bind-mounted, real file untouched) ───────
echo "[4/11] Setting up Ubuntu os-release for Intune..."
sudo tee /etc/os-release.ubuntu > /dev/null << 'EOF'
PRETTY_NAME="Ubuntu 24.04.2 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.2 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
EOF

# Systemd drop-ins: bind-mount fake os-release into service namespaces
for unit_dir in \
    /etc/systemd/system/microsoft-identity-device-broker.service.d \
    /etc/systemd/system/intune-daemon.service.d \
    /etc/systemd/user/intune-agent.service.d; do
    sudo mkdir -p "$unit_dir"
    sudo tee "$unit_dir/os-release.conf" > /dev/null << 'EOF'
[Service]
BindReadOnlyPaths=/etc/os-release.ubuntu:/etc/os-release
EOF
done

# Wrapper for intune-portal: uses bwrap for unprivileged bind mount
if [ -L /usr/bin/intune-portal ]; then
    # First install: move symlink aside
    sudo mv /usr/bin/intune-portal /usr/bin/intune-portal.real
elif [ -f /usr/bin/intune-portal ] && ! grep -q bwrap /usr/bin/intune-portal 2>/dev/null; then
    sudo mv /usr/bin/intune-portal /usr/bin/intune-portal.real
fi

sudo tee /usr/bin/intune-portal > /dev/null << 'WRAPPER'
#!/bin/bash
export WEBKIT_DISABLE_DMABUF_RENDERER=1
export GDK_BACKEND=x11
exec bwrap \
  --dev-bind / / \
  --ro-bind /etc/os-release.ubuntu /etc/os-release \
  /usr/bin/intune-portal.real "$@"
WRAPPER
sudo chmod +x /usr/bin/intune-portal

# ── Step 5: Disable lsb_release (contradicts fake os-release) ────────────────
echo "[5/11] Disabling lsb_release..."
if [ -f /usr/bin/lsb_release ] && [ ! -f /usr/bin/lsb_release.backup ]; then
    sudo mv /usr/bin/lsb_release /usr/bin/lsb_release.backup
    echo "       Moved to /usr/bin/lsb_release.backup"
else
    echo "       Already handled"
fi

# ── Step 6: Wayland workaround ──────────────────────────────────────────────
echo "[6/11] Configuring Wayland workarounds..."
if ! grep -q 'WEBKIT_DISABLE_DMABUF_RENDERER' /etc/environment 2>/dev/null; then
    echo 'WEBKIT_DISABLE_DMABUF_RENDERER="1"' | sudo tee -a /etc/environment
    echo "       Added WEBKIT_DISABLE_DMABUF_RENDERER to /etc/environment"
else
    echo "       WEBKIT_DISABLE_DMABUF_RENDERER already set"
fi

# ── Step 7: PAM configuration for Intune compliance ──────────────────────────
echo "[7/11] Setting up PAM common-password for Intune compliance..."
if [ ! -f /etc/pam.d/common-password ]; then
    sudo tee /etc/pam.d/common-password > /dev/null << 'EOF'
#
# /etc/pam.d/common-password - password-related modules common to all services
#
# This file is included from other service-specific PAM config files,
# and should contain a list of modules that define the services to be
# used to change user passwords.

# here are the per-package modules (the "Primary" block)
password	requisite			pam_pwquality.so retry=3 dcredit=-1 lcredit=-1 minlen=12 ocredit=-1 ucredit=-1
password	optional pam_intune.so
password	[success=2 default=ignore]	pam_unix.so obscure use_authtok try_first_pass yescrypt
password	sufficient			pam_sss.so use_authtok
# here's the fallback if no module succeeds
password	requisite			pam_deny.so
# prime the stack with a positive return value if there isn't one already;
# this avoids us returning an error just because nothing sets a success code
# since the modules above will each just jump around
password	required			pam_permit.so
# and here are more per-package modules (the "Additional" block)
password	optional	pam_gnome_keyring.so
# end of pam-auth-update config
EOF
    echo "       Created /etc/pam.d/common-password"
else
    echo "       /etc/pam.d/common-password already exists, skipping"
    echo "       Verify it includes pam_intune.so and pam_pwquality.so"
fi

# ── Step 8: YubiKey / SmartCard / PRMFA setup ────────────────────────────────
echo "[8/11] Setting up YubiKey/SmartCard support..."
sudo systemctl enable --now pcscd

# NSS database for certificate-based auth
NSSDB="$HOME/.pki/nssdb"
mkdir -p "$NSSDB"
chmod 700 "$HOME/.pki"
chmod 700 "$NSSDB"

OPENSC_LIB=$(find /usr/lib -name 'opensc-pkcs11.so' -print -quit 2>/dev/null)
if [ -z "$OPENSC_LIB" ]; then
    echo "       WARNING: opensc-pkcs11.so not found!"
else
    modutil -force -create -dbdir "sql:$NSSDB" 2>/dev/null || true
    if ! modutil -dbdir "sql:$NSSDB" -list 2>/dev/null | grep -q "SC Module"; then
        modutil -force -dbdir "sql:$NSSDB" -add 'SC Module' -libfile "$OPENSC_LIB"
        echo "       Added SC Module to NSS database"
    else
        echo "       SC Module already in NSS database"
    fi
fi

# ── Step 8b: Force X11 backend for broker GTK dialogs (Wayland PIN fix) ─────
# The broker uses gtk_dialog_run() for the smartcard PIN prompt, which
# malfunctions under Hyprland/Wayland causing an infinite prompt loop.
echo "       Configuring GDK_BACKEND=x11 for identity broker..."
BROKER_DROP_IN="/etc/systemd/user/microsoft-identity-broker.service.d"
sudo mkdir -p "$BROKER_DROP_IN"
sudo tee "$BROKER_DROP_IN/wayland-fix.conf" > /dev/null << 'EOF'
[Service]
Environment=GDK_BACKEND=x11
Environment=WEBKIT_DISABLE_DMABUF_RENDERER=1
EOF

# ── Step 9: Enable services and reload ───────────────────────────────────────
echo "[9/11] Enabling services..."
sudo systemctl daemon-reload
systemctl --user daemon-reload
systemctl enable --user --now intune-agent.timer

# ── Step 10: Install Azure VPN Client ────────────────────────────────────────
echo "[10/11] Installing Microsoft Azure VPN Client..."
AUR_HELPER=""
if command -v yay &>/dev/null; then
    AUR_HELPER="yay"
elif command -v paru &>/dev/null; then
    AUR_HELPER="paru"
fi

if [ -n "$AUR_HELPER" ]; then
    $AUR_HELPER -S --noconfirm --needed microsoft-azure-vpn-client-bin
else
    echo "       No AUR helper found (yay/paru). Install microsoft-azure-vpn-client-bin manually."
fi

# Add user to network group (required by Azure VPN on Arch)
if ! id -nG "$USER" | grep -qw network; then
    sudo usermod -aG network "$USER"
    echo "       Added $USER to network group (re-login required)"
fi

# Azure VPN requires VERSION in os-release
if ! grep -q '^VERSION=' /etc/os-release; then
    echo 'VERSION="0"' | sudo tee -a /etc/os-release > /dev/null
    echo "       Added VERSION to /etc/os-release"
fi

# Symlink VPN binary to PATH if not already there
if [ ! -f /usr/bin/microsoft-azurevpnclient ] && [ -f /opt/microsoft/microsoft-azurevpnclient/microsoft-azurevpnclient ]; then
    sudo ln -s /opt/microsoft/microsoft-azurevpnclient/microsoft-azurevpnclient /usr/bin/microsoft-azurevpnclient
    echo "       Symlinked microsoft-azurevpnclient to /usr/bin"
fi

# ── Step 11: Fix XDG Desktop Portal for Hyprland ────────────────────────────
# On Hyprland, the GTK portal (which handles FileChooser and OAuth redirects)
# has UseIn=gnome so it never activates. This breaks file import dialogs and
# OAuth authentication in apps like the Azure VPN Client.
echo "[11/11] Configuring XDG desktop portal for Hyprland..."
if [ "$XDG_CURRENT_DESKTOP" = "Hyprland" ] || pgrep -x Hyprland &>/dev/null; then
    PORTAL_CONF="$HOME/.config/xdg-desktop-portal/portals.conf"
    mkdir -p "$(dirname "$PORTAL_CONF")"
    if [ ! -f "$PORTAL_CONF" ]; then
        cat > "$PORTAL_CONF" << 'EOF'
[preferred]
default=gtk
org.freedesktop.impl.portal.Screenshot=hyprland
org.freedesktop.impl.portal.ScreenCast=hyprland
org.freedesktop.impl.portal.GlobalShortcuts=hyprland
EOF
        echo "       Created portals.conf (GTK as default, Hyprland for screen capture)"
    else
        echo "       portals.conf already exists, skipping"
    fi
    # Restart portals to pick up new config
    pkill -f "xdg-desktop-portal" 2>/dev/null || true
    echo "       Portal processes restarted (will respawn on demand)"
else
    echo "       Not running Hyprland, skipping"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "MANUAL STEPS REQUIRED:"
echo ""
echo "  1. Create a 'login' keyring (required for token storage):"
echo "     - Open 'seahorse' (Passwords and Keys)"
echo "     - File > New > Password Keyring"
echo "     - Name it 'login' and SET A PASSWORD"
echo ""
echo "  2. Log out and back in (to pick up environment and group changes)"
echo ""
echo "  3. Enroll your device:"
echo "     - Run 'intune-portal'"
echo "     - Sign in with your Microsoft account"
echo "     - If using YubiKey, enter your PIV PIN when prompted (not your FIDO PIN)"
echo ""
echo "  4. Connect Azure VPN:"
echo "     - Run 'microsoft-azurevpnclient'"
echo "     - Import your VPN profile XML"
echo "     - Click Connect and authenticate"
echo ""
echo "  5. Verify enrollment:"
echo "     - Open Microsoft Edge and sign in"
echo "     - You should not be prompted for a password again"
echo ""
