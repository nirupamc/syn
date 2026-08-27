"""Application service layer.

Service modules implement domain/business logic and orchestration. In M0 there
is intentionally little here; health reporting is provided directly by the API
layer using configuration. Later milestones (admission, scheduling, usage,
routing) will place their logic in this package so routers/steps stay thin.
"""