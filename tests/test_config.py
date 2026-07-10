"""Tests for configuration loading and validation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hokeypokey.config import AppConfig, ConfigError, load_config, parse_duration

# ---------------------------------------------------------------------------
# parse_duration
# ---------------------------------------------------------------------------


def test_parse_duration_seconds():
    assert parse_duration("30s") == 30


def test_parse_duration_minutes():
    assert parse_duration("5m") == 300


def test_parse_duration_hours():
    assert parse_duration("1h") == 3600


def test_parse_duration_compound():
    assert parse_duration("2h30m") == 9000


def test_parse_duration_full():
    assert parse_duration("1h15m30s") == 4530


def test_parse_duration_invalid():
    with pytest.raises(ConfigError):
        parse_duration("banana")


def test_parse_duration_empty():
    with pytest.raises(ConfigError):
        parse_duration("")


def test_parse_duration_days():
    assert parse_duration("1d") == 86400


def test_parse_duration_days_plural():
    assert parse_duration("7d") == 604800


def test_parse_duration_days_and_hours():
    assert parse_duration("1d12h") == 129600


def test_parse_duration_days_hours_minutes():
    assert parse_duration("2d6h30m") == 196200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "hokeypokey.toml"
    p.write_text(textwrap.dedent(content))
    return p


# ---------------------------------------------------------------------------
# load_config — happy path
# ---------------------------------------------------------------------------


def test_load_minimal_config(tmp_path):
    p = write_toml(
        tmp_path,
        """\
        [server]
        host = "127.0.0.1"
        port = 11371

        [cache]
        default_ttl = "10m"
    """,
    )
    config = load_config(p)
    assert isinstance(config, AppConfig)
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 11371
    assert config.cache.default_ttl == 600
    assert config.sources == []
    assert config.resolvers == []


def test_load_config_with_sources(tmp_path):
    p = write_toml(
        tmp_path,
        """\
        [server]
        host = "0.0.0.0"
        port = 11371

        [cache]
        default_ttl = "5m"

        [[sources]]
        name = "corporate-ldap"
        type = "ldap"
        priority = 10
        ttl = "5m"

        [sources.config]
        uri = "ldaps://ldap.example.com"
        base_dn = "ou=people,dc=example,dc=com"

        [sources.config.fields]
        email = "mail"
        username = "uid"

        [[sources]]
        name = "github-org"
        type = "github"
        priority = 50
        ttl = "15m"

        [sources.config]
        token_env = "GITHUB_TOKEN"

        [sources.config.fields]
        github_username = "login"
    """,
    )
    config = load_config(p)
    assert len(config.sources) == 2
    assert config.sources[0].name == "corporate-ldap"
    assert config.sources[0].priority == 10
    assert config.sources[0].ttl == 300
    assert config.sources[1].name == "github-org"
    assert config.sources[1].ttl == 900


def test_load_config_with_resolver(tmp_path):
    p = write_toml(
        tmp_path,
        """\
        [[sources]]
        name = "ldap"
        type = "ldap"
        priority = 10

        [sources.config.fields]
        email = "mail"
        github_id = "githubUsername"

        [[sources]]
        name = "github"
        type = "github"
        priority = 50

        [sources.config.fields]
        github_username = "login"

        [[resolvers]]
        name = "ldap-to-github"
        trigger_source = "ldap"
        trigger_field = "github_id"
        target_source = "github"
        target_field = "github_username"
    """,
    )
    config = load_config(p)
    assert len(config.resolvers) == 1
    r = config.resolvers[0]
    assert r.name == "ldap-to-github"
    assert r.trigger_source == "ldap"
    assert r.trigger_field == "github_id"
    assert r.target_source == "github"
    assert r.target_field == "github_username"


def test_load_config_tls(tmp_path):
    p = write_toml(
        tmp_path,
        """\
        [server]
        tls_cert = "/etc/ssl/cert.pem"
        tls_key = "/etc/ssl/key.pem"
    """,
    )
    config = load_config(p)
    assert config.server.tls_cert == "/etc/ssl/cert.pem"
    assert config.server.tls_key == "/etc/ssl/key.pem"


def test_load_config_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nonexistent.toml")


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


def test_duplicate_source_names(tmp_path):
    p = write_toml(
        tmp_path,
        """\
        [[sources]]
        name = "my-source"
        type = "ldap"
        priority = 10

        [[sources]]
        name = "my-source"
        type = "github"
        priority = 20
    """,
    )
    with pytest.raises(ConfigError, match="Duplicate source name"):
        load_config(p)


def test_resolver_unknown_trigger_source(tmp_path):
    p = write_toml(
        tmp_path,
        """\
        [[sources]]
        name = "github"
        type = "github"
        priority = 10

        [[resolvers]]
        name = "bad-resolver"
        trigger_source = "nonexistent"
        trigger_field = "github_id"
        target_source = "github"
        target_field = "github_username"
    """,
    )
    with pytest.raises(ConfigError, match="trigger_source"):
        load_config(p)


def test_resolver_unknown_target_source(tmp_path):
    p = write_toml(
        tmp_path,
        """\
        [[sources]]
        name = "ldap"
        type = "ldap"
        priority = 10

        [[resolvers]]
        name = "bad-resolver"
        trigger_source = "ldap"
        trigger_field = "github_id"
        target_source = "nonexistent"
        target_field = "github_username"
    """,
    )
    with pytest.raises(ConfigError, match="target_source"):
        load_config(p)


def test_duplicate_field_names_across_sources(tmp_path):
    p = write_toml(
        tmp_path,
        """\
        [[sources]]
        name = "ldap"
        type = "ldap"
        priority = 10

        [sources.config.fields]
        email = "mail"

        [[sources]]
        name = "github"
        type = "github"
        priority = 50

        [sources.config.fields]
        email = "email"
    """,
    )
    with pytest.raises(ConfigError, match="Field name 'email'"):
        load_config(p)


def test_source_missing_required_field(tmp_path):
    p = write_toml(
        tmp_path,
        """\
        [[sources]]
        type = "ldap"
        priority = 10
    """,
    )
    with pytest.raises(ConfigError, match="missing required field 'name'"):
        load_config(p)


def test_source_priority_must_be_positive(tmp_path):
    p = write_toml(
        tmp_path,
        """\
        [[sources]]
        name = "ldap"
        type = "ldap"
        priority = 0
    """,
    )
    with pytest.raises(ConfigError, match="priority must be a positive integer"):
        load_config(p)


# ---------------------------------------------------------------------------
# Cache: max_size / max_stale
# ---------------------------------------------------------------------------


def test_cache_max_size_default_is_bounded(tmp_path):
    p = write_toml(tmp_path, "[cache]\n")
    config = load_config(p)
    assert config.cache.max_size == 10_000


def test_cache_max_size_zero_means_unlimited(tmp_path):
    p = write_toml(tmp_path, "[cache]\nmax_size = 0\n")
    config = load_config(p)
    assert config.cache.max_size is None


def test_cache_max_size_negative_rejected(tmp_path):
    p = write_toml(tmp_path, "[cache]\nmax_size = -1\n")
    with pytest.raises(ConfigError, match="max_size"):
        load_config(p)


def test_cache_max_stale_parsed(tmp_path):
    p = write_toml(tmp_path, '[cache]\nmax_stale = "30m"\n')
    config = load_config(p)
    assert config.cache.max_stale == 1800


def test_cache_max_stale_default(tmp_path):
    p = write_toml(tmp_path, "[cache]\n")
    config = load_config(p)
    assert config.cache.max_stale == 3600


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_defaults(tmp_path):
    p = write_toml(tmp_path, "[server]\n")
    config = load_config(p)
    assert config.rate_limit.enabled is True
    assert config.rate_limit.requests == 60
    assert config.rate_limit.window == 60
    assert config.rate_limit.trust_forwarded_for is False


def test_rate_limit_section_parsed(tmp_path):
    p = write_toml(
        tmp_path,
        """\
        [rate_limit]
        enabled = true
        requests = 10
        window = "30s"
        trust_forwarded_for = true
    """,
    )
    config = load_config(p)
    assert config.rate_limit.requests == 10
    assert config.rate_limit.window == 30
    assert config.rate_limit.trust_forwarded_for is True


def test_rate_limit_can_be_disabled(tmp_path):
    p = write_toml(tmp_path, "[rate_limit]\nenabled = false\n")
    config = load_config(p)
    assert config.rate_limit.enabled is False


def test_rate_limit_requests_must_be_positive(tmp_path):
    p = write_toml(tmp_path, "[rate_limit]\nrequests = 0\n")
    with pytest.raises(ConfigError, match="rate_limit.requests"):
        load_config(p)


# ---------------------------------------------------------------------------
# Server: access_log
# ---------------------------------------------------------------------------


def test_access_log_default_on(tmp_path):
    p = write_toml(tmp_path, "[server]\n")
    config = load_config(p)
    assert config.server.access_log is True


def test_access_log_can_be_disabled(tmp_path):
    p = write_toml(tmp_path, "[server]\naccess_log = false\n")
    config = load_config(p)
    assert config.server.access_log is False


# ---------------------------------------------------------------------------
# resolver_max_depth
# ---------------------------------------------------------------------------


def test_resolver_max_depth_default(tmp_path):
    p = write_toml(tmp_path, "[server]\n")
    config = load_config(p)
    assert config.resolver_max_depth == 2


def test_resolver_max_depth_parsed(tmp_path):
    p = write_toml(tmp_path, "resolver_max_depth = 3\n")
    config = load_config(p)
    assert config.resolver_max_depth == 3


def test_resolver_max_depth_negative_rejected(tmp_path):
    p = write_toml(tmp_path, "resolver_max_depth = -1\n")
    with pytest.raises(ConfigError, match="resolver_max_depth"):
        load_config(p)
