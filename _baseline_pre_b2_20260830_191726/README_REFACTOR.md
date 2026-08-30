# ApexMacro Multipage Refactor

Entry point: `app.py`

Real routes:
- `/`
- `/login`
- `/vip`
- `/dashboard`
- `/forex`
- `/gold`
- `/oil`
- `/nasdaq`
- `/forecaster`
- `/admin`

## Compatibility-first architecture

`apex/production_core.py` preserves the authoritative production calculation,
payment, Telegram, alert, persistence and UI implementations in one compatibility
module. Responsibility-specific modules expose clean import boundaries without
duplicating those implementations.

This intentionally favors behavior preservation over a risky algorithm rewrite.

Existing JSON/state files must remain at the repository root. The refactor uses
a stable `PROJECT_ROOT` so moving Python code into `apex/` does not move data files.
