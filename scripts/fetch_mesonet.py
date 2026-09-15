#!/usr/bin/env python3
"""
Fetches Kentucky Mesonet camera images and current-conditions data for
every station this app actually uses, and writes them into a flat
directory structure with STABLE filenames (not the timestamped ones
Mesonet uses internally), so the app can always request a fixed URL
without needing to know the current filename ahead of time.

Run by .github/workflows/update-mesonet-data.yml on a 5-minute
schedule (matching Mesonet's own update cadence). Rehosts this data
per Kentucky Mesonet's own stated condition for reuse (per Dr. Jerry
Brotzge, Programs Director, in reply to a direct request) - the app
itself must never hit Mesonet's CDN or API directly, only this repo's
own rehosted mirror of it.

Zero third-party dependencies on purpose (stdlib only) - keeps the
GitHub Actions job fast and needing no pip install step.
"""
import gzip
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

# Every station code currently referenced by any site's
# mesonet_station field in autoload/site_data.gd. Keep this list in
# sync by hand when new sites are added or removed - there's no
# automated link between this script and the Godot project, they're
# separate repos-worth of concern.
STATIONS = [
    "ALBN", "BAND", "BLOM", "BLRK", "BMTN", "BNGL", "BNVL", "BTCK",
    "CCLA", "CHTR", "CMBA", "CRMT", "CROP", "DABN", "DORT", "DRFN",
    "ERLN", "EWPK", "FCHV", "FLRK", "GRDR", "GRHM", "HARD", "HCKM",
    "HHTS", "HRDB", "HTFD", "HUEY", "LGNT", "LGRN", "LNDN", "LSML",
    "LUSA", "LXGN", "MONT", "MRHD", "MROK", "MRRY", "PRST", "PVRT",
    "RBSN", "RFSM", "RPTN", "RSVL", "STAN", "SWON", "VEST", "WDBY",
    "WNCH", "WSHT", "ZION",
]

CAMERA_MANIFEST_URL = "https://d266k7wxhw6o23.cloudfront.net/camera/latest.json"
CAMERA_BASE_URL = "https://d266k7wxhw6o23.cloudfront.net/"
CURRENT_URL_TEMPLATE = "https://www.kymesonet.org/api/data/current/{station}"

OUT_DIR = "data"

# The ONE place this interval lives. Update this whenever the cron
# schedule on the VM changes - the app itself reads this value out of
# manifest.json at runtime (see get_update_interval_seconds() in
# autoload/mesonet_manifest.gd) rather than having its own hardcoded
# guess baked into the Godot build. That's the actual fix for a real
# problem: this interval changed three times in one session (5 min,
# 12 min, 10 min) and the app's own auto-refresh timer, previously a
# separate hardcoded constant in site_detail.gd, never once followed
# along - a full app rebuild and reinstall would have been needed
# every time just to keep the two in sync. Now there's only one place
# to edit, and it takes effect the next time the mirror runs, no app
# rebuild required.
UPDATE_INTERVAL_SECONDS = 600
USER_AGENT = "KYStateProBaiter-DataSync/1.0 (+https://github.com/oldmanbombin/MesonetMirror)"


def _maybe_decompress(raw: bytes) -> bytes:
    """CloudFront (and some other CDNs) can serve gzip-compressed
    content even without a client explicitly requesting it - unlike
    the third-party `requests` library, Python's built-in urllib
    never auto-decompresses, so a raw gzip body handed straight to
    json.loads() or saved as an "image" blows up or produces garbage.
    Detected here by checking for gzip's own magic number (0x1f 0x8b)
    directly on the raw bytes, rather than trusting the
    Content-Encoding response header, since that's not always set
    correctly by every CDN configuration. JPEGs (0xFF 0xD8) and plain
    JSON text never start with these bytes, so this is safe to apply
    unconditionally to both fetch_json() and fetch_bytes()."""
    if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B:
        return gzip.decompress(raw)
    return raw


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = _maybe_decompress(resp.read())
            return json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
        print(f"  FAILED: {url} - {e}")
        return None


