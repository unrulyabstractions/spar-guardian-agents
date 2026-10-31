#!/usr/bin/env bash
# Put a box on a kernel the access log can run on, as root on the box: box_kernel.sh [--check]
#
# The access log holds each open with fanotify permission events (src/sandbox/access_log/), which a kernel has only when
# built with CONFIG_FANOTIFY_ACCESS_PERMISSIONS. vast.ai's VM image boots Ubuntu's -kvm kernel, built without it, so
# fanotify_mark refuses FAN_OPEN_PERM with EINVAL. Ubuntu's generic kernel has it. kexec starts that kernel from the
# running one, past the firmware and the boot loader: a box set to boot it through grub never came back, from a reboot
# or from vast.ai's restart (58500176, 2026-10-31). The boot default stays as it was, so a power cycle returns the -kvm
# kernel, and the monitor then refuses at setup. --check only says whether the running kernel has the option.
set -euo pipefail
has_permission_events() { grep -qs '^CONFIG_FANOTIFY_ACCESS_PERMISSIONS=y' "/boot/config-$(uname -r)"; }
if has_permission_events; then
  echo "   kernel $(uname -r) has fanotify permission events"
  exit 0
fi
if [ "${1:-}" = --check ]; then
  echo "refused: kernel $(uname -r) has no fanotify permission events"
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer || true
echo "kexec-tools kexec-tools/load_kexec boolean false" | debconf-set-selections
apt-get -o DPkg::Lock::Timeout=900 update -qq
apt-get -o DPkg::Lock::Timeout=900 install -y -qq --no-install-recommends linux-image-generic kexec-tools
kernel=$(ls /boot/vmlinuz-*-generic | sed 's#/boot/vmlinuz-##' | sort -V | tail -1)
kexec -l "/boot/vmlinuz-$kernel" --initrd="/boot/initrd.img-$kernel" --append="$(sed 's/BOOT_IMAGE=[^ ]* //' /proc/cmdline)"
echo "   kexec into $kernel"
systemd-run --on-active=2 systemctl kexec > /dev/null
