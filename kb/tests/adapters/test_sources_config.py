"""Loading kb-sources.yaml: interpolation, coercion, the review gate.

These are the failures an operator hits at 9am with a half-configured file, so
each one has to say what is wrong and where.
"""

from __future__ import annotations

import pytest

from kb.sources_config import (
    ConfigError,
    GitLabSource,
    NotReviewed,
    SourcesConfig,
    interpolate,
    load,
)

MINIMAL = """
reviewed: true
sources:
  - id: confluence-eng
    kind: confluence
    enabled: true
    spaces: [ENG, PRODUCT]
"""


def write(tmp_path, text: str):
    path = tmp_path / "kb-sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_any_value_can_come_from_the_environment(tmp_path):
    # Covers: FR-CFG-01
    path = write(tmp_path, """
reviewed: true
sources:
  - id: gitlab
    kind: gitlab
    enabled: true
    base_url: ${GITLAB_BASE_URL}
    projects:
      - path: ${GITLAB_PROJECT:-platform/specs}
""")
    config = load(path, env={"GITLAB_BASE_URL": "https://gitlab.internal"})
    source = config.source("gitlab")
    assert isinstance(source, GitLabSource)
    assert source.base_url == "https://gitlab.internal"
    assert source.projects[0].path == "platform/specs"  # the default was used


def test_an_unset_variable_names_the_file_line_and_variable(tmp_path):
    # Covers: FR-CFG-02
    # An empty string here would produce a source pointed at nowhere that fails
    # much later, somewhere less obvious.
    path = write(tmp_path, """
reviewed: true
sources:
  - id: gitlab
    kind: gitlab
    base_url: ${GITLAB_BASE_URL}
""")
    with pytest.raises(ConfigError) as excinfo:
        load(path, env={})
    message = str(excinfo.value)
    assert "GITLAB_BASE_URL" in message
    assert "kb-sources.yaml:6" in message, "the line the variable is actually on"


def test_an_empty_variable_is_treated_as_unset(tmp_path):
    # Covers: FR-CFG-02
    path = write(tmp_path, "reviewed: ${KB_REVIEWED}\n")
    with pytest.raises(ConfigError):
        load(path, env={"KB_REVIEWED": ""})
    assert load(path, env={"KB_REVIEWED": "true"}).reviewed is True


def test_interpolated_values_become_the_type_the_field_expects(tmp_path):
    # Covers: FR-CFG-03
    path = write(tmp_path, """
reviewed: true
sources:
  - id: gitlab
    kind: gitlab
    enabled: ${KB_GITLAB_ENABLED:-false}
    base_url: https://gitlab.internal
    mechanisms:
      webhook: { enabled: ${KB_GITLAB_WEBHOOK:-false} }
""")
    source = load(path, env={"KB_GITLAB_ENABLED": "true"}).source("gitlab")
    assert source.enabled is True, "a real boolean, not the string 'true'"
    assert source.mechanisms.webhook.enabled is False


def test_credentials_are_named_not_written(tmp_path):
    # Covers: FR-CFG-04
    source = load(write(tmp_path, """
reviewed: true
sources:
  - id: gitlab
    kind: gitlab
    base_url: https://gitlab.internal
    token_env: GITLAB_TOKEN
""")).source("gitlab")
    assert source.token_env == "GITLAB_TOKEN"
    assert not hasattr(source, "token"), "the config must have nowhere to put a secret"


def test_sync_refuses_to_run_until_the_file_is_reviewed(tmp_path):
    # Covers: FR-CFG-05
    config = load(write(tmp_path, "reviewed: false\nsources: []\n"))
    with pytest.raises(NotReviewed) as excinfo:
        config.require_reviewed()
    assert "reviewed: true" in str(excinfo.value)
    load(write(tmp_path, MINIMAL)).require_reviewed()  # no raise


def test_a_source_is_disabled_unless_it_says_otherwise(tmp_path):
    # Covers: FR-CFG-06
    # Discovery appends candidates; none of them may take effect unseen.
    config = load(write(tmp_path, """
reviewed: true
sources:
  - id: proposed
    kind: confluence
  - id: chosen
    kind: confluence
    enabled: true
"""))
    assert [s.id for s in config.enabled_sources()] == ["chosen"]


