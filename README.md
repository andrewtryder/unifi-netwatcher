<p align="center">
  <img src="docs/assets/netwatcher-logo.svg" alt="NetWatcher" width="360">
</p>

<p align="center">
  <img src="docs/assets/screenshot.png" alt="NetWatcher dashboard" width="800">
</p>

# NetWatcher for UniFi

**Know what's connected to your network.**

NetWatcher is a simple dashboard for keeping an eye on devices connected to your UniFi network. Spot unfamiliar devices, keep track of the ones you trust, and see when devices connect, disconnect, or move between Wi-Fi access points.

It runs in a single Docker container and uses information from your existing UniFi controller—no separate network scanner required.

## What can it do?

- **Spot new devices.** See what has joined your network and identify anything unfamiliar.
- **Keep things organized.** Mark devices as trusted or ignored without losing track of them.
- **See connection history.** View disconnects, reconnects, Wi-Fi roaming, and IP address changes on each device's timeline.
- **Get alerts when you want them.** Set up Pushover, webhooks, and other supported notification channels. Connection and roaming alerts are optional and off by default.
- **Block a device if needed.** Available with the legacy UniFi API, with a dry-run option for testing.

Your device history is stored locally in SQLite. NetWatcher is designed for one UniFi controller and site per installation.

## Get started

You'll need Docker with Compose and access to a UniFi Network controller.

### 1. Download NetWatcher and create your settings file

```bash
git clone https://github.com/andrewtryder/unifi-netwatcher.git
cd unifi-netwatcher
cp .env.example .env
```

### 2. Connect it to UniFi

Open `.env` and enter your controller address and credentials. For the default username/password connection, the important settings are:

```env
UNIFI_URL=https://192.168.1.1
UNIFI_SITE=default
UNIFI_API_MODE=legacy
UNIFI_USERNAME=netwatcher
UNIFI_PASSWORD=your-password
UNIFI_MOCK_MODE=false
```

Replace the examples with your own details. Prefer a dedicated UniFi account with only the permissions it needs.

If your controller supports API keys, see [Connecting with an API key](#connecting-with-an-api-key) below.

### 3. Start NetWatcher

```bash
docker compose up -d
```

Open **http://localhost:8080** (or `http://YOUR-SERVER-IP:8080`). Sign in with **`admin` / `admin`** and change the password immediately under **Security**.

That's it. The dashboard will start showing the devices reported by UniFi. You can use **Test UniFi Connection** on the dashboard if nothing appears.

## Everyday use

- **Review devices:** Open the device list to see new, trusted, and ignored devices. Trust or ignore a device with a click. Approval status is separate from whether a device is currently online.
- **Check what changed:** Open a device to view its Connection Timeline, including disconnects, reconnects, Wi-Fi access point and SSID changes, and IP address changes. NetWatcher waits for repeated missed scans before calling a device offline, helping avoid false alarms from brief interruptions.
- **Choose your alerts:** Add a notification channel in Notifications. You can opt in to alerts for devices going offline or coming back online, changing Wi-Fi networks, roaming between access points, or changing IP addresses. These behavioral alerts are off by default.
- **Adjust scanning:** Use Tools to change scan frequency and history retention. Devices are never automatically trusted just because they reconnect.

## Connecting with an API key

NetWatcher also supports UniFi's official Network Integration API. If your UniFi console supports it, create a key under **Settings → Integrations** (or **API Keys**) and use:

```env
UNIFI_API_MODE=official
UNIFI_API_KEY=your-api-key
```

Keep your `UNIFI_URL` and `UNIFI_SITE` settings.

The official API supports monitoring but not device blocking; use `legacy` mode if you need that feature. An `auto` mode is also available for suitable setups.

Controller certificate verification is enabled by default. If you use a self-signed certificate, prefer adding its CA certificate with `UNIFI_CA_BUNDLE` rather than disabling verification.

## Updates and backups

To update a Docker installation:

```bash
docker compose pull
docker compose up -d
```

NetWatcher's database and generated encryption key live in its persistent Docker volume (`netwatcher-data`). Back up both before major changes or migrations so device history and saved notification credentials remain recoverable.

Container images are available on [GitHub Container Registry](https://github.com/andrewtryder/unifi-netwatcher/pkgs/container/unifi-netwatcher) and [Docker Hub](https://hub.docker.com/r/andrewtryder/unifi-netwatcher). For a stable installation, pin a versioned image tag instead of relying on `latest`.

## A few important notes

- **Keep NetWatcher on your trusted network.** It uses a web login, but don't expose the default HTTP service directly to the internet. Use HTTPS through a reverse proxy or a VPN for remote access.
- **Protect your `.env` file.** It contains UniFi credentials. Do not commit it to Git.
- **Run one instance.** Scanning and housekeeping run inside the app; use one container replica.

## Need help?

Check `docker compose logs -f` and use the dashboard's connection test. You can also [open an issue](https://github.com/andrewtryder/unifi-netwatcher/issues).

For the complete list of settings, see [.env.example](.env.example). Developers can start with [CONTRIBUTING.md](CONTRIBUTING.md), and upcoming features are tracked in [ROADMAP.md](ROADMAP.md).
