# Getting help with dd-demo-toolkit

<!-- TODO(owner): replace the placeholders below with the real Slack channel,
     on-call rotation name, and named maintainers before broad SE-org rollout.
     The placeholders are intentional and the toolkit ships with this file so
     the format is settled even if the contacts aren't filled in yet. -->

## Quick decision tree

| Situation | Where to go |
|---|---|
| Bug you can reproduce (deploy fails, simulator crashes, dashboard wrong) | Open an issue: [.github/ISSUE_TEMPLATE/bug.yml](.github/ISSUE_TEMPLATE/bug.yml) |
| Feature request, new vertical, new overlay | [.github/ISSUE_TEMPLATE/feature.yml](.github/ISSUE_TEMPLATE/feature.yml) |
| "How do I..." question that isn't covered in the README | `#TODO-dd-demo-toolkit` on Slack |
| Demo is broken right now and a customer is waiting | `#TODO-dd-demo-toolkit-oncall` on Slack — page the on-call SE engineer |
| Security concern (secret leak, RCE, etc.) | Email `TODO-security-contact@datadoghq.com` — do NOT file a public issue |

## Owners

| Area | Owner | Backup |
|---|---|---|
| Core simulator + CLI | TODO | TODO |
| Web UI (`dd_demo_toolkit_ui/`) | TODO | TODO |
| `data_obs/` stack (Kafka, dbt, LLM experiments) | TODO | TODO |
| Verticals (healthcare, finance, hospitality, insurance, manufacturing) | TODO | TODO |

## Response SLAs

| Severity | First response | Resolution target |
|---|---|---|
| P0 (demo broken, customer waiting) | 30 min | same day |
| P1 (regression on `main`) | 1 business day | 1 week |
| P2 (cosmetic / dev-experience) | 1 week | next minor release |
| Feature request | 1 week (acknowledgement) | rolling backlog |

## Release cadence

<!-- TODO(owner): document the release process here once we tag v0.1.0
     (see CODE_REVIEW_2026-05-21.md §Phase 4). -->

TBD — see project plan in `CODE_REVIEW_2026-05-21.md`.
