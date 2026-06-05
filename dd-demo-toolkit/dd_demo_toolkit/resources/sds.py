"""
Sensitive Data Scanner resource manager for dd-demo-toolkit.

Handles deployment and teardown of SDS scanning groups and rules.

The Datadog SDS API (v2) does not use fingerprint-based locking.  All
create/delete operations are independent REST calls that can be made without
carrying state between them.

Teardown identification:
  - Scanning GROUPS: the SDS API does not return tags on groups.  The toolkit
    embeds a machine-readable marker in the group description:
      ``[dd-demo-toolkit:vertical:<name>]``
    Teardown matches groups whose description contains this marker.
  - Scanning RULES: the SDS API returns and stores tags on rules.  The toolkit
    tags rules with ``dd-demo-toolkit:true`` and ``vertical:<name>``.  Teardown
    matches rules by these tags.

Rules must be deleted before their parent group (the API rejects deleting a
group that still has rules).  As an extra safeguard, deleting a group also
auto-deletes its rules on the Datadog side.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from dd_demo_toolkit.utils.dd_api import DatadogAPIClient


logger = logging.getLogger(__name__)


class SDSManager:
    """Manages SDS scanning groups and rules for a vertical."""

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    def deploy(
        self,
        vertical_path: str,
        api_client: DatadogAPIClient,
        tags: Optional[Dict[str, str]] = None,
        dry_run: bool = False,
        vertical_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Deploy SDS scanning groups and rules from sds.yaml.

        Reads sds.yaml from vertical_path.  Each top-level ``sds_groups``
        entry becomes one SDS scanning group.  Rules nested under the group
        are created after the group and reference it by the ID returned from
        the create-group API call.

        Args:
            vertical_path: Directory containing sds.yaml.
            api_client: Datadog API client.
            tags: Additional key/value tags injected into every resource.
            dry_run: Skip API calls; log what would be created.
            vertical_name: Override for the vertical name used in tag injection.

        Returns:
            Dict with created_ids, errors, total_created, total_errors.
        """
        vertical_path_obj = Path(vertical_path)
        sds_file = vertical_path_obj / "sds.yaml"

        result: Dict[str, Any] = {
            "created_ids": [],
            "errors": [],
            "total_created": 0,
            "total_errors": 0,
        }

        if not sds_file.exists():
            logger.info("No sds.yaml found at %s", sds_file)
            return result

        if vertical_name is None:
            vertical_name = vertical_path_obj.name

        try:
            config = yaml.safe_load(sds_file.read_text())
        except yaml.YAMLError as e:
            result["errors"].append(f"Failed to parse sds.yaml: {e}")
            result["total_errors"] += 1
            return result

        groups = (config or {}).get("sds_groups", [])
        if not groups:
            logger.info("No sds_groups defined in sds.yaml")
            return result

        base_tags = self._build_tags(vertical_name, tags)

        # Fetch the SDS config to get the root configuration ID, which is
        # required in the relationships block of every scanning-group create call.
        if dry_run:
            config_id = "dry-run-config-id"
        else:
            try:
                cfg_response = api_client.get_sds_config()
                if not isinstance(cfg_response, dict):
                    raise RuntimeError(
                        f"SDS config returned unexpected type {type(cfg_response).__name__}. "
                        "SDS may not be enabled for this Datadog org."
                    )
                config_id = cfg_response.get("data", {}).get("id")
                if not config_id:
                    raise RuntimeError(
                        "SDS config response has no configuration ID. "
                        "SDS may not be enabled for this Datadog org."
                    )
            except RuntimeError as e:
                result["errors"].append(f"Failed to fetch SDS config: {e}")
                result["total_errors"] += 1
                logger.warning(
                    "SDS deployment skipped — %s. "
                    "Enable Sensitive Data Scanner in the Datadog org to use this feature.",
                    e,
                )
                return result

        for group_cfg in groups:
            self._deploy_group(
                group_cfg, base_tags, vertical_name, config_id, api_client, dry_run, result
            )

        logger.info(
            "SDS deployment complete: %d created, %d errors",
            result["total_created"], result["total_errors"],
        )
        return result

    def _deploy_group(
        self,
        group_cfg: Dict[str, Any],
        base_tags: List[str],
        vertical_name: str,
        config_id: str,
        api_client: DatadogAPIClient,
        dry_run: bool,
        result: Dict[str, Any],
    ) -> Optional[str]:
        """Create one scanning group, then create its rules. Returns group_id or None."""
        group_name = group_cfg.get("name", "unnamed-sds-group")
        rules_cfg = group_cfg.get("rules", [])

        # Embed a machine-readable marker in the description so teardown can
        # identify toolkit-managed groups (the SDS API does not return tags on groups).
        toolkit_marker = f"[dd-demo-toolkit:vertical:{vertical_name}]"
        raw_desc = group_cfg.get("description", "")
        description = f"{raw_desc} {toolkit_marker}".strip() if raw_desc else toolkit_marker

        payload = {
            "data": {
                "type": "sensitive_data_scanner_group",
                "attributes": {
                    "name": group_name,
                    "description": description,
                    "filter": {"query": group_cfg.get("filter_query", "*")},
                    "is_enabled": group_cfg.get("is_enabled", True),
                    "product_list": group_cfg.get("product_list", ["logs"]),
                },
                "relationships": {
                    "configuration": {
                        "data": {
                            "type": "sensitive_data_scanner_configuration",
                            "id": config_id,
                        }
                    }
                },
            },
        }

        if dry_run:
            logger.info("[DRY RUN] Would create SDS group '%s'", group_name)
            result["created_ids"].append(f"dry-run-group:{group_name}")
            result["total_created"] += 1
            group_id = "dry-run-group-id"
        else:
            try:
                response = api_client.create_sds_group(payload)
                group_id = response.get("data", {}).get("id")
                if not group_id:
                    raise RuntimeError("No group ID in response")
                result["created_ids"].append(group_id)
                result["total_created"] += 1
                logger.info("Created SDS group '%s' (id=%s)", group_name, group_id)
            except RuntimeError as e:
                err = f"Failed to create SDS group '{group_name}': {e}"
                result["errors"].append(err)
                result["total_errors"] += 1
                logger.error(err)
                return None

        for rule_cfg in rules_cfg:
            self._deploy_rule(rule_cfg, group_id, base_tags, api_client, dry_run, result)

        return group_id

    def _deploy_rule(
        self,
        rule_cfg: Dict[str, Any],
        group_id: str,
        base_tags: List[str],
        api_client: DatadogAPIClient,
        dry_run: bool,
        result: Dict[str, Any],
    ) -> None:
        """Create one scanning rule inside a group."""
        rule_name = rule_cfg.get("name", "unnamed-sds-rule")

        replacement_type = rule_cfg.get("replacement_type", "replacement_string")
        text_replacement: Dict[str, Any] = {"type": replacement_type}
        if replacement_type in ("partial_replacement_from_beginning", "partial_replacement_from_end"):
            text_replacement["number_of_chars"] = rule_cfg.get("replacement_number_of_chars", 4)
        elif replacement_type == "replacement_string":
            text_replacement["replacement_string"] = rule_cfg.get("replacement_string", "[REDACTED]")

        attributes: Dict[str, Any] = {
            "name": rule_name,
            "description": rule_cfg.get("description", ""),
            "is_enabled": rule_cfg.get("is_enabled", True),
            "text_replacement": text_replacement,
            "tags": base_tags + rule_cfg.get("tags", []),
        }
        if "pattern" in rule_cfg:
            attributes["pattern"] = rule_cfg["pattern"]

        payload = {
            "data": {
                "type": "sensitive_data_scanner_rule",
                "attributes": attributes,
                "relationships": {
                    "group": {
                        "data": {
                            "type": "sensitive_data_scanner_group",
                            "id": group_id,
                        }
                    }
                },
            },
        }

        if dry_run:
            logger.info("[DRY RUN] Would create SDS rule '%s' in group %s", rule_name, group_id)
            result["created_ids"].append(f"dry-run-rule:{rule_name}")
            result["total_created"] += 1
            return

        try:
            response = api_client.create_sds_rule(payload)
            rule_id = response.get("data", {}).get("id")
            if rule_id:
                result["created_ids"].append(rule_id)
                result["total_created"] += 1
                logger.info("Created SDS rule '%s' (id=%s)", rule_name, rule_id)
            else:
                raise RuntimeError("No rule ID in response")
        except RuntimeError as e:
            err = f"Failed to create SDS rule '{rule_name}': {e}"
            result["errors"].append(err)
            result["total_errors"] += 1
            logger.error(err)

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def teardown(
        self,
        api_client: DatadogAPIClient,
        vertical_name: Optional[str],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Delete SDS rules and groups managed by the toolkit.

        Groups are identified by a marker in their description:
            ``[dd-demo-toolkit:vertical:<name>]``
        Rules are identified by the tag ``dd-demo-toolkit:true``.

        Rules are deleted first (SDS rejects deleting a group that still has
        rules).

        Args:
            api_client: Datadog API client.
            vertical_name: Vertical tag to filter on, or None to delete all
                toolkit-managed SDS resources across verticals.
            dry_run: Log what would be deleted without making API calls.

        Returns:
            Dict with deleted_ids, errors, total_deleted, total_errors.
        """
        result: Dict[str, Any] = {
            "deleted_ids": [],
            "errors": [],
            "total_deleted": 0,
            "total_errors": 0,
        }

        try:
            cfg_response = api_client.get_sds_config()
            if not isinstance(cfg_response, dict):
                raise RuntimeError(
                    f"SDS config returned unexpected type {type(cfg_response).__name__}. "
                    "SDS may not be enabled for this Datadog org."
                )
        except RuntimeError as e:
            logger.info("SDS teardown skipped — %s", e)
            return result

        included = cfg_response.get("included", []) or []

        toolkit_tag = "dd-demo-toolkit:true"
        vertical_tag = f"vertical:{vertical_name}" if vertical_name else None
        group_marker = f"[dd-demo-toolkit:vertical:{vertical_name}]" if vertical_name else "[dd-demo-toolkit:"

        def _rule_matches(resource: Dict[str, Any]) -> bool:
            if not isinstance(resource, dict):
                return False
            resource_tags = resource.get("attributes", {}).get("tags", []) or []
            if toolkit_tag not in resource_tags:
                return False
            if vertical_tag and vertical_tag not in resource_tags:
                return False
            return True

        def _group_matches(resource: Dict[str, Any]) -> bool:
            if not isinstance(resource, dict):
                return False
            desc = resource.get("attributes", {}).get("description", "") or ""
            return group_marker in desc

        rules_to_delete = [
            r for r in included
            if isinstance(r, dict)
            and r.get("type") == "sensitive_data_scanner_rule"
            and _rule_matches(r)
        ]
        groups_to_delete = [
            g for g in included
            if isinstance(g, dict)
            and g.get("type") == "sensitive_data_scanner_group"
            and _group_matches(g)
        ]

        scope = f"vertical '{vertical_name}'" if vertical_name else "all toolkit verticals"
        logger.info(
            "SDS teardown: %d rule(s) and %d group(s) to delete for %s",
            len(rules_to_delete), len(groups_to_delete), scope,
        )

        # Delete rules first
        for rule in rules_to_delete:
            rule_id = rule.get("id")
            rule_name = rule.get("attributes", {}).get("name", rule_id)
            if dry_run:
                logger.info("[DRY RUN] Would delete SDS rule %s (%s)", rule_id, rule_name)
                result["deleted_ids"].append(rule_id)
                result["total_deleted"] += 1
                continue
            try:
                api_client.delete_sds_rule(rule_id)
                result["deleted_ids"].append(rule_id)
                result["total_deleted"] += 1
                logger.info("Deleted SDS rule %s (%s)", rule_id, rule_name)
            except RuntimeError as e:
                err = f"Failed to delete SDS rule {rule_id}: {e}"
                result["errors"].append(err)
                result["total_errors"] += 1
                logger.error(err)

        # Delete groups after all rules
        for group in groups_to_delete:
            group_id = group.get("id")
            group_name = group.get("attributes", {}).get("name", group_id)
            if dry_run:
                logger.info("[DRY RUN] Would delete SDS group %s (%s)", group_id, group_name)
                result["deleted_ids"].append(group_id)
                result["total_deleted"] += 1
                continue
            try:
                api_client.delete_sds_group(group_id)
                result["deleted_ids"].append(group_id)
                result["total_deleted"] += 1
                logger.info("Deleted SDS group %s (%s)", group_id, group_name)
            except RuntimeError as e:
                err = f"Failed to delete SDS group {group_id}: {e}"
                result["errors"].append(err)
                result["total_errors"] += 1
                logger.error(err)

        logger.info(
            "SDS teardown complete: %d deleted, %d errors",
            result["total_deleted"], result["total_errors"],
        )
        return result

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_deployed(
        self,
        api_client: DatadogAPIClient,
        vertical_name: str,
    ) -> Dict[str, Any]:
        """
        List SDS groups and rules deployed for a vertical.

        Returns:
            Dict with sds_groups (list), sds_rules (list), total, error.
        """
        result: Dict[str, Any] = {
            "sds_groups": [],
            "sds_rules": [],
            "total": 0,
            "error": None,
        }

        try:
            cfg_response = api_client.get_sds_config()
            if not isinstance(cfg_response, dict):
                raise RuntimeError(
                    f"SDS config returned unexpected type {type(cfg_response).__name__}"
                )
        except RuntimeError as e:
            result["error"] = f"Failed to fetch SDS config: {e}"
            logger.warning(result["error"])
            return result

        vertical_tag = f"vertical:{vertical_name}"
        toolkit_tag = "dd-demo-toolkit:true"
        group_marker = f"[dd-demo-toolkit:vertical:{vertical_name}]"

        for item in cfg_response.get("included", []) or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "sensitive_data_scanner_group":
                desc = item.get("attributes", {}).get("description", "") or ""
                if group_marker in desc:
                    result["sds_groups"].append(item)
            elif item.get("type") == "sensitive_data_scanner_rule":
                tags = item.get("attributes", {}).get("tags", []) or []
                if vertical_tag in tags and toolkit_tag in tags:
                    result["sds_rules"].append(item)

        result["total"] = len(result["sds_groups"]) + len(result["sds_rules"])
        logger.info(
            "Found %d SDS group(s) and %d rule(s) for vertical '%s'",
            len(result["sds_groups"]), len(result["sds_rules"]), vertical_name,
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_tags(vertical_name: str, additional_tags: Optional[Dict[str, str]]) -> List[str]:
        tags = [f"vertical:{vertical_name}", "dd-demo-toolkit:true"]
        if additional_tags:
            for k, v in additional_tags.items():
                tags.append(f"{k}:{v}")
        return tags
