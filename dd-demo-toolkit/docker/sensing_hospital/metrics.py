"""Shared DogStatsD client for the Sensing Hospital services.

Sends custom application metrics to the real Datadog Agent over UDP 8125
(the agent runs with DD_DOGSTATSD_NON_LOCAL_TRAFFIC=true). This is what makes
"metrics emitted by the app" literally true — distinct from the Agent's
infra/container metrics and the APM-derived trace metrics.

Tags default to the service's deployment context (DD_SERVICE / deployment tag)
so the custom metrics line up with the traces and logs in Datadog.
"""
from __future__ import annotations

import os

from datadog import DogStatsd

_HOST = os.getenv("DD_AGENT_HOST", "localhost")
_SERVICE = os.getenv("DD_SERVICE", "sensing-hospital")
_DEPLOYMENT = os.getenv("DD_DEPLOYMENT", "unknown")

statsd = DogStatsd(
    host=_HOST,
    port=8125,
    constant_tags=[
        f"service:{_SERVICE}",
        f"deployment:{_DEPLOYMENT}",
        "dd-demo-toolkit:true",
        "vertical:healthcare",
        "incident_domain:care-experience",
        "env:demo",
    ],
)
