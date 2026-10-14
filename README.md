# Adchestra

Run Google Ads from Claude Desktop, using your own Google account and computer. Free to self-host.

Want to skip the technical setup? [Use hosted Adchestra](https://adchestra.com) for **$2.95 USD/month**, with a **14-day free trial**.

Create paused Search campaigns and ads, manage keywords and budgets, and review performance. Every change starts with a preview and a Google validation run.

## Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv tool install git+https://github.com/ShopPilot/adchestra-local
```

## Connect Google

1. Create a project in [Google Cloud](https://console.cloud.google.com/). Enable **Google Ads API** in the API Library. No Cloud billing is needed for the local setup tested here.
2. Open **Google Ads API > Overview**. Apply for **Explorer** access and wait until Explorer is shown as the current level.
3. Open **Google Auth Platform**. Create an app with your email and an **External** audience. Under **Audience**, add your Google email as a test user.
4. Under **Clients**, create a **Desktop app** client. Select **Use this client for an AI-powered agent**. Download its JSON file.
5. Copy your advertiser account's ten-digit customer ID from Google Ads, then run:

```sh
adchestra-local setup --client /path/to/client.json --account 1234567890
adchestra-local install-claude --apply
adchestra-local doctor
```

If your access is through a manager account, add `--manager 0987654321` to setup. The advertiser ID selects the campaigns; the manager ID routes access.

Setup opens Google sign-in and requests Google Ads access only. Credentials stay in `~/.adchestra-local`. Never share that folder or the downloaded client JSON.

Fully quit and reopen Claude. Under **Settings > Developer**, check that **adchestra-local** is Running.

## Use in Claude

> Use adchestra-local to list my campaigns and their status.

> Create a paused Search campaign for my product. Use $5 per day, target the US and English, and show me the keyword and ad suggestions first.

> Show my campaign results for the last seven days. Explain which search terms look irrelevant and suggest improvements.

Claude shows proposed changes, runs Google's validation, then asks before applying. Keep write tools on **Allow once**. New campaigns are paused; enabling them can start ad spending. The default daily budget limit is 50 in your account currency. Set another limit with `--max-daily-budget` during setup.

Pending plans survive restarts and expire after 24 hours. Applied plans cannot be applied again.

## Google's access requirements

**Keyword research:** Explorer permits campaign management and reporting, but not Keyword Planner API calls. For keyword ideas and forecasts, complete Google Auth brand verification and upgrade your Cloud project to **Basic**. [Google's requirements](https://developers.google.com/google-ads/api/docs/api-policy/access-levels).

**Reconnecting:** Google authorizations for an External app in Testing mode expire after seven days. Run `adchestra-local reconnect`, then restart Claude. Production mode avoids that specific Testing expiry, but may require additional configuration or verification. [Google's rules](https://support.google.com/cloud/answer/15549945).

## Troubleshooting

- **Server not running:** fully quit Claude and reopen it. Run `adchestra-local doctor` for the connection error.
- **Wrong account or permission denied:** use the Google email that has Ads access. Check advertiser and manager IDs and the Cloud project's API access level.
- **Authorization expired:** run `adchestra-local reconnect`.
- **Keyword Planner denied:** request Basic access; changing the connector does not remove Google's restriction.

## Development

```sh
uv sync --locked --group dev
uv run pytest -q
```

macOS has been tested live. Windows configuration is supported; a live Windows test is not yet completed.

## License and credit

This self-hosted edition is source available under MIT plus Commons Clause for our additions. Original portions are derived from [AdLoop](https://github.com/kLOsk/adloop), created by Daniel Klose, and retain their MIT license. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

