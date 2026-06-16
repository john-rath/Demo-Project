"""
LLM Observability trace generator using OpenTelemetry GenAI semantic conventions.

Generates realistic AI agent traces that flow through the OTel collector
and are mapped by Datadog's OTel exporter to LLM Observability.

OTel GenAI semantic conventions (v1.37+):
  - gen_ai.system, gen_ai.request.model, gen_ai.operation.name
  - gen_ai.usage.input_tokens, gen_ai.usage.output_tokens
  - gen_ai.input.messages / gen_ai.output.messages (JSON-serialized)
  - Span kind = CLIENT for LLM calls

Trace structure per scenario:
  Agent (INTERNAL) → Intent LLM (CLIENT) → Tool spans (INTERNAL)
                   → Embedding (CLIENT) → Retrieval (INTERNAL)
                   → Recommendation LLM (CLIENT)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import threading
import time
import urllib.request
import urllib.error
import uuid
from typing import Any, Dict, List, Optional

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import SpanKind, StatusCode, Status
from opentelemetry.semconv.resource import ResourceAttributes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conversation library — each scenario produces a full trace
# ---------------------------------------------------------------------------

SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "Family Vacation NYC",
        "user_input": "I'm looking for a 3-night family vacation in New York City. We have 2 kids under 10 and are Guest Loyalty Program Diamond members. Need a pool if possible.",
        "intent": "family_vacation",
        "search_query": "New York City, 2 adults + 2 children, 3 nights, pool:preferred, loyalty_tier:diamond",
        "search_results": "Premium Resort New York Downtown (available, $389/night Diamond rate), Hospitality Midtown (available, $299/night), New York Hospitality Midtown (waitlist)",
        "loyalty_profile": '{"tier": "Diamond", "points": 892000, "lifetime_nights": 347, "preferences": ["high_floor", "pool_access", "extra_pillows"], "family_members": 4}',
        "rag_docs": "Premium Resort New York Downtown: luxury all-suite hotel, 463 suites, rooftop pool, Danny Meyer restaurant, complimentary Diamond breakfast buffet, 3 blocks from Battery Park playground and Statue of Liberty ferry, kids activity program on weekends",
        "embedding_input": "family travel, 2 children under 10, pool required, Diamond member, New York City, 3 nights",
        "recommendation": "Based on your Diamond status and family needs, I recommend the **Premium Resort New York Downtown**.\n\n**Why this property:**\n- Complimentary breakfast for Diamond members (saves ~$200/day for a family of 4)\n- Rooftop pool — one of the few in Manhattan\n- All-suite layout gives kids their own space\n- 3 blocks from Battery Park playground and Statue of Liberty ferry\n- Weekend kids activity program\n\n**Rate:** $389/night with Diamond discount (vs. $520 rack rate)\n**Points alternative:** 95,000 pts/night — with 892K points, you could do 3 nights for 285K points.\n\nShall I check specific dates?",
        "model": "gpt-4-turbo",
        "provider": "azure_openai",
        "property_type": "Premium Resort",
        "region": "Americas",
    },
    {
        "name": "Business Trip Chicago",
        "user_input": "Need a hotel near McCormick Place in Chicago for 2 nights next week. I have late-night video calls to Asia Pacific, so a quiet room is essential. Gold member.",
        "intent": "business_travel",
        "search_query": "Chicago, McCormick Place area, 1 adult, 2 nights, quiet_room:required, loyalty_tier:gold",
        "search_results": "Hospitality Chicago (connected via skybridge, $259/night Gold rate), Select Service Inn McCormick (available, $179/night), Full Service Suites Chicago ($219/night)",
        "loyalty_profile": '{"tier": "Gold", "points": 215000, "lifetime_nights": 89, "preferences": ["high_floor", "quiet_room", "late_checkout"], "company": "Accenture"}',
        "rag_docs": "Hospitality Chicago: 1,544 rooms, direct skybridge to McCormick Place convention center, executive floor rooms 20-25 feature enhanced soundproofing, Executive Lounge open until midnight with complimentary refreshments and private meeting pods, 24/7 business center",
        "embedding_input": "business travel, McCormick Place, quiet room, late night calls, Asia Pacific timezone, Gold member",
        "recommendation": "I recommend the **Hospitality Chicago** with an Executive Floor room.\n\n**Why this property:**\n- Direct skybridge connection to McCormick Place — no taxi/rideshare needed\n- Executive floor (floors 20-25) with enhanced soundproofing for your late-night calls\n- Executive Lounge open until midnight — perfect for Asia call prep with complimentary refreshments\n- Gold status gets you complimentary room upgrade (subject to availability)\n\n**Rate:** $259/night Gold rate\n**Room request:** High floor, away from elevators, executive level\n**Late checkout:** Requested through 2pm for your Gold benefit\n\nWould you like me to book this?",
        "model": "gpt-4-turbo",
        "provider": "azure_openai",
        "property_type": "Full Service",
        "region": "Americas",
    },
    {
        "name": "Loyalty Points Redemption",
        "user_input": "I have 450,000 Loyalty points. What's the best value redemption for a romantic getaway? Flexible on dates and destination.",
        "intent": "points_redemption",
        "search_query": "Points redemption, 2 adults, romantic getaway, flexible dates, budget:450000 points, optimize:cpp_value",
        "search_results": "Luxury Collection Maldives (95K/night, 5th night free promo), Premium Resort Bora Bora (120K/night), Luxury Collection Rome (80K/night), Premium Resort Maldives (85K/night, 5th night free promo)",
        "loyalty_profile": '{"tier": "Diamond", "points": 450000, "lifetime_nights": 523, "preferences": ["ocean_view", "adults_only", "spa"], "status_match": "Marriott_Titanium_crossover"}',
        "rag_docs": "Luxury Collection Maldives Ithaafushi: overwater villas from $2,800/night cash rate, 5th night free for all award stays, private island, 3 infinity pools, spa over lagoon, 11 restaurants, complimentary speedboat transfer from Male. Best cpp (cents per point) redemption in portfolio at current rates: 3.68 cpp for Diamond members with 5th night free.",
        "embedding_input": "romantic getaway, points redemption, flexible dates, ocean view, luxury, value optimization",
        "recommendation": "For maximum value, I recommend the **Luxury Collection Maldives Ithaafushi**.\n\n**The math:**\n- 95,000 pts/night x 4 nights = 380,000 points\n- 5th night free promo = **5 nights for 380,000 points**\n- Cash rate: $2,800/night x 5 = $14,000 value\n- **Value: 3.68 cents per point** (vs. typical 0.5-0.6 cpp)\n\n**The experience:**\n- Overwater villa with private pool and glass floor\n- Private island — the definition of romantic\n- 11 dining venues, spa suspended over the lagoon\n- Complimentary speedboat from Male airport\n\n**Remaining points:** 70,000 (enough for a domestic weekend later)\n\nBest availability: May/June or September/October for weather + pricing. Want me to check specific dates?",
        "model": "gpt-4-turbo",
        "provider": "azure_openai",
        "property_type": "Luxury Collection",
        "region": "APAC",
    },
    {
        "name": "Group Event Planning",
        "user_input": "Planning a 50-person corporate offsite in Austin, TX for 3 days. Need meeting space for 50, breakout rooms, and a group rate. We're a tech company so fast WiFi is critical.",
        "intent": "group_event",
        "search_query": "Austin TX, group booking 50 rooms, 3 nights, meeting_space:50pax, breakout_rooms:3+, wifi:high_bandwidth",
        "search_results": "Hospitality Austin (available, group rate $199/night, 10K sqft meeting space), Upscale Select Austin (available, $169/night, 6K sqft), Select Service Inn Austin Downtown ($149/night, limited meeting space)",
        "loyalty_profile": '{"tier": "Silver", "points": 45000, "lifetime_nights": 22, "company": "Stripe", "group_history": ["Hospitality San Francisco 2024 Q3 offsite"]}',
        "rag_docs": "Hospitality Austin: 800 rooms, 40,000 sqft total meeting space, dedicated 1Gbps fiber for events, AV equipment included, rooftop pool with downtown views, 6th Street entertainment district walkable, Hospitality Events team provides dedicated coordinator for groups 25+, group WiFi package: dedicated SSID with guaranteed 500Mbps symmetric",
        "embedding_input": "corporate offsite, 50 people, Austin Texas, meeting space, breakout rooms, high speed WiFi, tech company, group rate",
        "recommendation": "I recommend the **Hospitality Austin** for your tech offsite.\n\n**Meeting setup:**\n- Grand Ballroom (50 theater / 40 classroom) as main session room\n- 3 breakout rooms on same floor (10-15 pax each)\n- Dedicated 500Mbps symmetric WiFi with custom SSID for your team\n- AV equipment included (projectors, screens, microphones)\n\n**Group rate:** $199/night for 50 rooms (21% below BAR)\n**Total estimate:** $29,850 for 50 rooms x 3 nights\n\n**Perks for your group:**\n- Dedicated Hospitality Events coordinator\n- Complimentary welcome reception on arrival night\n- Rooftop pool for evening networking\n- Walking distance to 6th Street\n\n**Loyalty bonus:** All attendees earn points even on group rate\n\nI can connect you with the events team to finalize. Want me to send an RFP?",
        "model": "gpt-4-turbo",
        "provider": "azure_openai",
        "property_type": "Full Service",
        "region": "Americas",
    },
    {
        "name": "Last-Minute Weekend Getaway",
        "user_input": "Looking for something spontaneous this weekend — a beach resort within 3 hours of London. Budget around £300/night. Loyalty Silver.",
        "intent": "spontaneous_travel",
        "search_query": "Beach resort, 3hr radius London, 2 nights, this weekend, budget:300GBP, loyalty_tier:silver",
        "search_results": "Hospitality Bournemouth (available, £189/night), Select Service Brighton (available, £149/night), Premium Resort Algarve Portugal (available, £279/night, 2.5hr flight)",
        "loyalty_profile": '{"tier": "Silver", "points": 78000, "lifetime_nights": 34, "preferences": ["sea_view", "spa", "late_checkout"], "home_airport": "LHR"}',
        "rag_docs": "Hospitality Bournemouth: beachfront property, 7 miles of sandy beach, spa with ocean-view treatment rooms, heated outdoor pool (seasonal), 2 restaurants including seafood terrace, Silver members get 5th night free on weekend stays, rooms renovated 2024 with sea-view balconies on floors 4-8",
        "embedding_input": "spontaneous weekend, beach resort, near London, budget 300GBP, sea view, spa, Silver member",
        "recommendation": "For a quick beach escape, I recommend **Hospitality Bournemouth**.\n\n**Why it works for this weekend:**\n- Available this Saturday-Sunday (I checked real-time inventory)\n- £189/night — well under your £300 budget\n- 2-hour train from London Waterloo (next train at 10:15)\n\n**The property:**\n- Direct beachfront on 7 miles of sandy beach\n- Recently renovated sea-view rooms with balcony (floors 4-8)\n- Full spa with ocean-view treatment rooms\n- Seafood terrace restaurant\n\n**Savings tip:** At £189/night, you'd save £222 vs. budget — or use 30,000 points for one night and pay cash for the other.\n\nWant me to book for this Saturday?",
        "model": "gpt-4-turbo",
        "provider": "azure_openai",
        "property_type": "Full Service",
        "region": "EMEA",
    },
    {
        "name": "Accessible Room Request",
        "user_input": "I need an accessible room in Tokyo for 5 nights. Wheelchair accessible with roll-in shower. Traveling with my service dog. Diamond member.",
        "intent": "accessibility_request",
        "search_query": "Tokyo, 1 adult, 5 nights, accessible:wheelchair, roll_in_shower:required, service_animal:yes, loyalty_tier:diamond",
        "search_results": "Premium Resort Tokyo (available, accessible king, $420/night Diamond), Hospitality Tokyo Odaiba (available, accessible twin, $310/night), Hospitality Tokyo (available, accessible king, $350/night Diamond)",
        "loyalty_profile": '{"tier": "Diamond", "points": 620000, "lifetime_nights": 412, "preferences": ["accessible_room", "low_floor", "service_animal"], "accessibility": {"wheelchair": true, "roll_in_shower": true}}',
        "rag_docs": "Premium Resort Tokyo: 37-floor luxury hotel in Shiodome, ADA-compliant rooms on floors 30-33 with widened doorways (36in), roll-in showers with bench and handheld showerhead, lowered closet bars and desk, service animals welcome with pet relief area on ground floor garden. Diamond benefits: complimentary breakfast at Collage restaurant, executive lounge access (accessible), complimentary minibar",
        "embedding_input": "accessible room, wheelchair, roll-in shower, service dog, Tokyo, 5 nights, Diamond member, low floor preference",
        "recommendation": "I recommend the **Premium Resort Tokyo** with their dedicated accessible room.\n\n**Accessibility features confirmed:**\n- Wheelchair-accessible king room (36-inch doorways)\n- Roll-in shower with bench seat and handheld showerhead\n- Lowered closet bars, desk, and amenities\n- Service dog welcome — ground floor garden has designated pet relief area\n- I've noted low-floor preference (room will be on floor 30-33 accessible wing)\n\n**Diamond benefits:**\n- Complimentary breakfast at Collage (fully accessible)\n- Executive lounge access (elevator accessible)\n- Complimentary minibar daily\n- Guaranteed late checkout until 4pm\n\n**Rate:** ¥62,000/night ($420 Diamond rate) or 95,000 points/night\n**Points option:** 475,000 points for 5 nights — leaves 145,000 remaining\n\nI've flagged your service animal and accessibility requirements in the reservation notes. Book this?",
        "model": "gpt-4-turbo",
        "provider": "azure_openai",
        "property_type": "Premium Resort",
        "region": "APAC",
    },
]

# ---------------------------------------------------------------------------
# Error scenarios for realism
# ---------------------------------------------------------------------------

ERROR_SCENARIOS = [
    {
        "user_input": "Find me a resort in Maui with availability this holiday weekend.",
        "error_msg": "Model inference timeout after 30000ms — Azure OpenAI endpoint us-east-2 throttled (429 Too Many Requests). Retry budget exhausted.",
    },
    {
        "user_input": "I want to compare loyalty redemption values across all Luxury Collection properties worldwide.",
        "error_msg": "Context window exceeded: 131,072 token limit. Input context (142,891 tokens) includes 89 property profiles. Consider chunked retrieval strategy.",
    },
    {
        "user_input": "Book me the cheapest room tonight near the airport in Dallas.",
        "error_msg": "Guardrail triggered: response contained competitor property recommendation (Marriott Courtyard). Content filter blocked output. Falling back to curated response.",
    },
]


# ---------------------------------------------------------------------------
# Prompt templates — versioned for prompt tracking
# ---------------------------------------------------------------------------
# Datadog LLM Obs tracks prompt versions automatically via template hashing.
# We define multiple versions to show version evolution in the UI.

INTENT_PROMPT_VERSIONS = [
    {
        "id": "intent-classifier",
        "version": "1.0.0",
        "template": "You are the hospitality brand's AI Stay Planner intent classifier. Classify the guest request into one of: {{intents}}. Return JSON with intent and extracted entities.",
        "variables": {"intents": "family_vacation, business_travel, points_redemption, group_event, spontaneous_travel, accessibility_request, romantic_getaway"},
        "weight": 0.3,  # 30% of traffic — older version
    },
    {
        "id": "intent-classifier",
        "version": "2.0.0",
        "template": "You are the hospitality brand's AI Stay Planner intent classifier v2. Classify the guest request and extract structured entities including destination, dates, party_size, loyalty_tier, and special_requirements. Intents: {{intents}}. Return JSON.",
        "variables": {"intents": "family_vacation, business_travel, points_redemption, group_event, spontaneous_travel, accessibility_request, romantic_getaway"},
        "weight": 0.7,  # 70% of traffic — newer version
    },
]

RECOMMENDATION_PROMPT_VERSIONS = [
    {
        "id": "recommendation-generator",
        "version": "1.0.0",
        "template": "You are the hospitality brand's AI Stay Planner. Generate a personalised hotel recommendation. Be specific about property features, loyalty benefits, and pricing. Use markdown formatting.",
        "variables": {},
        "weight": 0.4,
    },
    {
        "id": "recommendation-generator",
        "version": "2.1.0",
        "template": "You are the hospitality brand's AI Stay Planner. Generate a personalised hotel recommendation for a {{loyalty_tier}} member. Prioritise: 1) loyalty benefit maximisation, 2) property match to stated preferences, 3) value optimisation (points vs cash). Include specific dollar savings. Format with markdown headers.",
        "variables": {"loyalty_tier": "Diamond"},
        "weight": 0.6,
    },
]


# ---------------------------------------------------------------------------
# Model variants — for A/B experiment simulation
# ---------------------------------------------------------------------------
# Different model configs to create experiment-like comparisons in LLM Obs.

MODEL_VARIANTS = [
    {
        "model": "gpt-4-turbo",
        "provider": "azure_openai",
        "temperature": 0.7,
        "max_tokens": 4096,
        "experiment_tag": "model-experiment:gpt4-turbo-baseline",
        "weight": 0.5,
    },
    {
        "model": "gpt-4o",
        "provider": "azure_openai",
        "temperature": 0.6,
        "max_tokens": 4096,
        "experiment_tag": "model-experiment:gpt4o-challenger",
        "weight": 0.35,
    },
    {
        "model": "gpt-4o-mini",
        "provider": "azure_openai",
        "temperature": 0.5,
        "max_tokens": 2048,
        "experiment_tag": "model-experiment:gpt4o-mini-cost-opt",
        "weight": 0.15,
    },
]


# ---------------------------------------------------------------------------
# Evaluation definitions
# ---------------------------------------------------------------------------
# Custom evaluations submitted via Datadog HTTP API after each trace.

EVALUATION_DEFINITIONS = [
    {
        "label": "recommendation_quality",
        "metric_type": "score",
        "range": (0.55, 0.98),
        "error_range": (0.1, 0.35),
        "description": "Overall quality of the hotel recommendation",
    },
    {
        "label": "loyalty_accuracy",
        "metric_type": "score",
        "range": (0.7, 1.0),
        "error_range": (0.2, 0.5),
        "description": "Accuracy of loyalty tier benefits and point calculations",
    },
    {
        "label": "hallucination_score",
        "metric_type": "score",
        "range": (0.0, 0.08),  # Lower is better — low hallucination
        "error_range": (0.15, 0.45),
        "description": "Hallucination detection — lower is better",
    },
    {
        "label": "topic_relevance",
        "metric_type": "score",
        "range": (0.82, 0.99),
        "error_range": (0.3, 0.6),
        "description": "Relevance of response to user's travel request",
    },
    {
        "label": "sentiment",
        "metric_type": "categorical",
        "categories": ["positive", "positive", "positive", "neutral"],  # weighted toward positive
        "error_categories": ["negative", "neutral"],
        "description": "Sentiment of the recommendation response",
    },
]


# Capture the hospitality library (the module-level defaults above) under
# explicit names so the hospitality branch can restore them regardless of any
# prior global reassignment by finance/generic.
HOSPITALITY_SCENARIOS = SCENARIOS
HOSPITALITY_ERROR_SCENARIOS = ERROR_SCENARIOS
HOSPITALITY_INTENT_PROMPT_VERSIONS = INTENT_PROMPT_VERSIONS
HOSPITALITY_RECOMMENDATION_PROMPT_VERSIONS = RECOMMENDATION_PROMPT_VERSIONS
HOSPITALITY_MODEL_VARIANTS = MODEL_VARIANTS
HOSPITALITY_EVALUATION_DEFINITIONS = EVALUATION_DEFINITIONS


# ---------------------------------------------------------------------------
# Healthcare library — AdventHealth AI Care Companion
# ---------------------------------------------------------------------------
# Selected when vertical_name="healthcare". Emits LLM Obs traces under
# ml_app="ai-care-companion" with clinical-safety + cost evals — the
# executive-grade healthcare AI story. Same field contract as the other
# libraries (intent / property_type / region / loyalty_profile etc.); here
# `property_type` carries the question topic (Medication / Discharge / ...)
# and `loyalty_profile` carries the patient or clinician profile.

HEALTHCARE_SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "Medication Question — Missed Dose",
        "user_input": "I forgot my evening metformin yesterday. Should I double up this morning?",
        "intent": "medication_question",
        "search_query": "med:metformin, event:missed_dose, patient_role:diabetic",
        "search_results": "Metformin (AdventHealth med guide) — missed-dose policy; renal-function flag.",
        "loyalty_profile": '{"patient_id": "pat-1001", "acuity": "stable", "diagnoses": ["T2DM"], "preferences": ["med_reminders"], "language": "en"}',
        "rag_docs": "Metformin missed-dose policy (AdventHealth): if a dose is missed, skip it and resume the normal schedule; do NOT double up — risk of GI distress and hypoglycemia when combined with other agents. Contact the care team if multiple consecutive doses are missed. Renal function check required every 6 months.",
        "embedding_input": "metformin, missed dose, double dose, type 2 diabetes, AdventHealth med guide",
        "recommendation": "Don't double up — skip the missed evening dose and take your normal morning dose. Doubling metformin raises the risk of low blood sugar and stomach upset. If you've missed several doses in a row, message your care team. Want me to log this missed dose and notify your nurse?",
        "model": "gpt-4o",
        "provider": "azure_openai",
        "property_type": "Medication",
        "region": "Americas",
    },
    {
        "name": "Discharge Instructions — Post-Op TKA",
        "user_input": "After my knee replacement, when can I shower and how do I care for the incision?",
        "intent": "discharge_instructions",
        "search_query": "procedure:tka, doc_type:discharge_plan, topic:wound_care",
        "search_results": "Post-op TKA discharge plan (AdventHealth Orthopedics).",
        "loyalty_profile": '{"patient_id": "pat-1003", "acuity": "stable", "procedure": "TKA", "post_op_day": 2, "language": "en"}',
        "rag_docs": "Post-op TKA discharge (AdventHealth Orthopedics): keep incision dry for 48 hours, then showering is permitted; pat (do not rub) the incision dry. No baths, soaking, or pools for 3 weeks. Escalation criteria: spreading redness, drainage, separation, or fever >100.4°F. Follow-up wound check at 10–14 days.",
        "embedding_input": "knee replacement, shower, incision care, discharge instructions, post-op day 2",
        "recommendation": "You can shower after the first 48 hours — keep the incision dry until then, then pat (don't rub) it dry. No baths or soaking for 3 weeks. Call us for spreading redness, drainage, or a fever over 100.4°F. Your follow-up wound check is set for day 10. Want me to confirm the appointment?",
        "model": "gpt-4o",
        "provider": "azure_openai",
        "property_type": "Discharge",
        "region": "Americas",
    },
    {
        "name": "Symptom Triage — Chest Tightness (Escalate)",
        "user_input": "I've had chest tightness and shortness of breath since this morning.",
        "intent": "symptom_triage",
        "search_query": "symptom:chest_tightness, symptom:dyspnea, risk:cardiac",
        "search_results": "Triage policy: chest pain/tightness + dyspnea → emergency pathway; do not self-manage.",
        "loyalty_profile": '{"patient_id": "pat-1004", "acuity": "critical", "diagnoses": ["HTN","HLD"], "fall_risk": false}',
        "rag_docs": "AdventHealth triage policy (cardiac): any patient reporting chest tightness or chest pain with associated shortness of breath must be escalated to the emergency pathway. The AI assistant must NOT attempt self-management; immediate human-clinician contact and 911 guidance are required.",
        "embedding_input": "chest tightness, shortness of breath, possible cardiac, escalation, emergency",
        "recommendation": "Chest tightness with shortness of breath needs urgent evaluation — I'm escalating you to a clinician right now, and if it gets worse or you develop arm/jaw pain, sweating, or fainting, call 911. I am NOT able to manage this in-app. Connecting you to the on-call clinician.",
        "model": "gpt-4o",
        "provider": "azure_openai",
        "property_type": "Triage",
        "region": "Americas",
    },
    {
        "name": "Care-Plan Summary — Overnight Status",
        "user_input": "Summarize overnight status and open care-plan items for bed MedSurg-304.",
        "intent": "care_plan",
        "search_query": "bed:MedSurg-304, period:overnight, items:open",
        "search_results": "MedSurg-304 overnight: telemetry stable; pain 4/10; pending orders (CBC, ambulation); fall-risk flag.",
        "loyalty_profile": '{"clinician_id": "dr-rivera", "role": "hospitalist", "shift": "day", "scope": "MedSurg-3E"}',
        "rag_docs": "MedSurg-304 chart snapshot — acuity: critical. Overnight telemetry remained stable (HR 72-88, SpO2 95-97). Pain 4/10 controlled with oxycodone. Open items: pending CBC (ordered 22:00), first ambulation pending PT clearance. Fall-risk flag set; bed alarm confirmed active.",
        "embedding_input": "overnight summary, MedSurg-304, telemetry, pain, pending orders, fall risk, clinician",
        "recommendation": "MedSurg-304 (critical): telemetry stable overnight (HR 72-88, SpO2 95-97). Pain 4/10 controlled. Two open items — pending CBC and first ambulation (PT clearance pending). Fall-risk flag is set; bed alarm confirmed active. Want me to draft the order acknowledgements and ping PT?",
        "model": "gpt-4o",
        "provider": "azure_openai",
        "property_type": "Care Plan",
        "region": "Americas",
    },
    {
        "name": "Appointment / Follow-up",
        "user_input": "When is my next follow-up and what should I bring?",
        "intent": "appointment",
        "search_query": "patient_id:pat-1003, type:followup, period:next_30d",
        "search_results": "Next follow-up: ortho 2026-06-26 09:30 (Dr. Patel). Bring med list, incision photos if redness.",
        "loyalty_profile": '{"patient_id": "pat-1003", "primary_provider": "Dr. Patel", "portal_consent": true}',
        "rag_docs": "AdventHealth follow-up policy: arrive 15 minutes early, bring current med list (paper or via portal), insurance card, and any post-op photos of the incision if you've noted concerns. Telehealth is supported for routine follow-ups.",
        "embedding_input": "next follow-up appointment, what to bring, telehealth option, orthopedic post-op",
        "recommendation": "Your next follow-up is **Friday, June 26 at 9:30 AM** with Dr. Patel (orthopedics). Please arrive 15 minutes early and bring your current med list and insurance card. If you've noticed any redness or drainage, bring or upload a photo. Telehealth is also an option — want me to switch this to a video visit?",
        "model": "gpt-4o",
        "provider": "azure_openai",
        "property_type": "Appointment",
        "region": "Americas",
    },
]

HEALTHCARE_ERROR_SCENARIOS = [
    {
        "user_input": "Summarize all medication interactions across my full chart history.",
        "error_msg": "Context window exceeded: 131,072 token limit. Chart history (228k tokens) exceeds context. Use chunked retrieval / chart summary.",
    },
    {
        "user_input": "What's my temperature trend from the last vitals?",
        "error_msg": "Model inference timeout after 30000ms — Azure OpenAI endpoint throttled (429 Too Many Requests). Retry budget exhausted.",
    },
    {
        "user_input": "Recommend a specific dose of a controlled substance based on my pain level.",
        "error_msg": "Clinical-safety guardrail triggered: AI is not authorized to prescribe controlled-substance dosing. Falling back to escalation to clinician.",
    },
]

HEALTHCARE_INTENT_PROMPT_VERSIONS = [
    {
        "id": "intent-classifier",
        "version": "1.0.0",
        "template": "You are AdventHealth's AI Care Companion intent classifier. Classify the user request into one of: {{intents}}. Return JSON with intent and extracted entities.",
        "variables": {"intents": "medication_question, discharge_instructions, symptom_triage, care_plan, appointment, billing, general_question"},
        "weight": 0.3,
    },
    {
        "id": "intent-classifier",
        "version": "2.0.0",
        "template": "You are AdventHealth's AI Care Companion intent classifier v2. Classify the request and extract structured entities including topic, urgency (routine/urgent/emergency), and whether escalation to a human clinician is required by policy. Intents: {{intents}}. Return JSON.",
        "variables": {"intents": "medication_question, discharge_instructions, symptom_triage, care_plan, appointment, billing, general_question"},
        "weight": 0.7,
    },
]

HEALTHCARE_RECOMMENDATION_PROMPT_VERSIONS = [
    {
        "id": "recommendation-generator",
        "version": "1.0.0",
        "template": "You are AdventHealth's AI Care Companion. Answer using ONLY the retrieved care guidance. If the question involves urgent clinical risk, you MUST recommend escalation to a human clinician rather than self-manage. Be plain-language and supportive. Use markdown.",
        "variables": {},
        "weight": 0.4,
    },
    {
        "id": "recommendation-generator",
        "version": "2.1.0",
        "template": "You are AdventHealth's AI Care Companion. Answer using ONLY the retrieved guidance, citing AdventHealth policies where relevant. NEVER recommend specific controlled-substance dosing. ALWAYS escalate symptom-triage flags (chest pain, dyspnea, neuro changes, severe bleeding) to a human clinician. Format with markdown headers; close with a clear next step the patient or clinician can take.",
        "variables": {},
        "weight": 0.6,
    },
]

HEALTHCARE_EVALUATION_DEFINITIONS = [
    {
        "label": "clinical_groundedness",
        "metric_type": "score",
        "range": (0.85, 0.99),
        "error_range": (0.35, 0.65),
        "description": "Was the answer grounded in AdventHealth's care guidance?",
    },
    {
        "label": "hallucination_risk",
        "metric_type": "score",
        "range": (0.0, 0.08),  # lower is better
        "error_range": (0.30, 0.60),
        "description": "Risk that the answer contains unsupported clinical content (lower is better).",
    },
    {
        "label": "phi_handling",
        "metric_type": "score",
        "range": (0.92, 1.0),
        "error_range": (0.6, 0.85),
        "description": "Was PHI handled per policy (no unnecessary disclosure)?",
    },
    {
        "label": "answer_relevance",
        "metric_type": "score",
        "range": (0.85, 0.99),
        "error_range": (0.4, 0.7),
        "description": "Relevance of the response to the patient/clinician question.",
    },
    {
        "label": "escalation_appropriateness",
        "metric_type": "categorical",
        "categories": ["appropriate", "appropriate", "appropriate", "under-escalated"],
        "error_categories": ["under-escalated", "over-escalated"],
        "description": "Did the AI escalate to a human at the right time (per AdventHealth triage policy)?",
    },
]


# ---------------------------------------------------------------------------
# Generic library — vertical-neutral AI Assistant
# ---------------------------------------------------------------------------
# Used by default for any vertical without its own library. A plain enterprise
# knowledge/support assistant so the LLM Obs traces aren't tied to a specific
# industry. Reuses the same field names as the
# other libraries (`intent`, `property_type`, `region`) for compatibility —
# here `property_type` carries a generic topic and `loyalty_profile` a generic
# user profile. Span attribute keys are emitted under the `assistant.*` prefix.

GENERIC_SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "Account Access Help",
        "user_input": "I can't log in — my MFA device was replaced and I'm locked out. Enterprise admin account.",
        "intent": "account_access",
        "search_query": "topic:mfa_reset, account_type:admin, plan:enterprise, channel:assistant",
        "search_results": "KB-1042 MFA reset procedure, KB-088 admin recovery, ticket history (no prior lockouts), identity-provider status: operational.",
        "loyalty_profile": '{"plan": "Enterprise", "role": "admin", "tenure_months": 18, "preferences": ["email_updates"], "seats": 240}',
        "rag_docs": "MFA reset (KB-1042): admins can self-serve a reset from Settings → Security → Reset MFA after verifying via the recovery email on file. If the recovery email is also unavailable, an org owner can trigger a reset from the Members page. Lockouts auto-clear after 15 minutes of no attempts. SSO-backed orgs reset MFA at the identity provider, not in-app.",
        "embedding_input": "mfa reset, locked out, replaced device, enterprise admin, recovery email, sso",
        "recommendation": "Here's how to get back in:\n\n**Fastest path (self-serve):**\n1. Go to **Settings → Security → Reset MFA**.\n2. Verify with the recovery email on file.\n3. Re-enroll your new device.\n\n**If the recovery email is also unavailable:**\n- Have an **org owner** reset it for you from the **Members** page.\n\n**If your org uses SSO:** the MFA reset happens at your identity provider, not here — contact your IdP admin.\n\nLockouts also auto-clear after 15 minutes of no attempts. Want me to open a ticket as a backup?",
        "model": "gpt-4o",
        "provider": "azure_openai",
        "property_type": "Account & Access",
        "region": "Americas",
    },
    {
        "name": "How-To: Export a Report",
        "user_input": "How do I export last quarter's usage report as a CSV and schedule it to email monthly?",
        "intent": "how_to",
        "search_query": "topic:report_export, format:csv, schedule:monthly, plan:enterprise",
        "search_results": "KB-2210 exporting reports, KB-2231 scheduled exports, feature availability: scheduled exports on Enterprise plan.",
        "loyalty_profile": '{"plan": "Enterprise", "role": "analyst", "tenure_months": 6, "preferences": ["csv", "scheduled_reports"]}',
        "rag_docs": "Exporting (KB-2210): open the report, set the time range, then Export → CSV. Scheduled exports (KB-2231, Enterprise only): from a saved report choose Schedule → frequency (daily/weekly/monthly) → recipients. Scheduled CSVs are delivered as email attachments; files over 25MB are linked instead of attached.",
        "embedding_input": "export report, csv, schedule monthly, email delivery, saved report, enterprise",
        "recommendation": "Two steps:\n\n**1) Export last quarter as CSV**\n- Open the report, set the time range to **last quarter**.\n- Click **Export → CSV**.\n\n**2) Schedule it monthly**\n- Save the report first (Scheduling works off a saved report).\n- Choose **Schedule → Monthly**, add recipients.\n- It'll arrive as an email attachment; files over 25MB come as a download link.\n\nScheduled exports are an **Enterprise** feature — which your org has. Want me to set the schedule to the first of each month?",
        "model": "gpt-4o",
        "provider": "azure_openai",
        "property_type": "Reporting",
        "region": "EMEA",
    },
    {
        "name": "Troubleshooting: Slow Dashboard",
        "user_input": "One of my dashboards takes 20+ seconds to load. The others are fine. What should I check?",
        "intent": "troubleshooting",
        "search_query": "topic:dashboard_performance, symptom:slow_load, scope:single_dashboard",
        "search_results": "KB-3301 dashboard performance, KB-3318 widget query cost, telemetry: 38 widgets on the affected dashboard, 9 with wide time ranges.",
        "loyalty_profile": '{"plan": "Enterprise", "role": "editor", "tenure_months": 31, "preferences": ["dark_mode"]}',
        "rag_docs": "Dashboard performance (KB-3301): load time scales with widget count and per-widget query cost. Common causes: too many widgets on one board (>30), widgets with very wide time ranges, and high-cardinality group-bys. Fixes: split into multiple boards, narrow default time range, reduce group-by cardinality, and use scalar widgets instead of timeseries where a single number suffices.",
        "embedding_input": "slow dashboard, 20 seconds load, single dashboard, widget count, time range, cardinality",
        "recommendation": "Since only one dashboard is slow, it's almost certainly that board's query load, not the platform. Check, in order:\n\n1. **Widget count** — this board has **38 widgets**; anything over ~30 noticeably slows load. Split it into 2 boards.\n2. **Wide time ranges** — 9 widgets pull very wide ranges. Narrow the dashboard's default window.\n3. **High-cardinality group-bys** — reduce `by {…}` cardinality where you can.\n4. **Use scalar widgets** for single-number views instead of timeseries.\n\nStart with the split — it's the biggest win here. Want me to flag the 9 wide-range widgets?",
        "model": "gpt-4o",
        "provider": "azure_openai",
        "property_type": "Performance",
        "region": "APAC",
    },
]

GENERIC_ERROR_SCENARIOS = [
    {
        "user_input": "Summarize every knowledge-base article we have about billing.",
        "error_msg": "Context window exceeded: 131,072 token limit. Input context (158,402 tokens) includes 214 KB articles. Use chunked retrieval.",
    },
    {
        "user_input": "What's the status of my open ticket?",
        "error_msg": "Model inference timeout after 30000ms — provider endpoint throttled (429 Too Many Requests). Retry budget exhausted.",
    },
    {
        "user_input": "Compare our pricing to your competitors and tell me who's cheaper.",
        "error_msg": "Guardrail triggered: response contained unverified competitor claim. Content filter blocked output. Falling back to curated response.",
    },
]

GENERIC_INTENT_PROMPT_VERSIONS = [
    {
        "id": "intent-classifier",
        "version": "1.0.0",
        "template": "You are an enterprise AI assistant's intent classifier. Classify the user request into one of: {{intents}}. Return JSON with intent and extracted entities.",
        "variables": {"intents": "account_access, how_to, troubleshooting, billing, feature_request, general_question"},
        "weight": 0.3,
    },
    {
        "id": "intent-classifier",
        "version": "2.0.0",
        "template": "You are an enterprise AI assistant's intent classifier v2. Classify the request and extract structured entities including topic, plan, and urgency. Intents: {{intents}}. Return JSON.",
        "variables": {"intents": "account_access, how_to, troubleshooting, billing, feature_request, general_question"},
        "weight": 0.7,
    },
]

GENERIC_RECOMMENDATION_PROMPT_VERSIONS = [
    {
        "id": "recommendation-generator",
        "version": "1.0.0",
        "template": "You are a helpful enterprise AI assistant. Answer the user's question accurately using the retrieved knowledge. Be specific and actionable. Use markdown formatting.",
        "variables": {},
        "weight": 0.4,
    },
    {
        "id": "recommendation-generator",
        "version": "2.1.0",
        "template": "You are a helpful enterprise AI assistant. Answer the user's question using ONLY the retrieved knowledge; if the answer isn't supported, say so. Prioritise: 1) correctness, 2) the shortest path to resolution, 3) a clear next step. Format with markdown headers.",
        "variables": {},
        "weight": 0.6,
    },
]

GENERIC_EVALUATION_DEFINITIONS = [
    {
        "label": "answer_relevance",
        "metric_type": "score",
        "range": (0.82, 0.99),
        "error_range": (0.3, 0.6),
        "description": "Relevance of the response to the user's question",
    },
    {
        "label": "faithfulness",
        "metric_type": "score",
        "range": (0.7, 1.0),
        "error_range": (0.2, 0.5),
        "description": "Groundedness of the answer in the retrieved knowledge",
    },
    {
        "label": "hallucination_score",
        "metric_type": "score",
        "range": (0.0, 0.08),
        "error_range": (0.15, 0.45),
        "description": "Hallucination detection — lower is better",
    },
    {
        "label": "completeness",
        "metric_type": "score",
        "range": (0.75, 0.98),
        "error_range": (0.2, 0.5),
        "description": "Whether the answer fully addresses the request",
    },
    {
        "label": "sentiment",
        "metric_type": "categorical",
        "categories": ["positive", "positive", "positive", "neutral"],
        "error_categories": ["negative", "neutral"],
        "description": "Sentiment of the assistant response",
    },
]


# ---------------------------------------------------------------------------
# Finance library — EY CT Consulting Risk Portfolio scenarios
# ---------------------------------------------------------------------------
# When LLMObsSubmitter is instantiated with vertical_name="finance", the
# module-level constants above are swapped to the FINANCE_* variants
# below. Only one LLMObsSubmitter exists per process, so the swap is safe.
#
# Scenarios reuse the hospitality library's field names (`intent`,
# `property_type`, `region`) for backwards-compat — `property_type` here
# carries the EY engagement tier, `region` carries the client engagement
# region. Span attribute keys are emitted under the `ey.*` prefix when
# vertical_name="finance" (see LLMObsSubmitter.__init__).

FINANCE_SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "Credit-Counterparty Risk Memo",
        "user_input": "Draft a credit-risk memo for counterparty ACME Capital. Latest 10-K plus internal trade exposure attached. Highlight covenant breaches and concentration risk.",
        "intent": "credit_risk_memo",
        "search_query": "counterparty:acme_capital, exposure_band:tier1, jurisdiction:US, doc_types:[10K, swap_master, internal_credit_memo]",
        "search_results": "10-K excerpt (covenant ratios), internal credit memo v3, ISDA master schedule, current swap exposure $1.2B, concentration limit breach (40% > 35% policy).",
        "loyalty_profile": '{"engagement": "acme_audit_2026q2", "tier": "Tier 1", "client_lead": "Aneesh Pulukkul", "qrm_required": true, "client_jurisdictions": ["US", "UK"]}',
        "rag_docs": "ACME Capital Q1 2026 10-K: revenue down 12% YoY, debt-to-EBITDA 4.8x (covenant 4.5x — breached), interest coverage 1.9x (covenant 2.0x — breached). Internal trade exposure on equity derivatives $1.2B notional, concentration 40% of tier-1 desk limit. EY engagement scope: Q2 review under SOX §404.",
        "embedding_input": "credit risk memo, ACME Capital, covenant breach, concentration limit, equity derivatives, SOX 404 review",
        "recommendation": "**Credit Risk Memo — ACME Capital (Q2 2026)**\n\n**Headline:** Two-covenant breach + concentration overage. Recommend status downgrade to Watch List.\n\n**Findings:**\n- Debt/EBITDA 4.8x vs 4.5x covenant — quantitative breach, no waiver in file\n- Interest coverage 1.9x vs 2.0x covenant — quantitative breach\n- Equity-derivatives exposure $1.2B = 40% of tier-1 limit (policy: 35%)\n\n**Recommended actions:**\n1. Escalate to ACME relationship manager for waiver request\n2. Reduce equity-derivatives exposure by ~$150M to bring within policy\n3. QRM review required before Q3 close (SOX §404 scope)\n\n**Confidence:** High — both breaches are quantitative and unambiguous.",
        "model": "gpt-4.5",
        "provider": "azure_openai",
        "property_type": "Tier 1 Risk Advisory",
        "region": "Americas",
    },
    {
        "name": "Regulatory Compliance Q&A — SAR Filing",
        "user_input": "Walk me through the SAR filing decision for the flagged wire to high-risk jurisdiction. Customer is a Silver-tier corporate client.",
        "intent": "sar_filing_guidance",
        "search_query": "sar_decision, jurisdiction:high_risk, customer_tier:corporate_silver, wire_value:>50k, fincen_threshold:10k",
        "search_results": "FinCEN SAR threshold guidance §1020.320, internal SAR decision tree v2.4, customer KYC profile, prior SAR history (none in last 24 months).",
        "loyalty_profile": '{"engagement": "globex_compliance_2026q2", "tier": "Tier 2", "client_lead": "Sivakumar Sundaram", "qrm_required": false}',
        "rag_docs": "FinCEN §1020.320 requires a SAR within 30 days of detection when a transaction is suspected of involving funds from illegal activity, designed to evade BSA, has no apparent business purpose, or facilitates criminal activity. Wires to FATF grey-list jurisdictions over $50K trigger automatic enhanced due diligence per internal policy ECP-7.3. Customer KYC: no prior SAR, expected wire activity matches stated business purpose.",
        "embedding_input": "SAR filing decision, FinCEN, high-risk jurisdiction, wire transfer, corporate client, enhanced due diligence",
        "recommendation": "**SAR Filing Decision — Recommend FILE within 30 days.**\n\n**Reasoning:**\n- Wire size ($72K) exceeds the $50K trigger for enhanced due diligence under ECP-7.3\n- Destination jurisdiction is on the FATF grey list — by itself a SAR factor under FinCEN §1020.320\n- Stated business purpose is plausible (vendor payment) but unverified\n- Filing under FinCEN §1020.320(a)(2)(iii) — \"no apparent lawful purpose\" because purpose remains unverified\n\n**Required actions:**\n1. File SAR within 30 days of detection (today + 30 = file by 2026-06-18)\n2. Do NOT notify customer (tipping-off prohibition under §1020.320(e))\n3. Continue monitoring — no account closure required at this stage\n\n**Confidence:** Medium-high — file is the safer choice; the unverified purpose tips the decision.",
        "model": "gpt-4.5",
        "provider": "azure_openai",
        "property_type": "Tier 2 Compliance",
        "region": "Americas",
    },
    {
        "name": "Audit Finding Synthesis — Q2 Internal Audit",
        "user_input": "Summarise the internal audit findings for the Capital Markets desk Q2 review. Three separate audit reports attached.",
        "intent": "audit_finding_synthesis",
        "search_query": "audit_findings, desk:capital_markets, period:2026q2, doc_types:[trade_surveillance_audit, risk_limit_audit, books_records_audit]",
        "search_results": "Trade surveillance audit (3 findings, 1 high), Risk limit audit (5 findings, 2 high), Books & records audit (2 findings, 0 high).",
        "loyalty_profile": '{"engagement": "initech_audit_2026q2", "tier": "Tier 1", "client_lead": "Jaganathan T", "qrm_required": true}',
        "rag_docs": "Trade surveillance audit: gap in pre-trade compliance check for FIX session timeouts (High), late trade-blotter reconciliation 3 instances (Medium), missing supervisor sign-off on 12 trades (Medium). Risk limit audit: VaR limit breach on 3 days unreported within SLA (High), stress-test parameter drift (High), margin call response time exceeds policy on 4 instances (Medium). Books & records audit: trade-time stamp variance across systems (Medium), missing trade-purpose annotation on 8% of trades (Low).",
        "embedding_input": "internal audit findings synthesis, capital markets desk, trade surveillance, risk limits, books and records, Q2 2026",
        "recommendation": "**Q2 2026 Internal Audit Summary — Capital Markets Desk**\n\n**Top-line:** 10 findings total (3 High, 5 Medium, 2 Low). Three High-severity items concentrate in pre-trade controls and VaR oversight.\n\n**High-severity findings (require immediate remediation):**\n1. **Pre-trade compliance check gap** on FIX session timeouts — trade surveillance audit\n2. **VaR limit breaches** unreported within SLA on 3 days — risk limit audit\n3. **Stress-test parameter drift** — risk limit audit\n\n**Concentration risk:** 2 of 3 High findings sit in the risk-limit control surface. Recommend a focused review of the risk infrastructure controls in Q3, not a desk-wide overhaul.\n\n**QRM gate:** Required before sign-off — all High-severity items require partner review under engagement policy.\n\n**Confidence:** High — synthesis is mechanical from the source reports.",
        "model": "gpt-4.5",
        "provider": "azure_openai",
        "property_type": "Tier 1 Audit",
        "region": "Americas",
    },
    {
        "name": "Model Risk Validation — VaR Explanation",
        "user_input": "Explain why the VaR model flagged the FX swap book yesterday. Senior management wants the plain-English version.",
        "intent": "model_explanation",
        "search_query": "var_explanation, book:fx_swaps, date:2026-05-18, model_version:varcalc_v3.2",
        "search_results": "VaR run yesterday: 99% 1-day VaR = $48M (limit $30M). Top driver: GBP/USD vol shock contribution $32M. Stress-test overlay added $8M.",
        "loyalty_profile": '{"engagement": "globex_model_validation_2026q2", "tier": "Tier 1", "client_lead": "Nihar Ranjan Kanungo"}',
        "rag_docs": "VaRCalc v3.2 uses a 250-day historical simulation window with a stress overlay scaled by GARCH volatility forecast. GBP/USD historical-window vol jumped from 8% to 14% over the last 30 days following UK rate uncertainty, which pushes the 99% tail estimate up disproportionately when the book has long GBP swap exposure. Stress overlay adds a deterministic $8M for the gilt-curve scenario currently in the playbook.",
        "embedding_input": "VaR model explanation, FX swaps, GBP/USD volatility, historical simulation, stress overlay, plain English",
        "recommendation": "**Why the VaR model flagged the FX swap book — plain English:**\n\nYesterday's 99% 1-day VaR came in at **$48M** against a **$30M** limit — a $18M overage.\n\n**Two drivers — both real, not a model bug:**\n1. **GBP/USD got more volatile.** Over the last month, GBP/USD daily moves widened from ~8% to ~14% (annualised). The model uses 250 days of history, so the recent shock now contributes more to the tail estimate. **Impact: +$32M.**\n2. **The model's stress overlay added $8M** for the gilt-curve scenario currently in the standing playbook — that's by design, not a surprise.\n\n**What management should ask next:**\n- Is the $32M GBP/USD vol contribution acceptable given the book's directional exposure? If not, the desk should reduce GBP swap notional.\n- Is the gilt-curve stress scenario still the right one in the playbook, or has the rate uncertainty resolved enough to revisit it?\n\n**Confidence:** High — the math is auditable end-to-end via the VaRCalc v3.2 run logs.",
        "model": "gpt-4.5",
        "provider": "azure_openai",
        "property_type": "Tier 1 Model Validation",
        "region": "EMEA",
    },
    {
        "name": "M&A Diligence — Target Filings Review",
        "user_input": "Pull the material risks from the target company's last 3 years of 10-Ks for the M&A diligence brief. Target is a mid-cap industrial.",
        "intent": "ma_diligence",
        "search_query": "ma_target_diligence, doc_types:[10-K], years:[2023, 2024, 2025], target_sector:industrial_midcap",
        "search_results": "10-K 2023 (clean), 10-K 2024 (material weakness disclosed in IT controls), 10-K 2025 (material weakness remediation in progress, two new pending litigations).",
        "loyalty_profile": '{"engagement": "stark_ma_diligence_2026q2", "tier": "Tier 1", "client_lead": "Scott Llewelyn", "qrm_required": true}',
        "rag_docs": "2024 10-K Item 9A: material weakness in IT general controls around access provisioning — remediation plan in progress. 2025 10-K Item 9A: remediation ongoing, retesting deferred to Q1 2026. Two new disclosed litigations: product-liability class action $40M reserve, environmental claim Superfund $12M reserve. 2023 10-K clean — no material weakness, no significant litigation.",
        "embedding_input": "M&A diligence, target 10-K, material risks, IT controls weakness, litigation reserves, environmental claim",
        "recommendation": "**Material Risk Summary — Target Filings (2023–2025):**\n\n**Three items deserve immediate buyer attention:**\n\n1. **IT controls material weakness (open 2 years).** Disclosed in 2024, still in remediation per 2025 10-K. Recommend a forensic IT-controls walkthrough during diligence — material weaknesses that persist often signal deeper governance issues.\n\n2. **Product-liability class action — $40M reserved.** Litigation disclosed in 2025 10-K with $40M reserve. Recommend independent reserve adequacy review by litigation counsel.\n\n3. **Environmental Superfund claim — $12M reserved.** Smaller dollar amount but Superfund liability can expand. Confirm reserve assumptions match EPA's current PRP scope.\n\n**Net read:** This is not a deal-killer profile, but it materially weakens the standard rep-and-warranty package. Recommend a specific R&W carve-out for the IT controls weakness and a separate environmental indemnity with a survival period of 6+ years.\n\n**Confidence:** High — all items disclosed in audited filings.",
        "model": "gpt-4.5",
        "provider": "azure_openai",
        "property_type": "Tier 1 M&A Diligence",
        "region": "Americas",
    },
]

FINANCE_ERROR_SCENARIOS = [
    {
        "user_input": "Generate the full SOX §404 control narrative for all 14 sub-processes at once.",
        "error_msg": "Context window exceeded: 131,072 token limit. Input context (158,402 tokens) includes 14 process narratives. Consider chunked retrieval per sub-process.",
    },
    {
        "user_input": "Compare the credit risk profile of our entire counterparty book against the IFRS 9 staging methodology in real time.",
        "error_msg": "Model inference timeout after 30000ms — Azure OpenAI endpoint eu-west-1 throttled (429 Too Many Requests). Retry budget exhausted.",
    },
    {
        "user_input": "Synthesise the SAR draft including the customer's full bank account number and SSN inline.",
        "error_msg": "Guardrail triggered: response contained PII (account number, SSN) in cleartext. AI gateway PII-redaction policy blocked output. Falling back to redacted template.",
    },
]

FINANCE_INTENT_PROMPT_VERSIONS = [
    {
        "id": "risk-intent-classifier",
        "version": "1.0.0",
        "template": "You are the EY Risk Portfolio LLM agent's intent classifier. Classify the analyst request into one of: {{intents}}. Return JSON with intent and extracted entities.",
        "variables": {"intents": "credit_risk_memo, sar_filing_guidance, audit_finding_synthesis, model_explanation, ma_diligence, regulatory_qa"},
        "weight": 0.3,
    },
    {
        "id": "risk-intent-classifier",
        "version": "2.0.0",
        "template": "You are the EY Risk Portfolio agent's intent classifier v2. Classify the analyst request and extract entities including counterparty, engagement, jurisdiction, document_types, and QRM applicability. Intents: {{intents}}. Return JSON.",
        "variables": {"intents": "credit_risk_memo, sar_filing_guidance, audit_finding_synthesis, model_explanation, ma_diligence, regulatory_qa"},
        "weight": 0.7,
    },
]

FINANCE_RECOMMENDATION_PROMPT_VERSIONS = [
    {
        "id": "risk-recommendation-generator",
        "version": "1.0.0",
        "template": "You are the EY Risk Portfolio LLM agent. Generate an analyst-grade response. Be specific about findings, regulatory citations, and recommended actions. Use markdown formatting. Never include PII in cleartext.",
        "variables": {},
        "weight": 0.4,
    },
    {
        "id": "risk-recommendation-generator",
        "version": "2.1.0",
        "template": "You are the EY Risk Portfolio LLM agent. Generate an analyst-grade response for a {{engagement_tier}} engagement. Prioritise: 1) regulatory accuracy, 2) explicit citations, 3) confidence calibration, 4) PII redaction at the source. Include a Confidence line. Format with markdown headers.",
        "variables": {"engagement_tier": "Tier 1"},
        "weight": 0.6,
    },
]

FINANCE_MODEL_VARIANTS = [
    {
        "model": "gpt-4.1",
        "provider": "azure_openai",
        "temperature": 0.3,
        "max_tokens": 4096,
        "experiment_tag": "model-experiment:gpt41-baseline",
        "weight": 0.45,
    },
    {
        "model": "gpt-4.5",
        "provider": "azure_openai",
        "temperature": 0.25,
        "max_tokens": 4096,
        "experiment_tag": "model-experiment:gpt45-challenger",
        "weight": 0.40,
    },
    {
        "model": "gpt-4o-mini",
        "provider": "azure_openai",
        "temperature": 0.4,
        "max_tokens": 2048,
        "experiment_tag": "model-experiment:gpt4o-mini-cost-opt",
        "weight": 0.15,
    },
]

# The exact eval labels Jaganathan asked for, surfaced as LLM Obs custom
# evaluations alongside the existing hallucination + relevance set.
FINANCE_EVALUATION_DEFINITIONS = [
    {
        "label": "f1_score",
        "metric_type": "score",
        "range": (0.78, 0.94),
        "error_range": (0.45, 0.70),
        "description": "F1 score on the risk-portfolio eval set — Jaganathan's primary metric",
    },
    {
        "label": "precision",
        "metric_type": "score",
        "range": (0.80, 0.95),
        "error_range": (0.50, 0.72),
        "description": "Precision on the risk-portfolio eval set",
    },
    {
        "label": "recall",
        "metric_type": "score",
        "range": (0.76, 0.92),
        "error_range": (0.40, 0.68),
        "description": "Recall on the risk-portfolio eval set",
    },
    {
        "label": "relevance",
        "metric_type": "score",
        "range": (0.84, 0.98),
        "error_range": (0.35, 0.62),
        "description": "Relevance of response to the analyst request",
    },
    {
        "label": "hallucination_score",
        "metric_type": "score",
        "range": (0.0, 0.06),
        "error_range": (0.18, 0.50),
        "description": "Hallucination detection — lower is better",
    },
    {
        "label": "regulatory_citation_accuracy",
        "metric_type": "score",
        "range": (0.85, 0.99),
        "error_range": (0.30, 0.60),
        "description": "Accuracy of regulatory citations (FinCEN/SOX/IFRS sections etc.)",
    },
    {
        "label": "pii_leak_check",
        "metric_type": "categorical",
        "categories": ["pass", "pass", "pass", "pass"],
        "error_categories": ["fail", "fail"],
        "description": "Did the response leak PII in cleartext? Pass = no leak",
    },
]


# ---------------------------------------------------------------------------
# Weighted random selection helper
# ---------------------------------------------------------------------------

def _weighted_choice(items: list) -> dict:
    """Pick an item from a list using 'weight' keys."""
    weights = [item["weight"] for item in items]
    return random.choices(items, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Helper: format messages as JSON for gen_ai.input/output.messages
# ---------------------------------------------------------------------------

def _format_input_messages(messages: list) -> str:
    """Serialize messages to JSON string for gen_ai.input.messages attribute.

    Datadog expects each message to have a 'parts' array with typed content
    objects, matching the OTel GenAI v1.37+ semantic conventions.
    """
    formatted = []
    for msg in messages:
        formatted.append({
            "role": msg.get("role", "user"),
            "parts": [{"type": "text", "content": msg.get("content", "")}],
        })
    return json.dumps(formatted)


def _format_output_messages(content: str, finish_reason: str = "stop") -> str:
    """Serialize assistant response to JSON string for gen_ai.output.messages attribute."""
    return json.dumps([{
        "role": "assistant",
        "parts": [{"type": "text", "content": content}],
        "finish_reason": finish_reason,
    }])


# ---------------------------------------------------------------------------
# OTel GenAI trace generator
# ---------------------------------------------------------------------------


class EvaluationSubmitter:
    """
    Submits custom evaluations to Datadog LLM Observability via HTTP API.

    Endpoint: POST https://api.{site}/api/intake/llm-obs/v2/eval-metric
    For OTel spans, requires source:otel tag and decimal span/trace IDs.
    """

    EVAL_PATH = "/api/intake/llm-obs/v2/eval-metric"

    SITE_API_MAP = {
        "datadoghq.com": "https://api.datadoghq.com",
        "us3.datadoghq.com": "https://api.us3.datadoghq.com",
        "us5.datadoghq.com": "https://api.us5.datadoghq.com",
        "datadoghq.eu": "https://api.datadoghq.eu",
        "ap1.datadoghq.com": "https://api.ap1.datadoghq.com",
        "ddog-gov.com": "https://api.ddog-gov.com",
    }

    def __init__(self):
        self._api_key = os.environ.get("DD_API_KEY", "")
        site = os.environ.get("DD_SITE", "datadoghq.com")
        self._base_url = self.SITE_API_MAP.get(site, f"https://api.{site}")
        self._enabled = bool(self._api_key)
        if not self._enabled:
            logger.warning("EvaluationSubmitter disabled — DD_API_KEY not set")

    def submit(
        self,
        span_id: str,
        trace_id: str,
        label: str,
        metric_type: str,
        value: Any,
        ml_app: str = "ai-stay-planner",
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        """Submit an evaluation asynchronously (fire-and-forget)."""
        if not self._enabled:
            return

        # OTel uses hex IDs — Datadog API requires decimal strings
        try:
            decimal_span_id = str(int(span_id, 16)) if not span_id.isdigit() else span_id
            decimal_trace_id = str(int(trace_id, 16)) if not trace_id.isdigit() else trace_id
        except (ValueError, TypeError):
            decimal_span_id = span_id
            decimal_trace_id = trace_id

        tag_list = ["source:otel"]
        if tags:
            tag_list.extend(f"{k}:{v}" for k, v in tags.items())

        payload = {
            "data": {
                "type": "evaluation_metric",
                "attributes": {
                    "span_id": decimal_span_id,
                    "trace_id": decimal_trace_id,
                    "ml_app": ml_app,
                    "label": label,
                    "metric_type": metric_type,
                    "timestamp_ms": int(time.time() * 1000),
                    "tags": tag_list,
                },
            }
        }

        if metric_type == "categorical":
            payload["data"]["attributes"]["categorical_value"] = value
        else:
            payload["data"]["attributes"]["score_value"] = value

        # Fire-and-forget in background thread
        threading.Thread(
            target=self._post, args=(payload,), daemon=True
        ).start()

    def _post(self, payload: dict) -> None:
        """HTTP POST to evaluations endpoint."""
        url = f"{self._base_url}{self.EVAL_PATH}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "DD-API-KEY": self._api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 300:
                    logger.warning(f"Eval submit failed: HTTP {resp.status}")
        except Exception as exc:
            logger.debug(f"Eval submit error (non-fatal): {exc}")


class LLMObsSubmitter:
    """
    Generates LLM Observability traces via OTel GenAI semantic conventions (v1.37+).

    Features:
      - Trace generation with full prompt/completion content
      - Prompt version tracking (multiple template versions per prompt ID)
      - Model variant A/B experiments (gpt-4-turbo vs gpt-4o vs gpt-4o-mini)
      - Custom evaluation submission via Datadog HTTP API
    """

    def __init__(
        self,
        endpoint: str = "otel-collector:4317",
        insecure: bool = True,
        vertical_name: Optional[str] = None,
    ):
        self._tick_counter = 0
        self._interval = random.randint(3, 5)
        self._error_rate = 0.05

        # ---- Vertical-aware library selection --------------------------
        # Default is a vertical-neutral AI Assistant. `finance` swaps to the
        # EY Risk Portfolio agent and `hospitality` to the AI Stay Planner;
        # every other vertical (healthcare, insurance, ...) gets the generic
        # assistant so traces aren't tied to an industry. Only one
        # LLMObsSubmitter exists per process, so global reassignment is safe.
        global SCENARIOS, ERROR_SCENARIOS, MODEL_VARIANTS
        global EVALUATION_DEFINITIONS, INTENT_PROMPT_VERSIONS
        global RECOMMENDATION_PROMPT_VERSIONS
        if vertical_name == "finance":
            SCENARIOS = FINANCE_SCENARIOS
            ERROR_SCENARIOS = FINANCE_ERROR_SCENARIOS
            MODEL_VARIANTS = FINANCE_MODEL_VARIANTS
            EVALUATION_DEFINITIONS = FINANCE_EVALUATION_DEFINITIONS
            INTENT_PROMPT_VERSIONS = FINANCE_INTENT_PROMPT_VERSIONS
            RECOMMENDATION_PROMPT_VERSIONS = FINANCE_RECOMMENDATION_PROMPT_VERSIONS
            self._service_name = "risk-eval-agent"
            self._display_name = "EY Risk Portfolio"
            self._ml_app = "risk-eval-agent"
            self._scenario_attr_prefix = "ey"
            self._service_host = "risk-eval-agent-01"
            self._service_framework = "langgraph"
        elif vertical_name == "healthcare":
            # AdventHealth AI Care Companion — healthcare LLM Obs traces with
            # clinical-safety + cost evals. Emitted by the simulator (proven
            # OTel GenAI path) under ml_app=ai-care-companion.
            SCENARIOS = HEALTHCARE_SCENARIOS
            ERROR_SCENARIOS = HEALTHCARE_ERROR_SCENARIOS
            EVALUATION_DEFINITIONS = HEALTHCARE_EVALUATION_DEFINITIONS
            INTENT_PROMPT_VERSIONS = HEALTHCARE_INTENT_PROMPT_VERSIONS
            RECOMMENDATION_PROMPT_VERSIONS = HEALTHCARE_RECOMMENDATION_PROMPT_VERSIONS
            # MODEL_VARIANTS is vertical-neutral; reuse default.
            self._service_name = "ai-care-companion"
            self._display_name = "AI Care Companion"
            self._ml_app = "ai-care-companion"
            self._scenario_attr_prefix = "care_companion"
            self._service_host = "ai-care-companion-01"
            self._service_framework = "langchain"
        elif vertical_name == "hospitality":
            # Restore the curated AI Stay Planner library explicitly.
            SCENARIOS = HOSPITALITY_SCENARIOS
            ERROR_SCENARIOS = HOSPITALITY_ERROR_SCENARIOS
            MODEL_VARIANTS = HOSPITALITY_MODEL_VARIANTS
            EVALUATION_DEFINITIONS = HOSPITALITY_EVALUATION_DEFINITIONS
            INTENT_PROMPT_VERSIONS = HOSPITALITY_INTENT_PROMPT_VERSIONS
            RECOMMENDATION_PROMPT_VERSIONS = HOSPITALITY_RECOMMENDATION_PROMPT_VERSIONS
            self._service_name = "ai-stay-planner"
            self._display_name = "AI Stay Planner"
            self._ml_app = "ai-stay-planner"
            self._scenario_attr_prefix = "hospitality"
            self._service_host = "ai-stay-planner-host-01"
            self._service_framework = "langchain"
        else:
            SCENARIOS = GENERIC_SCENARIOS
            ERROR_SCENARIOS = GENERIC_ERROR_SCENARIOS
            EVALUATION_DEFINITIONS = GENERIC_EVALUATION_DEFINITIONS
            INTENT_PROMPT_VERSIONS = GENERIC_INTENT_PROMPT_VERSIONS
            RECOMMENDATION_PROMPT_VERSIONS = GENERIC_RECOMMENDATION_PROMPT_VERSIONS
            # MODEL_VARIANTS is vertical-neutral — reuse the module default.
            self._service_name = "ai-assistant"
            self._display_name = "AI Assistant"
            self._ml_app = "ai-assistant"
            self._scenario_attr_prefix = "assistant"
            self._service_host = "ai-assistant-01"
            self._service_framework = "langchain"

        # Dedicated TracerProvider for the configured LLM-agent service.
        resource = Resource.create({
            ResourceAttributes.SERVICE_NAME: self._service_name,
            ResourceAttributes.SERVICE_VERSION: "1.0.0",
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: "demo",
            "host.name": self._service_host,
            "service.language": "python",
            "service.framework": self._service_framework,
            "demo.display_name": self._display_name,
        })

        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
        self._processor = BatchSpanProcessor(
            exporter,
            max_queue_size=2048,
            max_export_batch_size=256,
            schedule_delay_millis=2000,
        )
        self._provider = TracerProvider(resource=resource)
        self._provider.add_span_processor(self._processor)
        self._tracer = self._provider.get_tracer(
            self._service_name, "1.0.0"
        )

        # Evaluation submitter for custom evals via HTTP API
        self._eval = EvaluationSubmitter()

        logger.info(
            f"LLM Obs: OTel GenAI trace generator initialised "
            f"(endpoint={endpoint}, vertical={vertical_name or 'generic'}, "
            f"service={self._service_name}, semconv=v1.37+, "
            f"evals={'enabled' if self._eval._enabled else 'disabled'})"
        )

    def tick(self) -> None:
        """Called each simulator tick. Generates a trace at the configured interval."""
        self._tick_counter += 1
        if self._tick_counter < self._interval:
            return
        self._tick_counter = 0
        self._interval = random.randint(3, 5)

        if random.random() < self._error_rate:
            self._generate_error_trace()
        else:
            scenario = random.choice(SCENARIOS)
            self._generate_trace(scenario)
            logger.info(f"LLM Obs trace generated: {scenario['name']}")

    def shutdown(self) -> None:
        """Flush and shut down the tracer provider."""
        self._processor.force_flush()
        self._provider.shutdown()

    # ------------------------------------------------------------------
    # Trace generation
    # ------------------------------------------------------------------

    def _generate_trace(self, scenario: Dict[str, Any]) -> None:
        """Generate a full agent trace with nested GenAI spans."""
        session_id = str(uuid.uuid4())

        # Select model variant for this trace (A/B experiment)
        model_variant = _weighted_choice(MODEL_VARIANTS)

        # Root agent span (INTERNAL — orchestrator). Span name + scenario
        # attribute keys are vertical-aware.
        prefix = self._scenario_attr_prefix
        with self._tracer.start_as_current_span(
            self._display_name,
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.system": model_variant["provider"],
                "gen_ai.operation.name": "invoke_agent",
                "session.id": session_id,
                f"{prefix}.scenario": scenario["name"],
                f"{prefix}.intent": scenario["intent"],
                f"{prefix}.topic": scenario["property_type"],
                f"{prefix}.region": scenario["region"],
                "ml_app": self._ml_app,
                model_variant["experiment_tag"].split(":")[0]: model_variant["experiment_tag"].split(":")[1],
            },
        ) as agent_span:
            agent_span.set_attribute(
                "gen_ai.input.messages",
                _format_input_messages([
                    {"role": "user", "content": scenario["user_input"]},
                ]),
            )

            # 1. Intent Classification (LLM call)
            self._intent_classification(scenario, model_variant)
            _sim_delay(0.08, 0.16)

            # 2. Catalog / availability search (tool call)
            self._tool_call(
                name="Catalog Search",
                input_value=scenario["search_query"],
                output_value=scenario["search_results"],
                tool_name="catalog_search_api",
                duration_range=(0.05, 0.12),
            )
            _sim_delay(0.04, 0.08)

            # 3. User profile lookup (tool call)
            self._tool_call(
                name="User Profile Lookup",
                input_value="Fetch user profile, plan, and preferences",
                output_value=scenario["loyalty_profile"],
                tool_name="user_profile_api",
                duration_range=(0.03, 0.08),
            )
            _sim_delay(0.03, 0.06)

            # 4. Query embedding
            self._embedding_call(scenario, model_variant)
            _sim_delay(0.02, 0.05)

            # 5. Knowledge base RAG retrieval
            self._retrieval_call(scenario)
            _sim_delay(0.03, 0.06)

            # 6. Recommendation Generation (LLM call)
            self._recommendation_generation(scenario, model_variant)

            agent_span.set_attribute(
                "gen_ai.output.messages",
                _format_output_messages(scenario["recommendation"]),
            )
            agent_span.set_status(Status(StatusCode.OK))

            # --- Submit custom evaluations for this trace ---
            span_ctx = agent_span.get_span_context()
            span_id_hex = format(span_ctx.span_id, "016x")
            trace_id_hex = format(span_ctx.trace_id, "032x")

            eval_tags = {
                "scenario": scenario["name"].lower().replace(" ", "_"),
                "model": model_variant["model"],
                model_variant["experiment_tag"].split(":")[0]: model_variant["experiment_tag"].split(":")[1],
            }

            for eval_def in EVALUATION_DEFINITIONS:
                if eval_def["metric_type"] == "categorical":
                    value = random.choice(eval_def["categories"])
                else:
                    lo, hi = eval_def["range"]
                    value = round(random.uniform(lo, hi), 4)

                self._eval.submit(
                    span_id=span_id_hex,
                    trace_id=trace_id_hex,
                    label=eval_def["label"],
                    metric_type=eval_def["metric_type"],
                    value=value,
                    ml_app=self._ml_app,
                    tags=eval_tags,
                )

    def _intent_classification(self, scenario: Dict[str, Any], model_variant: Dict[str, Any]) -> None:
        """Generate an LLM span for intent classification with prompt version tracking."""
        input_tokens = random.randint(180, 250)
        output_tokens = random.randint(30, 60)
        intent_result = json.dumps({
            "intent": scenario["intent"],
            "confidence": round(random.uniform(0.92, 0.99), 3),
            "entities": {
                "primary_entity": scenario["search_query"].split(",")[0],
                "topic": scenario["property_type"],
            },
        })

        # Select prompt version for this call
        prompt_version = _weighted_choice(INTENT_PROMPT_VERSIONS)
        system_content = prompt_version["template"].replace(
            "{{intents}}", prompt_version["variables"]["intents"]
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": scenario["user_input"]},
        ]

        # Generate deterministic prompt hash for version tracking
        prompt_hash = hashlib.sha256(
            prompt_version["template"].encode()
        ).hexdigest()[:12]

        with self._tracer.start_as_current_span(
            "Intent Classification",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.system": model_variant["provider"],
                "gen_ai.request.model": model_variant["model"],
                "gen_ai.response.model": model_variant["model"],
                "gen_ai.operation.name": "chat",
                "gen_ai.request.temperature": 0.3,
                "gen_ai.request.max_tokens": 256,
                "gen_ai.usage.input_tokens": input_tokens,
                "gen_ai.usage.output_tokens": output_tokens,
                "gen_ai.usage.total_tokens": input_tokens + output_tokens,
                "gen_ai.input.messages": _format_input_messages(messages),
                # Prompt tracking attributes
                "gen_ai.prompt.id": prompt_version["id"],
                "gen_ai.prompt.version": prompt_version["version"],
                "gen_ai.prompt.hash": prompt_hash,
                "gen_ai.prompt.template": prompt_version["template"],
                "gen_ai.prompt.variables": json.dumps(prompt_version["variables"]),
                # Experiment tag
                model_variant["experiment_tag"].split(":")[0]: model_variant["experiment_tag"].split(":")[1],
            },
        ) as span:
            _sim_work(0.08, 0.16)
            span.set_attribute(
                "gen_ai.output.messages",
                _format_output_messages(intent_result),
            )
            span.set_status(Status(StatusCode.OK))

    def _tool_call(
        self,
        name: str,
        input_value: str,
        output_value: str,
        tool_name: str,
        duration_range: tuple = (0.05, 0.12),
    ) -> None:
        """Generate a tool call span."""
        with self._tracer.start_as_current_span(
            name,
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.operation.name": "tool",
                "tool.name": tool_name,
                "gen_ai.input.messages": _format_input_messages([
                    {"role": "tool", "content": input_value},
                ]),
            },
        ) as span:
            _sim_work(*duration_range)
            span.set_attribute(
                "gen_ai.output.messages",
                _format_output_messages(output_value),
            )
            span.set_status(Status(StatusCode.OK))

    def _embedding_call(self, scenario: Dict[str, Any], model_variant: Dict[str, Any]) -> None:
        """Generate an embedding span with model variant tracking."""
        input_tokens = len(scenario["embedding_input"].split()) * 2

        with self._tracer.start_as_current_span(
            "Query Embedding",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.system": model_variant["provider"],
                "gen_ai.request.model": "text-embedding-3-large",
                "gen_ai.response.model": "text-embedding-3-large",
                "gen_ai.operation.name": "embeddings",
                "gen_ai.usage.input_tokens": input_tokens,
                "gen_ai.usage.output_tokens": 0,
                "gen_ai.usage.total_tokens": input_tokens,
                "embedding.dimensions": 1536,
                "gen_ai.input.messages": _format_input_messages([
                    {"role": "user", "content": scenario["embedding_input"]},
                ]),
                # Experiment tag
                model_variant["experiment_tag"].split(":")[0]: model_variant["experiment_tag"].split(":")[1],
            },
        ) as span:
            _sim_work(0.02, 0.06)
            span.set_attribute(
                "gen_ai.output.messages",
                _format_output_messages(
                    f"[vector dim=1536, norm=0.{random.randint(85, 99)}]"
                ),
            )
            span.set_status(Status(StatusCode.OK))

    def _retrieval_call(self, scenario: Dict[str, Any]) -> None:
        """Generate a RAG retrieval span."""
        with self._tracer.start_as_current_span(
            "Knowledge Base",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "retrieval",
                "retrieval.source": "knowledge-base",
                "retrieval.top_k": 3,
                "gen_ai.input.messages": _format_input_messages([
                    {"role": "user", "content": scenario["embedding_input"]},
                ]),
            },
        ) as span:
            _sim_work(0.05, 0.12)
            span.set_attribute(
                "gen_ai.output.messages",
                _format_output_messages(scenario["rag_docs"]),
            )
            span.set_status(Status(StatusCode.OK))

    def _recommendation_generation(self, scenario: Dict[str, Any], model_variant: Dict[str, Any]) -> None:
        """Generate the main recommendation LLM span with prompt version tracking."""
        input_tokens = random.randint(1200, 1800)
        output_tokens = random.randint(600, 1000)

        # Select prompt version for recommendation
        prompt_version = _weighted_choice(RECOMMENDATION_PROMPT_VERSIONS)
        system_content = prompt_version["template"]
        # Substitute variables if present
        for var_key, var_val in prompt_version["variables"].items():
            system_content = system_content.replace(f"{{{{{var_key}}}}}", var_val)

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": scenario["user_input"]},
            {"role": "assistant", "content": f"[Intent: {scenario['intent']}]\n[Results: {scenario['search_results']}]\n[Profile: {scenario['loyalty_profile']}]\n[Knowledge: {scenario['rag_docs']}]"},
            {"role": "user", "content": "Now generate the personalised recommendation based on all context above."},
        ]

        # Generate deterministic prompt hash for version tracking
        prompt_hash = hashlib.sha256(
            prompt_version["template"].encode()
        ).hexdigest()[:12]

        with self._tracer.start_as_current_span(
            "Recommendation Generation",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.system": model_variant["provider"],
                "gen_ai.request.model": model_variant["model"],
                "gen_ai.response.model": model_variant["model"],
                "gen_ai.operation.name": "chat",
                "gen_ai.request.temperature": model_variant["temperature"],
                "gen_ai.request.max_tokens": model_variant["max_tokens"],
                "gen_ai.usage.input_tokens": input_tokens,
                "gen_ai.usage.output_tokens": output_tokens,
                "gen_ai.usage.total_tokens": input_tokens + output_tokens,
                "gen_ai.input.messages": _format_input_messages(messages),
                # Prompt tracking attributes
                "gen_ai.prompt.id": prompt_version["id"],
                "gen_ai.prompt.version": prompt_version["version"],
                "gen_ai.prompt.hash": prompt_hash,
                "gen_ai.prompt.template": prompt_version["template"],
                "gen_ai.prompt.variables": json.dumps(prompt_version["variables"]),
                # Experiment tag
                model_variant["experiment_tag"].split(":")[0]: model_variant["experiment_tag"].split(":")[1],
            },
        ) as span:
            _sim_work(0.5, 1.2)
            span.set_attribute(
                "gen_ai.output.messages",
                _format_output_messages(scenario["recommendation"]),
            )
            span.set_status(Status(StatusCode.OK))

    # ------------------------------------------------------------------
    # Error trace generation
    # ------------------------------------------------------------------

    def _generate_error_trace(self) -> None:
        """Generate a trace where the recommendation fails, with model variant and error-range evaluations."""
        error = random.choice(ERROR_SCENARIOS)
        session_id = str(uuid.uuid4())

        # Select model variant even for error traces (experiment tracking)
        model_variant = _weighted_choice(MODEL_VARIANTS)

        with self._tracer.start_as_current_span(
            self._display_name,
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.system": model_variant["provider"],
                "gen_ai.operation.name": "agent",
                "session.id": session_id,
                "ml_app": self._ml_app,
                "error": True,
                model_variant["experiment_tag"].split(":")[0]: model_variant["experiment_tag"].split(":")[1],
                "gen_ai.input.messages": _format_input_messages([
                    {"role": "user", "content": error["user_input"]},
                ]),
            },
        ) as agent_span:

            # Intent classification succeeds
            input_tokens = random.randint(150, 200)
            output_tokens = random.randint(30, 50)
            prompt_version = _weighted_choice(INTENT_PROMPT_VERSIONS)
            system_content = prompt_version["template"].replace(
                "{{intents}}", prompt_version["variables"]["intents"]
            )
            intent_messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": error["user_input"]},
            ]
            intent_result = '{"intent": "spontaneous_travel", "confidence": 0.87}'

            with self._tracer.start_as_current_span(
                "Intent Classification",
                kind=SpanKind.CLIENT,
                attributes={
                    "gen_ai.system": model_variant["provider"],
                    "gen_ai.request.model": model_variant["model"],
                    "gen_ai.response.model": model_variant["model"],
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.temperature": 0.3,
                    "gen_ai.usage.input_tokens": input_tokens,
                    "gen_ai.usage.output_tokens": output_tokens,
                    "gen_ai.usage.total_tokens": input_tokens + output_tokens,
                    "gen_ai.input.messages": _format_input_messages(intent_messages),
                    "gen_ai.prompt.id": prompt_version["id"],
                    "gen_ai.prompt.version": prompt_version["version"],
                    model_variant["experiment_tag"].split(":")[0]: model_variant["experiment_tag"].split(":")[1],
                },
            ) as intent_span:
                _sim_work(0.08, 0.16)
                intent_span.set_attribute(
                    "gen_ai.output.messages",
                    _format_output_messages(intent_result),
                )
                intent_span.set_status(Status(StatusCode.OK))

            _sim_delay(0.05, 0.1)

            # Recommendation generation fails
            failed_input_tokens = random.randint(800, 1500)
            fail_messages = [
                {"role": "user", "content": error["user_input"]},
            ]

            with self._tracer.start_as_current_span(
                "Recommendation Generation",
                kind=SpanKind.CLIENT,
                attributes={
                    "gen_ai.system": model_variant["provider"],
                    "gen_ai.request.model": model_variant["model"],
                    "gen_ai.response.model": model_variant["model"],
                    "gen_ai.operation.name": "chat",
                    "gen_ai.usage.input_tokens": failed_input_tokens,
                    "gen_ai.usage.output_tokens": 0,
                    "gen_ai.usage.total_tokens": failed_input_tokens,
                    "error": True,
                    "gen_ai.input.messages": _format_input_messages(fail_messages),
                    model_variant["experiment_tag"].split(":")[0]: model_variant["experiment_tag"].split(":")[1],
                },
            ) as rec_span:
                _sim_work(1.5, 3.0)
                rec_span.set_attribute(
                    "gen_ai.output.messages",
                    _format_output_messages(
                        f"Error: {error['error_msg']}", "error"
                    ),
                )
                rec_span.set_status(
                    Status(StatusCode.ERROR, error["error_msg"])
                )
                rec_span.record_exception(
                    Exception(error["error_msg"])
                )

            # Mark agent as errored
            agent_span.set_status(
                Status(StatusCode.ERROR, error["error_msg"])
            )
            agent_span.set_attribute(
                "gen_ai.output.messages",
                _format_output_messages(
                    f"Error: {error['error_msg']}", "error"
                ),
            )

            # --- Submit error-range evaluations for failed traces ---
            span_ctx = agent_span.get_span_context()
            span_id_hex = format(span_ctx.span_id, "016x")
            trace_id_hex = format(span_ctx.trace_id, "032x")

            eval_tags = {
                "scenario": "error_trace",
                "model": model_variant["model"],
                "error": "true",
                model_variant["experiment_tag"].split(":")[0]: model_variant["experiment_tag"].split(":")[1],
            }

            for eval_def in EVALUATION_DEFINITIONS:
                if eval_def["metric_type"] == "categorical":
                    value = random.choice(eval_def["error_categories"])
                else:
                    lo, hi = eval_def["error_range"]
                    value = round(random.uniform(lo, hi), 4)

                self._eval.submit(
                    span_id=span_id_hex,
                    trace_id=trace_id_hex,
                    label=eval_def["label"],
                    metric_type=eval_def["metric_type"],
                    value=value,
                    ml_app=self._ml_app,
                    tags=eval_tags,
                )

        logger.info(f"LLM Obs error trace generated: {error['error_msg'][:60]}...")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sim_work(min_s: float, max_s: float) -> None:
    """Simulate work by sleeping a small random amount."""
    time.sleep(random.uniform(min_s, max_s))


def _sim_delay(min_s: float, max_s: float) -> None:
    """Simulate gap between operations."""
    time.sleep(random.uniform(min_s, max_s))
