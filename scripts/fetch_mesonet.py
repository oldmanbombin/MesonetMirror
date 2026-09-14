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
USER_AGENT = "KYStateProBaiter-DataSync/1.0 (+https://github.com/oldmanbombin/MesonetMirror)"


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        print(f"  FAILED: {url} - {e}")
        return None


def fetch_bytes(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
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
        "stations": {},
    }

    print("Fetching camera manifest...")
    camera_manifest = fetch_json(CAMERA_MANIFEST_URL) or {}
    if not camera_manifest:
        print("WARNING: camera manifest fetch failed or returned nothing - camera images will be skipped this run, previous images (from the last successful run) stay live since we only overwrite what we successfully fetch.")

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

        current = fetch_json(CURRENT_URL_TEMPLATE.format(station=station))
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
