# NetWatcher — Modernization & Behavioral Monitoring Roadmap

## Role and Objective

NetWatcher is an open-source, lightweight UniFi network device monitoring and approval application. It is designed to run in home labs, Proxmox environments, and self-hosted Docker installations with minimal operational overhead.

The objective of this roadmap is to incrementally evolve NetWatcher from an unknown-device monitor into a comprehensive, event-driven network monitoring and device approval application featuring:

1. **Modern UniFi API Integration**: Support for the documented UniFi Network integration API and API-key authentication alongside existing session-authenticated controller installations.
2. **Device Behavioral Tracking**: Independent connectivity tracking (online/offline) and state-transition monitoring (roaming, disconnections, reconnections) separate from device approval status.
3. **Event-Driven Reconciliation**: Real-time alarm integration with UniFi Network 11 Alarm Manager webhooks, preserving periodic polling as a fallback.
4. **Intelligent Notifications & Richer Context**: Granular, opt-in notification rules over Pushover, Webhooks, and Apprise, enriched with network and VLAN context.

---

## Non-Negotiable Design Principles

Every phase and pull request must strictly adhere to these architectural constraints:

1. **Single-Site Scope**: Each NetWatcher instance monitors exactly one UniFi controller and site. Multi-site management, controller switching, or multi-tenant database schemas are out of scope.
2. **Backward Compatibility**: Existing session-based UniFi controller configurations (`UNIFI_URL`, `UNIFI_USERNAME`, `UNIFI_PASSWORD`, `UNIFI_SITE`) must continue to work without manual modification.
3. **Approval State Integrity**: Existing approval workflows and states (`unknown`, `trusted`, `ignored`, `blocked`) are immutable and strictly decoupled from transient connectivity state.
4. **Notification Architecture**: Pushover, custom Webhook, and Apprise integrations must remain operational without breaking changes to configurations or encrypted secrets.
5. **Minimal Infrastructure**: SQLite and APScheduler are the foundational database and scheduling engines. No external message brokers (Redis, RabbitMQ, Kafka) or distributed workers (Celery) will be introduced.
6. **Hardened Docker Security**: Deployments run as a non-root user in a hardened, minimal container environment with secure defaults.
7. **Additive, Opt-In Changes**: All behavioral monitoring and alert rules are opt-in to avoid alert fatigue on existing deployments.
8. **Safe Database Evolutions**: All schema changes must be implemented via reversible Alembic migrations preserving existing user data.
9. **Defense-in-Depth**: Strict SSRF protections, authenticated routes, CSP policies, CSRF/origin validation, and Fernet encryption for secrets at rest must be maintained.
10. **Documented APIs Only**: Integrations must use official or documented UniFi endpoints without inventing unsupported capabilities or speculative schemas.
11. **Decoupled Modularity**: Core scanning and scheduling logic must remain free of provider- or adapter-specific branching.
12. **Lightweight Resource Footprint**: Memory usage and CPU overhead must remain lightweight for continuous operation on constrained hardware.

---

## Release Roadmap Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                        v0.4.0 — UniFi API Modernization                 │
│  - UniFi Adapter Interface (Legacy & Official)                         │
│  - API Selection (legacy | official | auto)                            │
│  - Scan Reliability (Distinguish empty site from controller errors)    │
│  - System & Connection Status UI                                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                  v0.5.0 — Device Behavioral Monitoring                 │
│  - Independent Connectivity State (Online / Offline)                  │
│  - Behavioral Event Engine (Discovered, Connected, Disconnected, Roam) │
│  - Safeguards & Noise Suppression (Grace periods, flapping suppression)│
│  - Configurable Notification Rules across all providers               │
│  - Lightweight Device Timeline UI                                      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│              v0.6.0 — UniFi Network 11 Alarm Integration               │
│  - Authenticated Webhook Receiver for Alarm Manager events             │
│  - Event-Assisted Inventory Reconciliation                             │
│  - Coalesced Triggering & Polling Fallback                             │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│             v0.7.0 — Network Context and UI Improvements               │
│  - Human-readable VLAN and Network information                         │
│  - Connection Stability & Activity Metrics                             │
│  - Enriched Alert Messages                                             │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Detailed Milestone Specifications

### Release v0.4.0 — UniFi API Modernization