def test_source_mechanisms_override_the_defaults_field_by_field(tmp_path):
    # Covers: FR-CFG-07
    # Overriding one switch must not silently reset the others.
    config = load(write(tmp_path, """
reviewed: true
defaults:
  mechanisms:
    incremental: { enabled: true, every: 15m }
    full_scrape: { enabled: true, at: "03:00" }
    webhook: { enabled: false }
sources:
  - id: gitlab
    kind: gitlab
    base_url: https://gitlab.internal
    mechanisms:
      webhook: { enabled: true }
      incremental: { every: 5m }
"""))
    mechanisms = config.mechanisms_for(config.source("gitlab"))
    assert mechanisms.webhook.enabled is True
    assert mechanisms.incremental.every == "5m"
    assert mechanisms.incremental.enabled is True, "the default survived the override"
    assert mechanisms.full_scrape.at == "03:00"


def test_gitlab_scopes_default_to_markdown_across_the_whole_repo(tmp_path):
    # No Covers: this is the config shape only. FR-SRC-03 is claimed by the
    # GitLab adapter's own tests, once that adapter exists.
    config = load(write(tmp_path, """
reviewed: true
sources:
  - id: gitlab
    kind: gitlab
    base_url: https://gitlab.internal
    projects:
      - path: platform/specs
      - path: media-service
        ref: main
        scopes:
          - { dir: docs/ADRs, type: spec, tags: [adr] }
          - dir: docs
            patterns: ["**/*.md", "**/*.mdx"]
            exclude: ["**/CHANGELOG.md"]
"""))
    whole_repo, scoped = config.source("gitlab").projects
    assert whole_repo.scopes[0].dir == "." and whole_repo.scopes[0].patterns == ("**/*.md",)
    assert scoped.ref == "main"
    assert scoped.scopes[0].type == "spec" and scoped.scopes[0].tags == ("adr",)
    assert scoped.scopes[1].exclude == ("**/CHANGELOG.md",)


def test_an_unknown_source_kind_is_rejected_with_the_known_ones_listed(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, "reviewed: true\nsources:\n  - id: x\n    kind: sharepoint\n"))
    assert "kind" in str(excinfo.value)


def test_a_typo_in_a_field_name_is_rejected_rather_than_ignored(tmp_path):
    # Silently ignoring `space:` would mean a source that quietly reads nothing.
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, "reviewed: true\nsources:\n  - id: x\n    kind: confluence\n    space: ENG\n"))
    assert "space" in str(excinfo.value)


def test_malformed_yaml_reports_where(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, "reviewed: true\nsources: [\n"))
    assert "kb-sources.yaml" in str(excinfo.value)


def test_a_missing_file_says_what_to_copy(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load(tmp_path / "absent.yaml")
    assert "kb-sources.yaml.example" in str(excinfo.value)


def test_asking_for_an_unknown_source_lists_the_known_ones(tmp_path):
    config = load(write(tmp_path, MINIMAL))
    with pytest.raises(ConfigError) as excinfo:
        config.source("nope")
    assert "confluence-eng" in str(excinfo.value)


def test_interpolation_is_a_pure_function_of_text_and_env():
    assert interpolate("a ${X} b", {"X": "1"}) == "a 1 b"
    assert interpolate("${X:-fallback}", {}) == "fallback"
    assert interpolate("no variables here", {}) == "no variables here"


def test_an_empty_config_is_valid_and_does_nothing():
    config = SourcesConfig()
    assert config.reviewed is False
    assert config.enabled_sources() == ()


def test_a_variable_written_in_a_comment_is_left_alone(tmp_path):
    # Covers: FR-CFG-01
    # The file's own header explains ${VAR}; resolving that would make the
    # documentation break the file it documents.
    path = write(tmp_path, """
# ${VAR} and ${VAR:-default} work anywhere.
reviewed: true
sources:
  - id: confluence-eng   # ${ALSO_NOT_A_VARIABLE}
    kind: confluence
    enabled: true
""")
    assert load(path, env={}).source("confluence-eng").enabled is True


def test_a_hash_inside_a_quoted_value_does_not_hide_a_variable(tmp_path):
    path = write(tmp_path, """
reviewed: true
sources:
  - id: jira
    kind: jira
    jql: "project = PAY AND text ~ '#${TICKET}'"
""")
    assert "#42" in load(path, env={"TICKET": "42"}).source("jira").jql
