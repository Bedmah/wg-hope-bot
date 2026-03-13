# WG Hope Bot v1.2.3

Release focus: production hardening for dynamic uplink/region management and reboot safety.

## Summary

This release closes the class of incidents where newly added VPN uplinks could break connectivity after reboot, silently fall back to another region, or miss expected DOWN/RECOVERY signaling.

## What Changed

- Uplink config safety:
  - VPN uplink config replacement now force-normalizes `Table = off` in `[Interface]`.
  - This prevents accidental default-route hijack when `AllowedIPs = 0.0.0.0/0` is present.

- Service lifecycle sync:
  - Bot startup now synchronizes DB uplink state with systemd:
  - `enabled=1` -> `systemctl enable + start`
  - `enabled=0` -> `systemctl stop + disable`
  - Result: enabled uplinks survive reboot and come back automatically.

- Region/routing safety:
  - Routing logic ignores disabled uplinks.
  - Region assignment rejects disabled interfaces.
  - Region assignment for VPN uplinks also rejects non-ready interfaces.
  - Interface status in admin checks now includes stale-handshake probe (`probe=ok|fail`) to avoid misleading “OK/FAIL”.

- Monitoring reliability:
  - Added `UPLINK_ALERT_DOWN_ON_START` (default `1`) to control down-alert behavior after restart.
  - Fixed alert state transitions so recovery correctly stores `last_alert_state=ok`.

- Production defaults and startup consistency:
  - Default subnet updated to `/22` in templates/settings.
  - Bot unit launch path aligned to direct `bot.py` execution.

## Practical Effect

- New regions backed by enabled/healthy uplinks remain stable after server reboot.
- Clients no longer “silently drift” to fallback region when the target uplink is actually healthy and should be active.
- Alerting behavior is more predictable during restart windows.

## Files Updated

- `vpn_bot/server_admin.py`
- `vpn_bot/routing.py`
- `vpn_bot/db.py`
- `vpn_bot/monitoring.py`
- `vpn_bot/main.py`
- `vpn_bot/settings.py`
- `deploy/systemd/wg-hope-bot.service`
- `.env.example`
- `README.md`
- `CHANGELOG.md`

