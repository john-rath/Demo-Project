"""
RUM (Real User Monitoring) metrics simulator for the smart-hospitality demo.

Emits custom metrics through the OTel collector pipeline that represent
real-user-monitoring data for the Guest Loyalty Program web and mobile app.
Metrics appear in Datadog Metrics Explorer under the ``hospitality.rum.*``
namespace and power dashboards, monitors, and the RCA notebook
correlation between frontend errors and infrastructure incidents.

User flows simulated:
  - Core booking:  Homepage → Search → Property Details → Room Select → Checkout → Confirmation
  - Loyalty:       Login → Loyalty Dashboard → Points History → Redemption Calc → AI Stay Planner → Booking
  - In-stay:       My Trips → Digital Key → In-Stay Services → Post-Stay Survey

During active WiFi/IoT incidents (published by the Meraki cascade plugin),
the simulator inflates error rates and latencies on affected views to
create a visible correlation in dashboards.
"""

from __future__ import annotations

import logging
import random
import uuid
from typing import Any, Dict, List, Optional, Tuple

from opentelemetry.metrics import Meter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RUM_SERVICE = "reservations-portal"
RUM_ENV = "demo"
RUM_VERSION = "4.12.0"

# Loyalty tiers with realistic distribution
LOYALTY_TIERS = [
    {"tier": "Member", "weight": 0.40, "avg_points": 15_000, "avg_revenue": 189},
    {"tier": "Silver", "weight": 0.25, "avg_points": 75_000, "avg_revenue": 245},
    {"tier": "Gold", "weight": 0.20, "avg_points": 210_000, "avg_revenue": 320},
    {"tier": "Diamond", "weight": 0.15, "avg_points": 580_000, "avg_revenue": 495},
]

PROPERTY_TYPES = [
    "Luxury Collection", "Premium Resort", "Full Service",
    "Upscale Select", "Select Service", "Extended Stay",
]
REGIONS = ["Americas", "EMEA", "APAC"]

# Browser / device distributions
BROWSERS = [
    {"name": "Chrome", "version": "124.0", "weight": 0.55},
    {"name": "Safari", "version": "17.4", "weight": 0.25},
    {"name": "Firefox", "version": "125.0", "weight": 0.10},
    {"name": "Edge", "version": "124.0", "weight": 0.10},
]
DEVICE_TYPES = [
    {"type": "mobile", "weight": 0.58},
    {"type": "desktop", "weight": 0.35},
    {"type": "tablet", "weight": 0.07},
]
OS_LIST = [
    {"name": "iOS", "version": "17.4", "weight": 0.35},
    {"name": "Android", "version": "14", "weight": 0.25},
    {"name": "Windows", "version": "11", "weight": 0.22},
    {"name": "macOS", "version": "14.4", "weight": 0.15},
    {"name": "Linux", "version": "", "weight": 0.03},
]

# ---------------------------------------------------------------------------
# View definitions — each flow is an ordered list of views
# ---------------------------------------------------------------------------

BOOKING_FLOW: List[Dict[str, Any]] = [
    {
        "name": "/",
        "title": "Guest Loyalty Program - Home",
        "lcp": (1200, 2800), "fid": (20, 90), "cls": (0.01, 0.12), "ttfb": (80, 350),
        "load_time": (1400, 3200),
        "resources": 24, "actions": ["search_click", "promo_banner_click"],
        "error_rate": 0.01,
    },
    {
        "name": "/search",
        "title": "Search Results - Hospitality",
        "lcp": (1800, 3500), "fid": (30, 120), "cls": (0.02, 0.18), "ttfb": (100, 400),
        "load_time": (2000, 4000),
        "resources": 38, "actions": ["filter_click", "sort_click", "property_card_click", "map_toggle"],
        "error_rate": 0.02,
    },
    {
        "name": "/hotel/property-details",
        "title": "Property Details - Hospitality",
        "lcp": (1500, 3200), "fid": (25, 100), "cls": (0.01, 0.10), "ttfb": (90, 320),
        "load_time": (1600, 3600),
        "resources": 42, "actions": ["gallery_swipe", "room_tab_click", "review_expand", "select_room_click"],
        "error_rate": 0.015,
    },
    {
        "name": "/booking/room-selection",
        "title": "Select Your Room - Hospitality",
        "lcp": (1000, 2200), "fid": (15, 70), "cls": (0.005, 0.06), "ttfb": (70, 280),
        "load_time": (1200, 2600),
        "resources": 18, "actions": ["room_upgrade_click", "points_toggle", "add_to_cart"],
        "error_rate": 0.01,
    },
    {
        "name": "/booking/checkout",
        "title": "Checkout - Guest Loyalty Program",
        "lcp": (900, 1800), "fid": (10, 50), "cls": (0.002, 0.04), "ttfb": (60, 250),
        "load_time": (1000, 2000),
        "resources": 14, "actions": ["payment_submit", "apply_promo_code", "points_slider_adjust"],
        "error_rate": 0.03,
    },
    {
        "name": "/booking/confirmation",
        "title": "Booking Confirmed - Hospitality",
        "lcp": (800, 1500), "fid": (8, 40), "cls": (0.001, 0.03), "ttfb": (50, 200),
        "load_time": (900, 1700),
        "resources": 10, "actions": ["add_to_calendar", "share_itinerary", "upsell_accept"],
        "error_rate": 0.005,
    },
]

