# Family Music

Family Music is a standalone local PWA for controlling Music Assistant and Sonos without requiring a Home Assistant login.

## Before first start

1. Open Music Assistant.
2. Go to **Settings -> Profile**.
3. Create a long-lived access token.
4. Paste the token into the Family Music app configuration.
5. Leave the Music Assistant URL at `http://127.0.0.1:8095` when Music Assistant is running on the same HAOS host.

The app listens directly on port **8099**. On your home network, open:

`http://<home-assistant-host-ip>:8099`

Then on iPhone/iPad, use Safari **Share -> Add to Home Screen**.

The initial release is intended only for a trusted home LAN. Do not expose port 8099 directly to the public internet.
