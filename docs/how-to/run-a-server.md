# Run a server for the Android app

Run `music-loader serve` on the machine that holds the library, so the
[Android app](../../android/README.md) (or any HTTP client) can queue links from another device.

Prerequisites: the CLI is installed and `ffmpeg` is in `PATH` (see [Install](../../README.md#install)).

1. Start the server with the library folder:

   ```bash
   music-loader serve -o ~/Music
   ```

   At startup it prints the server address and the access token, for example:

   ```text
   Server address: http://192.168.1.10:8765
   Token: <generated token> (~/.config/music-loader/server-token)
   ```

2. In the app, open **Settings → Where to download → Server**, enter the address and the token,
   then tap **Test → Save**.

3. Expose the server only in a trusted network. It speaks plain HTTP, so use one of:

   - the home network;
   - a VPN (Tailscale, WireGuard);
   - an HTTPS reverse proxy, with the server started as `music-loader serve -o ~/Music --host 127.0.0.1`.
     Turn response buffering off for the event stream; the server sends `X-Accel-Buffering: no`
     for nginx.

4. To keep the server running on Linux, create a systemd user unit
   `~/.config/systemd/user/music-loader.service`:

   ```ini
   [Unit]
   Description=Music Loader server
   After=network-online.target

   [Service]
   ExecStart=%h/.local/bin/music-loader serve -o %h/Music
   Restart=on-failure
   # Optional: own Spotify credentials, a fixed token
   # EnvironmentFile=%h/.config/music-loader/env

   [Install]
   WantedBy=default.target
   ```

   If you use an `EnvironmentFile` with secrets, make it readable only by its owner (`chmod 600`).

5. Enable and start the unit, then read the address and token from the log:

   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now music-loader
   journalctl --user -u music-loader -f
   ```

To pair the app with a new token, restart the server once with `--new-token` and repeat step 2.

Related: [Server reference](../reference/server.md) (options, token sources, limits, HTTP API),
[Security](../explanation/security.md).