LOYALTY_FLOW: List[Dict[str, Any]] = [
    {
        "name": "/login",
        "title": "Sign In - Guest Loyalty Program",
        "lcp": (600, 1200), "fid": (10, 40), "cls": (0.001, 0.02), "ttfb": (40, 180),
        "load_time": (700, 1400),
        "resources": 8, "actions": ["login_submit", "forgot_password", "biometric_auth"],
        "error_rate": 0.04,
    },
    {
        "name": "/loyalty/dashboard",
        "title": "Loyalty Dashboard - Hospitality",
        "lcp": (1400, 2600), "fid": (20, 80), "cls": (0.01, 0.08), "ttfb": (90, 300),
        "load_time": (1600, 3000),
        "resources": 32, "actions": ["view_activity", "check_offers", "tier_progress_expand"],
        "error_rate": 0.01,
    },
    {
        "name": "/loyalty/points-history",
        "title": "Points Activity - Guest Loyalty Program",
        "lcp": (1100, 2200), "fid": (15, 60), "cls": (0.005, 0.06), "ttfb": (70, 260),
        "load_time": (1300, 2500),
        "resources": 20, "actions": ["date_filter", "download_statement", "transaction_expand"],
        "error_rate": 0.01,
    },
    {
        "name": "/loyalty/redemption-calculator",
        "title": "Points Value Calculator - Hospitality",
        "lcp": (1000, 2000), "fid": (12, 55), "cls": (0.005, 0.05), "ttfb": (60, 240),
        "load_time": (1100, 2300),
        "resources": 16, "actions": ["destination_input", "dates_select", "calculate_click", "compare_toggle"],
        "error_rate": 0.015,
    },
    {
        "name": "/ai-stay-planner",
        "title": "AI Stay Planner - Guest Loyalty Program",
        "lcp": (1600, 3000), "fid": (25, 100), "cls": (0.01, 0.10), "ttfb": (100, 350),
        "load_time": (1800, 3500),
        "resources": 28, "actions": ["planner_submit", "suggestion_click", "refine_preferences"],
        "error_rate": 0.025,
    },
    {
        "name": "/booking/checkout",
        "title": "Checkout - Guest Loyalty Program",
        "lcp": (900, 1800), "fid": (10, 50), "cls": (0.002, 0.04), "ttfb": (60, 250),
        "load_time": (1000, 2000),
        "resources": 14, "actions": ["payment_submit", "apply_promo_code", "points_slider_adjust"],
        "error_rate": 0.03,
    },
]

