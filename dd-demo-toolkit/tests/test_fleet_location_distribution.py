"""
Regression tests for even fleet distribution across location dimensions.

Bug (2026-07-09): the engine placed each device type's instances by walking
*consecutive* combos of the cartesian product of all location dimensions
(`location_counter % len(locations)`). Once a high-cardinality dimension was
added (the Ascension overlay's `campus`, making each floor/wing cell 120
combos wide), a modest-count device type never advanced past the first cell —
so the whole fleet showed up under a single floor/wing (e.g. "1, east") and
most campuses/floors/wings were empty on the dashboard.

Fix: place instances by INDEPENDENT per-dimension round-robin (each dimension
cycles by i % len(values)), giving even marginal coverage across every
dimension. Environment topology is still respected: (region, environment) is
assigned as an allowed pair, never round-robined into a disallowed combo.
"""

from dd_demo_toolkit.simulator.engine import SimulatorEngine


def _base_config(dimensions, devices, env_prefix="test", topology=None):
    cfg = {
        "vertical": {"name": "t", "display_name": "Test", "env_prefix": env_prefix, "emit_interval_sec": 15},
        "locations": {"dimensions": dimensions},
        "device_categories": {"cat": {"devices": devices}},
    }
    if topology:
        cfg["environment_topology"] = topology
    return cfg


def _values_seen(fleet, dim):
    return {d.location.get(dim) for d in fleet if dim in d.location}


def test_high_cardinality_dimension_does_not_pile_into_one_cell():
    """The exact Ascension shape: floor×wing×department×campus, modest count."""
    dims = [
        {"name": "floor", "values": ["1", "2", "3", "4", "5"]},
        {"name": "wing", "values": ["East", "West", "North", "South"]},
        {"name": "department", "values": ["ED", "ICU", "OR", "MedSurg", "Radiology", "Pharmacy", "Lab", "Admin"]},
        {"name": "campus", "values": [f"campus-{i}" for i in range(15)]},
    ]
    devices = [{"type": "gateway", "count": 45, "metrics": [
        {"name": "test.device.online", "type": "gauge", "range": [0, 1]}]}]
    fleet = SimulatorEngine(_base_config(dims, devices)).fleet

    assert len(fleet) == 45
    # Every value of every dimension is represented (45 >= each cardinality).
    assert _values_seen(fleet, "floor") == set(dims[0]["values"])
    assert _values_seen(fleet, "wing") == set(dims[1]["values"])
    assert _values_seen(fleet, "campus") == set(dims[3]["values"])  # all 15
    # Not piled into a single (floor, wing) cell.
    cells = {(d.location["floor"], d.location["wing"]) for d in fleet}
    assert len(cells) >= 15


def test_department_pool_overrides_department_dimension():
    dims = [
        {"name": "floor", "values": ["1", "2", "3"]},
        {"name": "campus", "values": ["a", "b", "c", "d", "e"]},
    ]
    devices = [{"type": "sensor", "count": 20, "metrics": [
        {"name": "test.device.online", "type": "gauge", "range": [0, 1]}]}]
    cfg = _base_config(dims, devices)
    cfg["device_categories"]["cat"]["department_pool"] = ["MedSurg"]
    fleet = SimulatorEngine(cfg).fleet
    assert {d.location["department"] for d in fleet} == {"MedSurg"}
    assert _values_seen(fleet, "campus") == {"a", "b", "c", "d", "e"}


def test_environment_topology_respected_no_disallowed_pairs():
    """(region, environment) must only ever be an allowed pair."""
    dims = [
        {"name": "region", "values": ["us-east", "us-west", "eu"]},
        {"name": "environment", "values": ["production", "staging", "dr-site"]},
        {"name": "business_unit", "values": ["bu1", "bu2", "bu3", "bu4"]},
    ]
    # dr-site only in us-east; staging only in us-east/us-west; prod everywhere.
    topology = {
        "production": ["us-east", "us-west", "eu"],
        "staging": ["us-east", "us-west"],
        "dr-site": ["us-east"],
    }
    allowed = {(r, e) for e, rs in topology.items() for r in rs}
    devices = [{"type": "node", "count": 40, "metrics": [
        {"name": "test.device.online", "type": "gauge", "range": [0, 1]}]}]
    fleet = SimulatorEngine(_base_config(dims, devices, topology=topology)).fleet

    emitted = {(d.location.get("region"), d.location.get("environment")) for d in fleet}
    assert emitted <= allowed, f"disallowed pairs emitted: {emitted - allowed}"
    # count (40) >= number of allowed pairs (6) → every allowed pair covered,
    # and business_unit spreads across all BUs (not piled into one).
    assert emitted == allowed
    assert _values_seen(fleet, "business_unit") == {"bu1", "bu2", "bu3", "bu4"}
