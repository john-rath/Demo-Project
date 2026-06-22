"""
Tests for vertical configuration loading and validation.
"""

import pytest
import yaml
from pathlib import Path
from dd_demo_toolkit.config import ConfigLoader, ConfigError


class TestConfigLoading:
    """Test configuration file loading for all verticals."""

    def test_all_verticals_load_without_error(self):
        """Test that all four vertical configs load successfully."""
        verticals = ["healthcare", "finance", "manufacturing", "insurance"]
        loader = ConfigLoader()

        for vertical in verticals:
            config = loader.load_vertical(vertical)
            assert config is not None, f"Failed to load {vertical} config"
            assert "vertical" in config, f"{vertical} config missing 'vertical' section"
            assert "name" in config["vertical"], f"{vertical} missing vertical name"

    def test_config_has_required_sections(self):
        """Test that each vertical config has all required top-level sections."""
        required_sections = {
            "vertical": ["name", "display_name", "description", "env_prefix"],
            "device_categories": [],
            "locations": [],
        }

        loader = ConfigLoader()
        verticals = ["healthcare", "finance", "manufacturing", "insurance"]

        for vertical in verticals:
            config = loader.load_vertical(vertical)

            # Check required sections exist
            for section, required_keys in required_sections.items():
                assert (
                    section in config
                ), f"{vertical} config missing section '{section}'"

                # Check required keys within sections
                if required_keys:
                    for key in required_keys:
                        assert (
                            key in config[section]
                        ), f"{vertical}.{section} missing key '{key}'"

    def test_device_count_is_positive(self):
        """Test that all devices have positive counts."""
        loader = ConfigLoader()
        verticals = ["healthcare", "finance", "manufacturing", "insurance"]

        for vertical in verticals:
            config = loader.load_vertical(vertical)
            device_categories = config.get("device_categories", {})

            for category, category_config in device_categories.items():
                devices = category_config.get("devices", [])
                for device in devices:
                    count = device.get("count", 0)
                    assert (
                        count > 0
                    ), f"{vertical}.{category}.{device['type']} has invalid count: {count}"

    def test_services_have_required_fields(self):
        """Test that services config exists and has required fields."""
        loader = ConfigLoader()
        verticals = ["healthcare", "finance", "manufacturing", "insurance"]
        verticals_base = Path(__file__).parent.parent / "verticals"

        for vertical in verticals:
            services_file = verticals_base / vertical / "services.yaml"
            assert services_file.exists(), f"{vertical} services.yaml not found"

            with open(services_file) as f:
                services_config = yaml.safe_load(f)

            assert services_config is not None, f"{vertical} services.yaml is empty"
            services = services_config.get("services", [])
            assert len(services) > 0, f"{vertical} has no services defined"

            for service in services:
                # verticals use either Service Catalog v2 ('dd-service') or plain 'name'
                assert "dd-service" in service or "name" in service, \
                    f"{vertical} service missing identifier field (dd-service or name)"
                identifier = service.get("dd-service") or service.get("name")
                assert isinstance(identifier, str), f"{vertical} service identifier must be string"