INSTAY_FLOW: List[Dict[str, Any]] = [
    {
        "name": "/my-trips",
        "title": "My Trips - Guest Loyalty Program",
        "lcp": (1200, 2400), "fid": (15, 70), "cls": (0.008, 0.07), "ttfb": (80, 300),
        "load_time": (1400, 2800),
        "resources": 22, "actions": ["trip_expand", "modify_reservation", "add_requests"],
        "error_rate": 0.01,
    },
    {
        "name": "/digital-key",
        "title": "Digital Key - Guest Loyalty Program",
        "lcp": (800, 1600), "fid": (10, 45), "cls": (0.003, 0.04), "ttfb": (50, 200),
        "load_time": (900, 1800),
        "resources": 12, "actions": ["activate_key", "share_key", "door_unlock"],
        "error_rate": 0.02,
    },
    {
        "name": "/in-stay/services",
        "title": "In-Stay Services - Hospitality",
        "lcp": (1000, 2000), "fid": (12, 55), "cls": (0.005, 0.06), "ttfb": (60, 240),
        "load_time": (1100, 2300),
        "resources": 26, "actions": ["room_service_order", "spa_booking", "late_checkout_request", "concierge_chat"],
        "error_rate": 0.015,
    },
    {
        "name": "/post-stay/survey",
        "title": "How Was Your Stay? - Hospitality",
        "lcp": (700, 1400), "fid": (8, 35), "cls": (0.002, 0.03), "ttfb": (40, 180),
        "load_time": (800, 1600),
        "resources": 10, "actions": ["star_rating", "comment_submit", "photo_upload"],
        "error_rate": 0.008,
    },
]

ALL_FLOWS = [
    {"name": "booking", "views": BOOKING_FLOW, "weight": 0.45},
    {"name": "loyalty", "views": LOYALTY_FLOW, "weight": 0.35},
    {"name": "instay", "views": INSTAY_FLOW, "weight": 0.20},
]

# ---------------------------------------------------------------------------
# RUM error messages (emitted as metric tags for context)
# ---------------------------------------------------------------------------

