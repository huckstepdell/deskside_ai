# Delltech platform firmware — changelog

## 2.4.1 — released 2026-02-24

- **USB-C / Thunderbolt:** reworked the power-delivery negotiation sequence to
  shorten handshake time on cold attach. The controller now renegotiates the
  PD contract whenever the attached device reports a change in available
  power budget, rather than only at attach time.
- Display: fixed a rare blank-screen condition when hot-plugging a second
  4K panel.
- Thermals: raised the fan curve knee by 4°C to reduce audible ramping under
  light load.
- Security: bumped the bundled TPM firmware to 7.2.1.

## 2.4.0 — released 2026-01-15

- Battery: improved charge-rate estimation below 15% state of charge.
- USB-C: added support for 240W EPR sources.
- Fixed an issue where the fingerprint reader was unavailable after
  resuming from hibernation.

## 2.3.9 — released 2025-10-30

- Display: corrected color profile loading on external panels.
- Audio: reduced pop on headphone insertion.
- Networking: fixed intermittent Wi-Fi 7 roaming failures on WPA3 networks.