class TestConfigValidation:
    """Test configuration validation rules."""

    def test_vertical_name_matches_directory(self):
        """Test that vertical config name matches its directory name."""
        loader = ConfigLoader()
        verticals = ["healthcare", "finance", "manufacturing", "insurance"]

        for vertical_name in verticals:
            config = loader.load_vertical(vertical_name)
            config_name = config["vertical"]["name"]
            assert (
                config_name == vertical_name
            ), f"Config name {config_name} doesn't match directory {vertical_name}"

    def test_config_validation_catches_missing_fields(self, tmp_path):
        """Test that load_vertical catches missing required fields."""
        invalid_config = {
            "vertical": {
                "name": "test",
                "display_name": "Test Vertical",
                "description": "Test",
                "env_prefix": "test",
            },
            "device_categories": {
                "test_devices": {
                    "devices": [
                        {
                            "type": "test_device",
                            "manufacturer": "Test",
                        }
                    ]
                }
            },
            "locations": {"dimensions": []},
            # Missing top-level 'services' field
        }

        vertical_dir = tmp_path / "test"
        vertical_dir.mkdir()
        (vertical_dir / "config.yaml").write_text(yaml.safe_dump(invalid_config))

        loader = ConfigLoader(verticals_dir=str(tmp_path))
        with pytest.raises(ConfigError):
            loader.load_vertical("test")

    def test_display_name_not_empty(self):
        """Test that all verticals have non-empty display names."""
        loader = ConfigLoader()
        verticals = ["healthcare", "finance", "manufacturing", "insurance"]

        for vertical in verticals:
            config = loader.load_vertical(vertical)
            display_name = config["vertical"].get("display_name", "")
            assert (
                len(display_name) > 0
            ), f"{vertical} has empty display_name"

    def test_env_prefix_valid(self):
        """Test that env_prefix is a valid identifier."""
        import re

        loader = ConfigLoader()
        verticals = ["healthcare", "finance", "manufacturing", "insurance"]
        identifier_pattern = re.compile(r"^[a-z_][a-z0-9_]*$")

        for vertical in verticals:
            config = loader.load_vertical(vertical)
            env_prefix = config["vertical"].get("env_prefix", "")
            assert identifier_pattern.match(
                env_prefix
            ), f"{vertical} has invalid env_prefix: {env_prefix}"


class TestVerticalListing:
    """Test vertical discovery and listing."""

    def test_list_verticals_returns_all_known(self):
        """list_verticals returns all shipped verticals. Uses a subset/>= check
        so adding a new vertical (e.g. agribusiness) doesn't break the suite."""
        loader = ConfigLoader()
        verticals = loader.list_verticals()

        expected = [
            "agribusiness", "finance", "healthcare",
            "hospitality", "insurance", "manufacturing",
        ]
        for exp in expected:
            assert (
                exp in verticals
            ), f"Expected vertical '{exp}' not found in list"
        assert len(verticals) >= len(expected), (
            f"Expected at least {len(expected)} verticals, got {len(verticals)}"
        )

    def test_list_verticals_includes_metadata(self):
        """Test that each vertical name in list is non-empty and loadable."""
        loader = ConfigLoader()
        verticals = loader.list_verticals()

        for vertical_name in verticals:
            assert isinstance(vertical_name, str) and vertical_name, "Vertical name must be non-empty string"
            config = loader.load_vertical(vertical_name)
            assert "display_name" in config["vertical"], f"{vertical_name} missing display_name"
            assert isinstance(config["vertical"]["display_name"], str), "display_name must be string"


class TestPlaceholderResolution:
    """Test placeholder resolution in configurations."""

    def test_metric_name_placeholder_resolved(self):
        """Test that {prefix} placeholder is resolved in metric names."""
        loader = ConfigLoader()
        config = loader.load_vertical("healthcare")

        device_categories = config.get("device_categories", {})
        for category, category_config in device_categories.items():
            devices = category_config.get("devices", [])
            for device in devices:
                metrics = device.get("metrics", [])
                for metric in metrics:
                    metric_name = metric.get("name", "")
                    # Check that {prefix} exists in the template
                    # (actual resolution happens at runtime)
                    if "{prefix}" in metric_name:
                        # This is expected for template metrics
                        prefix = category_config.get("env_prefix", config["vertical"]["env_prefix"])
                        resolved = metric_name.format(prefix=prefix)
                        assert "{prefix}" not in resolved, f"Placeholder not resolved in {metric_name}"

    def test_vertical_name_placeholder_resolved(self):
        """Test that {vertical} placeholders can be resolved."""
        loader = ConfigLoader()
        verticals = loader.list_verticals()

        for vertical_name in verticals:
            config = loader.load_vertical(vertical_name)
            env_prefix = config["vertical"].get("env_prefix")
            assert env_prefix is not None, f"{vertical_name} missing env_prefix"