RUM_ERRORS = [
    "TypeError: Cannot read properties of undefined (reading 'points_balance')",
    "ChunkLoadError: Loading chunk 14 failed (timeout)",
    "NetworkError: Failed to fetch /api/v3/availability — 504 Gateway Timeout",
    "SecurityError: Blocked a frame with origin 'https://payments.hospitality.demo'",
    "SyntaxError: Unexpected token '<' in JSON at position 0",
    "AbortError: user navigated away during AI planner response",
    "RangeError: Maximum call stack size exceeded in loyalty tier calculation",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _weighted_choice(items: list) -> dict:
    """Pick from a list using 'weight' keys."""
    weights = [item["weight"] for item in items]
    return random.choices(items, weights=weights, k=1)[0]


def _rand_range(r: Tuple[float, float]) -> float:
    """Random float in a (min, max) tuple range."""
    return random.uniform(r[0], r[1])


def _generate_user() -> Dict[str, Any]:
    """Generate a synthetic Guest Loyalty Program user profile."""
    tier_info = _weighted_choice(LOYALTY_TIERS)
    user_id = str(uuid.uuid4())[:8]
    first_names = ["James", "Sarah", "Michael", "Emma", "David", "Olivia", "Robert", "Sophia",
                   "William", "Isabella", "Daniel", "Mia", "Thomas", "Charlotte", "Christopher",
                   "Priya", "Wei", "Yuki", "Carlos", "Fatima", "Ahmed", "Kenji", "Maria", "Aisha"]
    last_names = ["Chen", "Smith", "Patel", "Johnson", "Williams", "Brown", "Jones", "Garcia",
                  "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
                  "Nakamura", "Kim", "Tanaka", "Singh", "Ali", "Schmidt", "Muller", "Dubois"]
    first = random.choice(first_names)
    last = random.choice(last_names)
    return {
        "id": f"HH-{user_id}",
        "name": f"{first} {last}",
        "email": f"{first.lower()}.{last.lower()}@example.com",
        "tier": tier_info["tier"],
        "points": int(tier_info["avg_points"] * random.uniform(0.4, 1.8)),
        "avg_revenue": tier_info["avg_revenue"],
        "property_pref": random.choice(PROPERTY_TYPES),
        "region": random.choice(REGIONS),
    }


# ---------------------------------------------------------------------------
# Incident degradation — inflates error rates and latencies during incidents
# ---------------------------------------------------------------------------

# Views most affected by WiFi/IoT incidents (Digital Key, in-stay services)
_INCIDENT_SENSITIVE_VIEWS = {
    "/digital-key": 5.0,        # 5x error rate multiplier
    "/in-stay/services": 4.0,
    "/my-trips": 2.5,
    "/ai-stay-planner": 3.0,    # AI planner calls backend which is also affected
    "/booking/checkout": 2.0,
    "/loyalty/dashboard": 1.5,
}


def _apply_incident_degradation(views: List[Dict[str, Any]], severity: float) -> List[Dict[str, Any]]:
    """
    Return a copy of the view list with degraded performance during an active incident.

    severity: 0.0 (no impact) to 1.0 (full outage)
    - Error rates increase proportionally to view sensitivity
    - Load times / LCP / TTFB inflate
    - CLS worsens as error UI elements appear
    """
    degraded = []
    for view in views:
        v = {**view}  # shallow copy

        multiplier = _INCIDENT_SENSITIVE_VIEWS.get(view["name"], 1.0)
        impact = severity * multiplier

        # Inflate error rate — sensitive views can hit 30-60% error rates during outage
        base_err = view["error_rate"]
        v["error_rate"] = min(0.6, base_err + (impact * 0.12))

        # Inflate load times (API timeouts cascade to frontend)
        load_lo, load_hi = view["load_time"]
        v["load_time"] = (load_lo * (1 + impact * 0.8), load_hi * (1 + impact * 1.5))

        # Inflate LCP (DOM re-renders on error states)
        lcp_lo, lcp_hi = view["lcp"]
        v["lcp"] = (lcp_lo * (1 + impact * 0.5), lcp_hi * (1 + impact * 1.2))

        # Inflate TTFB (backend under load)
        ttfb_lo, ttfb_hi = view["ttfb"]
        v["ttfb"] = (ttfb_lo * (1 + impact * 0.6), ttfb_hi * (1 + impact * 1.0))

        # CLS worsens as error UI elements appear
        cls_lo, cls_hi = view["cls"]
        v["cls"] = (cls_lo, min(0.5, cls_hi * (1 + impact * 0.8)))

        degraded.append(v)
    return degraded


# ============================================================================
# OTel custom metrics emitter
# ============================================================================

class RUMMetricsEmitter:
    """
    Emits RUM-style custom metrics through the engine's shared OTel meter.

    Uses **gauges** for all instruments (matching the device-metric pattern that
    exports reliably through the Datadog OTel Collector pipeline).  OTel
    histograms and monotonic counters don't reliably reach Datadog via the
    collector's distribution/delta conversion — gauges bypass that entirely.

    For "count"-style metrics the emitter tracks running totals internally and
    calls ``gauge.set()`` with the cumulative value each tick.  Datadog sees a
    monotonically increasing gauge and dashboards can use ``sum:`` or
    ``.as_count()`` to derive rates.
    """

    def __init__(self, meter: Meter):
        # --- Web Vitals (gauge — latest observed value per tag set) -----------
        self._lcp = meter.create_gauge("hospitality.rum.lcp_ms", unit="ms", description="Largest Contentful Paint")
        self._fid = meter.create_gauge("hospitality.rum.fid_ms", unit="ms", description="First Input Delay")
        self._cls = meter.create_gauge("hospitality.rum.cls", description="Cumulative Layout Shift")
        self._ttfb = meter.create_gauge("hospitality.rum.ttfb_ms", unit="ms", description="Time to First Byte")
        self._page_load = meter.create_gauge("hospitality.rum.page_load_ms", unit="ms", description="Page load time")

        # --- Counts (gauge — cumulative running total) ------------------------
        self._sessions = meter.create_gauge("hospitality.rum.sessions_total", description="Total user sessions")
        self._page_views = meter.create_gauge("hospitality.rum.page_views_total", description="Total page views")
        self._errors = meter.create_gauge("hospitality.rum.errors_total", description="Total frontend errors")
        self._actions = meter.create_gauge("hospitality.rum.user_actions_total", description="Total user actions")

        self._loyalty_logins = meter.create_gauge("hospitality.rum.loyalty_logins_total", description="Loyalty authenticated sessions")
        self._points_viewed = meter.create_gauge("hospitality.rum.points_balance_viewed", description="Points balance at session start")
        self._redemption_attempts = meter.create_gauge("hospitality.rum.redemption_attempts_total", description="Points redemption attempts")
        self._redemption_conversions = meter.create_gauge("hospitality.rum.redemption_conversions_total", description="Completed points redemptions")

        self._booking_revenue = meter.create_gauge("hospitality.rum.booking_revenue_usd", unit="USD", description="Booking revenue per session")
        self._upsell_shown = meter.create_gauge("hospitality.rum.upsell_impressions_total", description="Upsell offers shown")
        self._upsell_accepted = meter.create_gauge("hospitality.rum.upsell_accepted_total", description="Upsell offers accepted")
        self._ancillary_revenue = meter.create_gauge("hospitality.rum.ancillary_revenue_usd", unit="USD", description="Ancillary (spa, F&B, services) revenue")

        self._funnel_step = meter.create_gauge("hospitality.rum.funnel_step_total", description="Funnel progression events")
        self._funnel_drop = meter.create_gauge("hospitality.rum.funnel_dropoff_total", description="Funnel drop-off events")

        # Internal running totals keyed by frozen-tag-set
        self._counts: Dict[str, Dict[tuple, int]] = {}

        logger.info("RUM OTel metrics emitter initialised (all-gauge mode, shared meter)")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _inc(self, metric_name: str, gauge, amount: int, tags: Dict[str, str]) -> None:
        """Increment a running total and set the gauge to the new value."""
        bucket = self._counts.setdefault(metric_name, {})
        key = tuple(sorted(tags.items()))
        bucket[key] = bucket.get(key, 0) + amount
        gauge.set(bucket[key], tags)

    # ------------------------------------------------------------------

    def record_session(
        self,
        user: Dict,
        flow: Dict,
        views: List[Dict[str, Any]],
        views_completed: int,
        total_views: int,
        is_degraded: bool = False,
    ) -> None:
        """Record all OTel metrics for a completed (or partially completed) session."""
        tags = {
            "loyalty_tier": user["tier"],
            "property_type": user["property_pref"],
            "region": user["region"],
            "flow": flow["name"],
            "device_type": _weighted_choice(DEVICE_TYPES)["type"],
            "browser": _weighted_choice(BROWSERS)["name"],
        }
        if is_degraded:
            tags["incident_affected"] = "true"

        self._inc("sessions", self._sessions, 1, tags)
        self._inc("loyalty_logins", self._loyalty_logins, 1, tags)
        self._points_viewed.set(user["points"], tags)

        # Walk through viewed pages
        for i, view in enumerate(views[:views_completed]):
            view_tags = {**tags, "view_path": view["name"]}
            self._inc("page_views", self._page_views, 1, view_tags)

            # Web vitals — set to latest sampled value (gauge)
            self._lcp.set(_rand_range(view["lcp"]), view_tags)
            self._fid.set(_rand_range(view["fid"]), view_tags)
            self._cls.set(round(_rand_range(view["cls"]), 4), view_tags)
            self._ttfb.set(_rand_range(view["ttfb"]), view_tags)
            self._page_load.set(_rand_range(view["load_time"]), view_tags)

            # Actions
            num_actions = random.randint(1, len(view["actions"]))
            for action in random.sample(view["actions"], num_actions):
                self._inc("actions", self._actions, 1, {**view_tags, "action": action})

            # Errors
            if random.random() < view["error_rate"]:
                error_msg = random.choice(RUM_ERRORS)
                error_type = error_msg.split(":")[0]
                self._inc("errors", self._errors, 1, {**view_tags, "error_type": error_type})

            # Funnel tracking
            self._inc("funnel_step", self._funnel_step, 1, {**view_tags, "step": str(i + 1)})

        # Drop-off if didn't complete flow
        if views_completed < total_views:
            drop_view = views[views_completed - 1]
            self._inc("funnel_drop", self._funnel_drop, 1, {**tags, "view_path": drop_view["name"], "step": str(views_completed)})

        # Revenue & loyalty metrics for completed booking flows
        if views_completed == total_views and flow["name"] in ("booking", "loyalty"):
            base_rev = user["avg_revenue"] * random.uniform(0.7, 1.5) * random.randint(1, 4)
            self._booking_revenue.set(round(base_rev, 2), tags)

            # Points redemption
            if flow["name"] == "loyalty" or random.random() < 0.3:
                self._inc("redemption_attempts", self._redemption_attempts, 1, tags)
                if random.random() < 0.65:
                    self._inc("redemption_conversions", self._redemption_conversions, 1, tags)

            # Upsell
            self._inc("upsell_shown", self._upsell_shown, 1, tags)
            if random.random() < 0.22:
                self._inc("upsell_accepted", self._upsell_accepted, 1, tags)
                self._ancillary_revenue.set(
                    round(random.uniform(25, 180), 2), tags
                )

    def shutdown(self) -> None:
        # Meter lifecycle managed by the engine's MeterProvider — nothing to do here
        pass


# ============================================================================
# Main RUM Submitter — called from the engine tick loop
# ============================================================================

class RUMSubmitter:
    """
    Top-level RUM simulator.

    Each tick (at a configurable interval) generates one or more user sessions:
      - Picks a random user (loyalty tier, region, property preference)
      - Picks a user flow (booking / loyalty / in-stay)
      - Simulates partial or full funnel completion
      - Emits OTel custom metrics (web vitals, engagement, revenue, funnel)
      - During active WiFi/IoT incidents, inflates error rates and latencies
        for affected properties to create visible dashboard correlation
    """

    def __init__(self, meter: Meter):
        self._otel = RUMMetricsEmitter(meter=meter)
        logger.info("RUM Submitter initialised (OTel custom metrics, shared meter)")

    def tick(self, incident_state: Optional[Dict[str, Any]] = None) -> None:
        """Called each simulator tick. Generates user sessions every tick for dense demo data."""
        # Generate 5-12 concurrent sessions per tick for realistic dashboard density
        num_sessions = random.choices([5, 7, 9, 12], weights=[0.3, 0.35, 0.25, 0.1], k=1)[0]
        for _ in range(num_sessions):
            self._generate_session(incident_state=incident_state)

    def _generate_session(self, incident_state: Optional[Dict[str, Any]] = None) -> None:
        """Generate one user session with degraded UX during active incidents."""
        user = _generate_user()
        flow = _weighted_choice(ALL_FLOWS)
        total_views = len(flow["views"])

        # Check for active WiFi incident — degrades frontend experience
        wifi_incident = (incident_state or {}).get("wifi_client_overload")
        incident_severity = wifi_incident["severity"] if wifi_incident else 0.0
        incident_property = wifi_incident["property"] if wifi_incident else None
        incident_region = wifi_incident["region"] if wifi_incident else None

        # If user is at the affected property during an active incident, degrade UX
        user_affected = (
            incident_severity > 0
            and user["property_pref"] == incident_property
            and user["region"] == incident_region
        )

        # Apply degradation to view definitions (inflated errors, latencies)
        if user_affected and incident_severity >= 0.5:
            effective_views = _apply_incident_degradation(flow["views"], incident_severity)
        else:
            effective_views = flow["views"]

        # Simulate funnel completion — higher tiers complete more often
        tier_completion_boost = {
            "Member": 0.0, "Silver": 0.05, "Gold": 0.10, "Diamond": 0.15,
        }
        base_completion = 0.55
        if user_affected:
            base_completion -= incident_severity * 0.3  # up to 30% drop in completion
        completion_prob = base_completion + tier_completion_boost.get(user["tier"], 0)

        if random.random() < completion_prob:
            views_completed = total_views
        else:
            views_completed = random.randint(1, max(1, total_views - 1))

        # Emit OTel metrics using the (potentially degraded) view definitions
        try:
            self._otel.record_session(
                user=user,
                flow=flow,
                views=effective_views,
                views_completed=views_completed,
                total_views=total_views,
                is_degraded=user_affected and incident_severity >= 0.5,
            )
        except Exception as exc:
            logger.debug(f"RUM OTel metrics error: {exc}")

        incident_tag = " [DEGRADED]" if user_affected and incident_severity >= 0.5 else ""
        logger.debug(
            f"RUM session: {user['tier']} {user['name']} — "
            f"{flow['name']} flow, {views_completed}/{total_views} views{incident_tag}"
        )

    def shutdown(self) -> None:
        """Flush OTel metrics and clean up."""
        try:
            self._otel.shutdown()
        except Exception:
            pass
        logger.info("RUM Submitter shutdown")
