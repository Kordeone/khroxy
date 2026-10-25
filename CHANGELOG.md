# Changelog

## 0.7.1 — Reliability audit / GitHub preparation

- Fix `g` being consumed by scroll-to-End navigation before it could activate Fastest mode in the Profiles TUI; Home/End remain supported.
- Prevent stale `@active` / `@fastest` application references from aborting the `any` subscription-fetch mode before trying healthy profiles.
- Add a regression test for this failure.
- Add repository-level secret/build exclusions, offline GitHub Actions checks, and a security/deployment status document.
- Keep the same four-screen keyboard-first TUI and `?` keybindings overlay.
- Docker runtime remains **unverified**: no live kernel, container, or firewall tests have run.

## 0.7 — Initial four-screen TUI and Docker development preview

- Profiles, Applications, Subscriptions and Logs on separate screens.
- Active-profile marker, HTTPS latency probes and symbolic active/fastest routes.
- Experimental combined Docker gateway + AI workspace with a namespace-local egress guard.
