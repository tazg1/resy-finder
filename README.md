# Resy finder

Checks Resy for a table and pushes an alert to your phone (via the free [ntfy](https://ntfy.sh) app) when one opens up.

- **In the cloud:** GitHub Actions runs `cloud_check.py` every 30 minutes. It alerts you about new openings. Before a release (for example 11:00 AM ET) it sends a 10-minute heads-up, then checks every few seconds right after the drop.
- **On your computer:** `py resy_finder.py` asks for a restaurant, shares booking tips, and searches.

## Change what it looks for

Edit `settings.json` on GitHub (pencil icon, then **Commit changes**):

```json
{
  "restaurants": [
    {"name": "Bungalow", "release_time_et": "11:00 AM"},
    {"name": "Lilia", "release_time_et": ""},
    {"name": "The Four Horsemen", "release_time_et": ""}
  ],
  "party_size": 2,
  "days": ["Thursday", "Friday", "Saturday"],
  "earliest": "6:00 PM",
  "latest": "8:00 PM"
}
```

`release_time_et` is when the restaurant releases new dates, in New York time. Leave it as `""` to use the time from Resy's guide, if there is one. How many days ahead dates open is worked out from Resy's live calendar.

## Set up

1. Install **ntfy** from the Google Play Store and subscribe to a hard-to-guess topic name.
2. In this repo, go to **Settings > Secrets and variables > Actions > New repository secret**. Name it `NTFY_TOPIC` and set the value to your topic name.
3. Go to **Actions > Resy finder > Run workflow**, choose `test`, and check that your phone buzzes.

## Notes

- GitHub pauses scheduled runs if a public repo has no commits for 60 days. Re-enable it on the Actions tab.
- This uses Resy's unofficial website API, so it could stop working if Resy changes it.