**Goal:** Introduce modern UniFi Network integration API support while maintaining full backward compatibility with existing session-authenticated controllers.

#### Architecture Components
- **UniFi Adapter Interface (`app/unifi/base.py`)**: Abstract base class (`UniFiAdapter`) exposing:
  - `capabilities: UniFiCapabilities`
  - `get_clients() -> list[UniFiClientDevice]`
  - `block_client(mac: str) -> bool`
  - `unblock_client(mac: str) -> bool`
  - `test_connection() -> tuple[bool, str]`
  - `close() -> None`
- **Normalized Client Model (`app/unifi/models.py`)**: `UniFiClientDevice` providing typed attributes (`mac`, `ip`, `hostname`, `is_wired`, `rssi`, `rx_rate_bps`, etc.) with backward-compatible dictionary access for existing code.
- **Typed Exception Hierarchy**: `UniFiError`, `UniFiAuthError`, `UniFiPermissionError`, `UniFiConnectionError`, `UniFiTimeoutError`, `UniFiResponseError`.
- **Legacy Adapter (`app/unifi/legacy.py`)**: Session-authenticated client using cookie-based login (`/api/auth/login`), `/stat/sta` client discovery, and `stamgr` command execution.
- **Official API Adapter (`app/unifi/official.py`)**: Modern UniFi Network API integration using API-key authentication (`X-API-KEY`), single-site identifier resolution, and complete pagination.
- **Adapter Factory & Capability Discovery (`app/unifi/factory.py`)**: Factory resolving active adapter based on `UNIFI_API_MODE`:
  - `legacy`: Session-authenticated (default).
  - `official`: Documented Network API with API key.
  - `auto`: Conservative detection without automatic fallback during mutations.
- **Scan Reliability**: Scanner correctly differentiates between a successful scan with zero connected clients (`scan_finished`, `success=True`) and controller/network failures (`scan_failed`, `success=False`).