def fetch_bytes(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return _maybe_decompress(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"  FAILED: {url} - {e}")
        return None


def parse_iso_to_epoch(ts_str: str) -> int:
    """Mesonet's own format looks like '2026-08-28T14:07:00.000000Z'.
    Computed here in Python (robust stdlib ISO parsing) and stored as
    a plain int in our manifest, so the Godot side never has to parse
    an ISO string itself - it already has a UTC-epoch-to-local-time
    helper (site_detail.gd's _local_time_string) that just wants an
    int."""
    if not ts_str:
        return 0
    try:
        cleaned = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return int(dt.timestamp())
    except ValueError:
        return 0


def main() -> None:
    os.makedirs(f"{OUT_DIR}/camera", exist_ok=True)
    os.makedirs(f"{OUT_DIR}/current", exist_ok=True)

    manifest = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "update_interval_seconds": UPDATE_INTERVAL_SECONDS,
        "stations": {},
    }

    print("Fetching camera manifest...")
    # Cache-busting query param - real suspected cause of images
    # appearing "frozen" across multiple workflow runs despite fresh
    # commits each time: d266k7wxhw6o23.cloudfront.net (Mesonet's own
    # CDN, not ours) may be caching this exact URL for longer than our
    # 5-minute polling interval, in which case every run within that
    # window gets back the SAME stale filename from THEM, and we
    # legitimately re-download and re-commit the same unchanged image
    # - not a bug in our own overwrite logic, but worth ruling out
    # empirically rather than assuming. Appending a changing query
    # string is the standard way to bypass a CDN cache when the cache
    # key includes the query string (not guaranteed to work on every
    # CDN config, but low-risk to try).
    cache_bust_url = f"{CAMERA_MANIFEST_URL}?_cb={int(datetime.now(timezone.utc).timestamp())}"
    camera_manifest = fetch_json(cache_bust_url) or {}
    if not camera_manifest:
        print("WARNING: camera manifest fetch failed or returned nothing - camera images will be skipped this run, previous images (from the last successful run) stay live since we only overwrite what we successfully fetch.")
    else:
        # Diagnostic: print what Mesonet's OWN manifest actually says
        # for a couple of stations, every run - lets us directly
        # confirm from the Actions log whether their filename/
        # timestamp is really advancing between runs, or whether we're
        # legitimately just being handed the same stale answer.
        for probe_station in ("BAND", "LGRN", "WDBY"):
            probe_info = camera_manifest.get(probe_station)
            print(f"  [DIAG] Mesonet's own manifest for {probe_station}: {probe_info}")

    for station in STATIONS:
        print(f"Station {station}:")
        station_entry = {"has_camera": False, "has_current": False}

        cam_info = camera_manifest.get(station) if camera_manifest else None
        if cam_info and cam_info.get("filename"):
            image_bytes = fetch_bytes(CAMERA_BASE_URL + cam_info["filename"])
            if image_bytes:
                with open(f"{OUT_DIR}/camera/{station}.jpg", "wb") as f:
                    f.write(image_bytes)
                station_entry["has_camera"] = True
                station_entry["camera_timestamp_epoch"] = parse_iso_to_epoch(
                    cam_info.get("utcTimestampCollected", "")
                )
                print(f"  camera OK ({len(image_bytes)} bytes)")
        else:
            print("  no camera entry in manifest for this station")

        current_url = CURRENT_URL_TEMPLATE.format(station=station) + f"?_cb={int(datetime.now(timezone.utc).timestamp())}"
        current = fetch_json(current_url)
        if current:
            with open(f"{OUT_DIR}/current/{station}.json", "w") as f:
                json.dump(current, f)
            station_entry["has_current"] = True
            print("  current OK")

        manifest["stations"][station] = station_entry

    with open(f"{OUT_DIR}/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    ok_camera = sum(1 for s in manifest["stations"].values() if s["has_camera"])
    ok_current = sum(1 for s in manifest["stations"].values() if s["has_current"])
    print(f"\nDone. {ok_camera}/{len(STATIONS)} cameras, {ok_current}/{len(STATIONS)} current-conditions fetched successfully.")


if __name__ == "__main__":
    main()
