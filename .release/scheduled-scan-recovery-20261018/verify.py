"""Read-only, redacted acceptance checks for the scheduled-scan incident."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
import zipfile


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


backup = Path("/data/.incident-backups/20261018-234742-scheduled-scan")
before = json.loads((backup / "baseline.json").read_text())
log = Path("/logs/tg-cron.log").read_text(errors="replace")
current = log.rsplit("==== TG scan triggered at ", 1)[-1]
assert "==== TG scan done at " in current, "recovery scan is still running"
assert "📊 总结:" in current, "scan did not reach its summary"
assert "Bot 通知已发送:" in current, "no accepted notification in recovery round"
assert not any(s in current for s in ["[ERROR]", "下载失败", "扫描群异常", "异常退出", "通知发送失败"]), "recovery round contains failures"

protected = Path(before["protected_path"])
config_unchanged = {
    path: sha(Path(path)) == expected
    for path, expected in before["config_sha256"].items()
}
assert all(config_unchanged.values()), "unrelated configuration changed"
assert protected.stat().st_size == before["protected_size"]
assert sha(protected) == before["protected_sha256"], "protected X IPA changed"
assert sha(Path("/tmp/repo_base_url.applied")) == before["repo_base_sha256"]

repo = json.loads(Path("/data/repo.json").read_text())
altstore = json.loads(Path("/data/_altstore.json").read_text())
assert sorted(repo) == before["repo_keys"]
assert sorted({key for app in repo.get("apps", []) for key in app}) == before["app_keys"]
assert repo.get("apps") == altstore.get("apps"), "two subscription files differ"
downloaded = [line.split("  OK: ", 1)[1] for line in current.splitlines() if "  OK: " in line]
assert downloaded, "no real IPA completed"
targets = []
for filename in downloaded:
    matches = [p for p in Path("/data/ipa").rglob("*.ipa") if p.name == filename and ".archive" not in p.parts]
    assert len(matches) == 1, "completed IPA missing or ambiguous"
    path = matches[0]
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None, "IPA ZIP CRC check failed"
        assert any(n.startswith("Payload/") and n.endswith(".app/Info.plist") for n in archive.namelist())
    assert not path.with_suffix(path.suffix + ".part").exists(), "target partial remains"
    apps = [a for a in repo["apps"] if urllib.parse.unquote(urllib.parse.urlsplit(a["downloadURL"]).path).endswith("/" + filename)]
    assert len(apps) == 1, "completed IPA missing from subscription"
    parsed = urllib.parse.urlsplit(apps[0]["downloadURL"])
    local_url = urllib.parse.urlunsplit(("http", "127.0.0.1", parsed.path, parsed.query, ""))
    request = urllib.request.Request(local_url, headers={"Range": "bytes=0-31"})
    with urllib.request.urlopen(request, timeout=15) as response:
        assert response.status == 206
        assert response.read(32).startswith(b"PK")
    targets.append({"filename": filename, "size": path.stat().st_size, "zip_crc": "ok", "published": True, "range_status": 206})

base = Path("/tmp/repo_base_url.applied").read_text().strip()
parsed = urllib.parse.urlsplit(base)
local_url = urllib.parse.urlunsplit(("http", "127.0.0.1", parsed.path, parsed.query, ""))
with urllib.request.urlopen(local_url, timeout=15) as response:
    assert response.status == 200
    assert len(json.load(response)["apps"]) == len(repo["apps"])
cron = Path("/etc/cron.d/ipaes").read_text()
schedule = next(line.split(" root ", 1)[0] for line in cron.splitlines() if " root /app/run-tg-scan.sh" in line)
summary = next(line.split("[INFO] ", 1)[-1] for line in current.splitlines() if "📊 总结:" in line)
proxy = json.loads(Path("/config/proxy.json").read_text())
assert proxy["url"] == "http://10.0.0.2:6666" and proxy["enabled"]
assert schedule == "0 1,13 * * *"
assert 'TG_SCAN_HOURS="25"' in cron
assert 'TG_PROXY_OVERRIDE=""' in cron
result = {
    "verified_at": datetime.datetime.now().isoformat(),
    "proxy": proxy["url"],
    "cron_schedule": schedule,
    "scheduled_lookback_hours": 25,
    "recovery_lookback_hours": 36,
    "message_limit_per_group": 500,
    "groups_checked": current.count("群扫描结果 ["),
    "summary": summary,
    "notification_accepted": True,
    "last_scan": json.loads(Path("/config/state.json").read_text())["last_scan"],
    "apps_before": before["app_count"],
    "apps_after": len(repo["apps"]),
    "targets": targets,
    "unrelated_config_unchanged": all(config_unchanged.values()),
    "protected_x_10_76_unchanged": True,
    "subscription_schema_and_url_unchanged": True,
    "subscription_status": 200,
    "backup": str(backup),
}
print(json.dumps(result, ensure_ascii=False, indent=2))