#### Implementation Pull Requests
- **PR 1**: `refactor(unifi): introduce adapter interface and scan reliability` ([PR #58](https://github.com/andrewtryder/unifi-netwatcher/pull/58) — Merged).
- **PR 2**: `feat(unifi): support official network API and API key authentication` ([PR #59](https://github.com/andrewtryder/unifi-netwatcher/pull/59) — Merged).
- **PR 3**: `feat(unifi): expose adapter capabilities and connection status in UI` ([PR #60](https://github.com/andrewtryder/unifi-netwatcher/pull/60) — Merged).

#### Acceptance Criteria
- [x] Existing legacy installations operate with zero configuration changes.
- [x] Official API mode successfully synchronizes single-site client inventory.
- [x] Empty sites produce successful scans without error events or disconnection misclassifications.
- [x] Controller network errors and authentication failures trigger `scan_failed` with explicit error detail.
- [x] API keys and passwords are masked and never logged.
- [x] UI exposes configured API mode, active adapter, connection health, and declared capabilities.
- [x] Isolated "Test UniFi Connection" action runs on-demand without mutating devices or dispatching alerts.
- [x] Server-side routes and UI actions gracefully reject unsupported capabilities (e.g. Official mode blocking) with HTTP 409 Conflict without altering device state.
- [x] 100% test pass rate with coverage maintained above 80% (achieved 85.84%).

---

### Release v0.5.0 — Device Behavioral Monitoring

**Goal:** Track online/offline status and device state transitions over time while keeping approval decisions independent.

#### Architecture Components
- **Decoupled Connectivity Model**:
  - `approval_status`: `unknown`, `trusted`, `ignored`, `blocked`.
  - `connectivity_state`: `unknown`, `online`, `offline`.
  - Timestamp tracking: `last_connected_at`, `last_disconnected_at`, `consecutive_missed_scans`, `offline_candidate_since`.
- **Behavioral Event Engine**:
  - Standardized events: `device_discovered`, `device_connected`, `device_disconnected`, `device_reconnected`, `device_ssid_changed`, `device_ap_changed`, `device_network_changed`.
- **Safeguards & Flapping Suppression**:
  - Configurable offline grace period: device must miss $\ge N$ consecutive scans (`DEVICE_OFFLINE_MISSED_SCANS`) and satisfy grace duration (`DEVICE_OFFLINE_GRACE_SECONDS`) before transitioning to offline.
  - Outage immunity: Controller scan failures or timeouts freeze connectivity state and counters.
  - Baseline initialization: Pre-existing or newly imported devices establish an initial baseline without firing spurious reconnection events.
- **Granular Notification Rules**:
  - Independent toggles for each behavioral event type.
  - Alert deduplication and per-device cooldown windows.
- **Device Timeline**:
  - Lightweight HTML/Tailwind timeline component in the device detail view rendering recent state transitions and connection history.

#### Implementation Pull Requests
- **PR 1**: `feat(devices): add independent connectivity state and transition events` (Decoupled connectivity model, persistent timestamps, multi-scan and grace period thresholds, behavioral events, baseline initialization, outage immunity, device detail view — Complete, Pending Review).
- **PR 2**: `feat(notifications): add configurable behavioral notification rules` (Granular notification rules, opt-in toggles, per-device cooldown windows).
- **PR 3**: `feat(devices): add lightweight connection history and timeline UI` (Device timeline component, state transitions, roaming history).

#### Acceptance Criteria
- [x] Devices retain their trust status regardless of connectivity changes.
- [x] Controller outages do not trigger false offline alerts or state changes.
- [ ] Roaming between access points does not cause false disconnection/reconnection cycles.
- [ ] All behavioral alerts are disabled by default and require explicit user opt-in.
- [ ] Timeline renders efficiently on SQLite without external charting libraries.

---

### Release v0.6.0 — UniFi Network 11 Alarm Integration

**Goal:** Enable low-latency, event-assisted scanning via UniFi Alarm Manager webhooks without discarding periodic polling.

#### Architecture Components
- **Authenticated Alarm Receiver (`app/api/routes_alarms.py`)**:
  - Dedicated endpoint accepting incoming UniFi Alarm Manager webhooks.
  - Secret token authentication via header or URL token.
  - Strict payload validation, size capping, and rate limiting.
- **Event-Assisted Inventory Reconciliation**:
  - Incoming alarm triggers inventory reconciliation through the active UniFi adapter.
  - Coalesced scheduler triggers: Multiple alarms received within a short window coalesce into a single immediate scan.
- **Resilient Polling Fallback**:
  - Periodic polling continues on its configured schedule to guarantee consistency even if webhooks are dropped or delayed.

#### Acceptance Criteria
- [ ] Webhook receiver operates only when enabled and properly authenticated.
- [ ] Incoming alarms trigger immediate scan reconciliation without redundant duplicate scans.
- [ ] Periodic scans recover from missed webhook events seamlessly.
- [ ] Invalid or unauthenticated webhook attempts are rejected and audit-logged.

---

### Release v0.7.0 — Network Context and UI Improvements

**Goal:** Provide meaningful network and infrastructure context to assist approval decisions without duplicating the UniFi controller dashboard.

#### Features
- **VLAN & Subnet Attribution**: Display human-readable network and VLAN names when available from adapter metadata.
- **Infrastructure Context**: Association of clients with AP name, switch port, and connection quality metrics.
- **Stability Indicators**: Simple badges indicating connection stability and roaming frequency.
- **Enriched Notifications**: Enhanced message formatting across Pushover, Webhook, and Apprise with network details.

#### Acceptance Criteria
- [ ] Graceful fallback when network metadata is not exposed by the controller.
- [ ] No regression in SQLite query performance on large observation histories.
- [ ] UI remains clean, accessible, and fast on mobile and desktop.

---

## Technical Dependencies and Risk Matrix

| Risk / Dependency | Impact | Mitigation Strategy |
|---|---|---|
| **UniFi API Schema Changes** | High | Abstract controller communication behind `UniFiAdapter`; validate payloads with strict defaults and fallback to generic client structures. |
| **Flapping / Roaming Noise** | High | Implement scan confirmation thresholds and grace periods before marking devices offline. |
| **Controller Outage Cascade** | High | Explicitly distinguish scan failures from zero-client observations; freeze connectivity state during scan errors. |
| **SQLite Concurrency & Lock Contention** | Medium | Single-process scanning lock (`_scan_lock`); batch DB reads and writes in bounded transactions. |
| **Webhook Delivery Reliability** | Medium | Maintain periodic polling as the authoritative state reconciler; treat webhooks solely as scan triggers. |

