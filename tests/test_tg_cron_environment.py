import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APPLY_SCRIPT = ROOT / "rootfs" / "app" / "apply-repo-path.sh"
COMPOSE_FILE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"


class TgCronEnvironmentTests(unittest.TestCase):
    def test_cron_receives_download_timeout_and_concurrency(self):
        source = APPLY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'CRON_TG_DOWNLOAD_TIMEOUT=$(cron_quote "${TG_DOWNLOAD_TIMEOUT:-3600}")',
            source,
        )
        self.assertIn(
            'CRON_TG_MAX_CONCURRENT=$(cron_quote "${TG_MAX_CONCURRENT:-1}")',
            source,
        )
        self.assertIn('TG_DOWNLOAD_TIMEOUT="$CRON_TG_DOWNLOAD_TIMEOUT"', source)
        self.assertIn('TG_MAX_CONCURRENT="$CRON_TG_MAX_CONCURRENT"', source)

    def test_compose_and_env_example_expose_the_same_defaults(self):
        compose = COMPOSE_FILE.read_text(encoding="utf-8")
        env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

        compose_defaults = {
            'image: hoya0803/ipaes:${IPAES_TAG:-latest}',
            '${HOST_PORT_NGINX:-8080}:80',
            '${HOST_PORT_WEBUI:-8085}:8085',
            '${IPA_DIR:-./data/ipa}:/data/ipa',
            '${ICONS_DIR:-./data/icons}:/data/icons',
            'REPO_NAME=${REPO_NAME:-Private IPA Repo}',
            'REPO_IDENTIFIER=${REPO_IDENTIFIER:-com.private.ipa.repo}',
            'TG_PROXY=${TG_PROXY:-}',
            'TG_SCAN_CRON=${TG_SCAN_CRON:-0 1 * * *}',
            'TG_SCAN_HOURS=${TG_SCAN_HOURS:-25}',
            'TG_DOWNLOAD_TIMEOUT=${TG_DOWNLOAD_TIMEOUT:-3600}',
            'TG_MAX_CONCURRENT=${TG_MAX_CONCURRENT:-1}',
            'TZ=${TZ:-Asia/Shanghai}',
        }
        for expected in compose_defaults:
            self.assertIn(expected, compose)

        env_defaults = {
            'IPAES_TAG=latest',
            'HOST_PORT_NGINX=8080',
            'HOST_PORT_WEBUI=8085',
            'IPA_DIR=./data/ipa',
            'ICONS_DIR=./data/icons',
            'TG_DOWNLOAD_TIMEOUT=3600',
            'TG_MAX_CONCURRENT=1',
        }
        for expected in env_defaults:
            self.assertIn(expected, env_example)


if __name__ == "__main__":
    unittest.main()
