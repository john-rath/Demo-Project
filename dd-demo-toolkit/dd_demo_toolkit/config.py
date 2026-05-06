"""
Configuration loader for vertical definitions.

Loads and validates vertical configs from YAML files, resolves placeholders,
and provides utilities for discovering available verticals.
"""

import os
from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml


class ConfigError(Exception):
    """Raised when config validation or loading fails."""
    pass


class ConfigLoader:
    """Loader and validator for vertical configurations."""

    REQUIRED_CONFIG_FIELDS = [
        "vertical",
        "locations",
        "device_categories",
        "services",
    ]

    REQUIRED_VERTICAL_FIELDS = ["name", "display_name", "env_prefix"]
    REQUIRED_LOCATION_FIELDS = ["dimensions"]
    REQUIRED_DEVICE_CATEGORY_FIELDS = ["devices"]
    REQUIRED_SERVICE_FIELDS = ["name", "language", "operations"]

    def __init__(self, verticals_dir: str = "verticals"):
        """
        Initialize config loader.

        Args:
            verticals_dir: Directory containing vertical config subdirectories.
        """
        self.verticals_dir = Path(verticals_dir)

    def list_verticals(self) -> List[str]:
        """
        List available vertical names by scanning verticals directory.

        Returns:
            List of vertical names (directory names in verticals_dir).
        """
        if not self.verticals_dir.exists():
            return []

        verticals = []
        for item in self.verticals_dir.iterdir():
            if item.is_dir() and (item / "config.yaml").exists():
                verticals.append(item.name)

        return sorted(verticals)

    def get_vertical_path(self, name: str) -> Path:
        """
        Get the full path to a vertical's directory.

        Args:
            name: Vertical name.

        Returns:
            Path to the vertical directory.

        Raises:
            ConfigError: If vertical directory doesn't exist.
        """
        vertical_path = self.verticals_dir / name
        if not vertical_path.exists():
            raise ConfigError(f"Vertical '{name}' not found at {vertical_path}")
        return vertical_path

    def list_overlays(self, vertical_name: str) -> List[str]:
        """
        List available sub-vertical overlay names for a vertical.

        Discovered by scanning ``verticals/<vertical>/overlays/`` for either
        ``<name>.yaml`` files (single-file overlays that only add devices /
        services to the simulator config) or ``<name>/`` sub-directories
        (full-resource overlays that may also carry monitors/dashboards/etc.).
        A given overlay may exist as either, or both — the YAML side is
        loaded by ``load_vertical`` and the directory side is consumed by
        the resource managers.

        Returns:
            Sorted list of overlay names.
        """
        overlays_dir = self.verticals_dir / vertical_name / "overlays"
        if not overlays_dir.exists():
            return []

        names: set = set()
        for item in overlays_dir.iterdir():
            if item.is_dir():
                names.add(item.name)
            elif item.is_file() and item.suffix in (".yaml", ".yml"):
                names.add(item.stem)
        return sorted(names)

    def load_vertical(
        self,
        name: str,
        sub_vertical: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Load and parse a vertical config YAML file.

        Args:
            name: Vertical name.
            sub_vertical: Optional sub-vertical overlay name. When provided,
                the file at ``verticals/<name>/overlays/<sub_vertical>.yaml``
                is loaded and additively merged onto the base config.
                Lists (e.g. ``device_categories.<cat>.devices`` and
                top-level ``services``) are appended; dictionaries are
                merged key-by-key with the overlay winning on conflict.
                The base ``vertical.env_prefix`` is preserved so the metric
                namespace does not change — overlays add devices/services
                under the same ``hospital.*`` namespace as the base
                healthcare vertical.

        Returns:
            Parsed config dictionary.

        Raises:
            ConfigError: If config is malformed or missing required fields.
        """
        vertical_path = self.get_vertical_path(name)
        config_file = vertical_path / "config.yaml"

        if not config_file.exists():
            raise ConfigError(f"Config file not found at {config_file}")

        try:
            with open(config_file, "r") as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"Failed to parse YAML in {config_file}: {e}")
        except IOError as e:
            raise ConfigError(f"Failed to read {config_file}: {e}")

        if not isinstance(config, dict):
            raise ConfigError(f"Config must be a YAML dictionary, got {type(config)}")

        # Validate required top-level fields
        for field in self.REQUIRED_CONFIG_FIELDS:
            if field not in config:
                raise ConfigError(f"Missing required field '{field}' in config")

        # Validate vertical section
        vertical = config.get("vertical", {})
        for field in self.REQUIRED_VERTICAL_FIELDS:
            if field not in vertical:
                raise ConfigError(f"Missing required field 'vertical.{field}'")

        # Validate locations section
        locations = config.get("locations", {})
        if "dimensions" not in locations:
            raise ConfigError("Missing required field 'locations.dimensions'")

        # Validate device_categories section
        device_categories = config.get("device_categories", {})
        if not isinstance(device_categories, dict) or not device_categories:
            raise ConfigError("device_categories must be a non-empty dictionary")

        for cat_name, cat_config in device_categories.items():
            if "devices" not in cat_config:
                raise ConfigError(f"Missing 'devices' in device_category '{cat_name}'")
            if not isinstance(cat_config.get("devices"), list):
                raise ConfigError(f"'devices' must be a list in category '{cat_name}'")

        # Validate services section
        services = config.get("services", [])
        if not isinstance(services, list):
            raise ConfigError("'services' must be a list")

        for idx, service in enumerate(services):
            if not isinstance(service, dict):
                raise ConfigError(f"Service at index {idx} must be a dictionary")
            for field in self.REQUIRED_SERVICE_FIELDS:
                if field not in service:
                    raise ConfigError(f"Service at index {idx} missing required field '{field}'")
            if not isinstance(service.get("operations"), list):
                raise ConfigError(f"Service at index {idx}: 'operations' must be a list")

        # Apply sub-vertical overlay (additive merge) if requested. Done
        # before placeholder resolution so overlay strings can also reference
        # {prefix}.
        if sub_vertical:
            overlay_file = (
                vertical_path / "overlays" / f"{sub_vertical}.yaml"
            )
            if overlay_file.exists():
                try:
                    with open(overlay_file, "r") as f:
                        overlay = yaml.safe_load(f) or {}
                except yaml.YAMLError as e:
                    raise ConfigError(
                        f"Failed to parse overlay {overlay_file}: {e}"
                    )
                except IOError as e:
                    raise ConfigError(
                        f"Failed to read overlay {overlay_file}: {e}"
                    )
                if not isinstance(overlay, dict):
                    raise ConfigError(
                        f"Overlay {overlay_file} must be a YAML dict"
                    )
                config = self._merge_overlay(config, overlay)
            else:
                # An overlay directory may exist without a top-level YAML
                # (e.g. resource-only overlays). That's fine — just log.
                pass

        # Resolve placeholders
        prefix = vertical.get("env_prefix", "")
        config = self._resolve_placeholders(config, prefix)

        return config

    @staticmethod
    def _merge_overlay(
        base: Dict[str, Any], overlay: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Additive merge of an overlay onto the base vertical config.

        Rules:
          - ``device_categories.<cat>.devices`` lists are concatenated.
          - Top-level ``services`` list is concatenated.
          - ``locations.dimensions``: existing dimensions keep their values;
            overlay-only dimensions are appended.
          - All other dicts are merged recursively, scalars from the overlay
            win on conflict.
          - The ``vertical`` block is intentionally NOT modified — overlays
            cannot rename the vertical or change ``env_prefix``.
        """
        result = dict(base)

        # device_categories — concatenate device lists per category
        if "device_categories" in overlay:
            base_cats = dict(result.get("device_categories", {}))
            for cat_name, cat_config in overlay["device_categories"].items():
                if cat_name in base_cats:
                    merged_cat = dict(base_cats[cat_name])
                    merged_cat["devices"] = list(
                        merged_cat.get("devices", [])
                    ) + list(cat_config.get("devices", []))
                    # department_pool: overlay wins if explicitly set
                    if "department_pool" in cat_config:
                        merged_cat["department_pool"] = cat_config[
                            "department_pool"
                        ]
                    base_cats[cat_name] = merged_cat
                else:
                    base_cats[cat_name] = cat_config
            result["device_categories"] = base_cats

        # services — concatenate
        if "services" in overlay:
            result["services"] = list(result.get("services", [])) + list(
                overlay["services"]
            )

        # locations.dimensions — append overlay-only dimensions
        if "locations" in overlay and "dimensions" in overlay["locations"]:
            base_loc = dict(result.get("locations", {}))
            base_dims = list(base_loc.get("dimensions", []))
            base_dim_names = {d.get("name") for d in base_dims}
            for dim in overlay["locations"]["dimensions"]:
                if dim.get("name") not in base_dim_names:
                    base_dims.append(dim)
            base_loc["dimensions"] = base_dims
            result["locations"] = base_loc

        return result

    def _resolve_placeholders(self, obj: Any, prefix: str) -> Any:
        """
        Recursively resolve {prefix} placeholders in config.

        Args:
            obj: Config object (dict, list, or scalar).
            prefix: Prefix value to substitute.

        Returns:
            Config object with placeholders resolved.
        """
        if isinstance(obj, dict):
            return {k: self._resolve_placeholders(v, prefix) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_placeholders(item, prefix) for item in obj]
        elif isinstance(obj, str):
            return obj.replace("{prefix}", prefix)
        else:
            return obj


def load_vertical_config(name: str, verticals_dir: str = "verticals") -> Dict[str, Any]:
    """
    Convenience function to load a vertical config.

    Args:
        name: Vertical name.
        verticals_dir: Directory containing verticals.

    Returns:
        Parsed config dictionary.

    Raises:
        ConfigError: If config loading or validation fails.
    """
    loader = ConfigLoader(verticals_dir)
    return loader.load_vertical(name)


def list_available_verticals(verticals_dir: str = "verticals") -> List[str]:
    """
    Convenience function to list available verticals.

    Args:
        verticals_dir: Directory containing verticals.

    Returns:
        List of vertical names.
    """
    loader = ConfigLoader(verticals_dir)
    return loader.list_verticals()
