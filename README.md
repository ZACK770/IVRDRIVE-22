# Mortgage refinance IVR

Standalone Hebrew IVR service for estimating refinance savings and capturing
interested callers.

## Calculation

The estimate scales linearly from the supplied baseline:

`₪150,000 / (₪400,000 × 20 years) = 0.01875 savings per ₪ per year`

For example, ₪1,500,000 over 20 remaining years produces an estimated
opportunity of ₪562,500.

## Endpoints

- `POST /voice/mortgage` — start the IVR flow from a telephony provider.
- `GET /health` — health check.
- `GET /leads` — JSON lead list for a CRM/import job.

Configure the telephony provider's voice webhook to the deployed
`/voice/mortgage` URL. Leads are stored in SQLite at `data/leads.db` by
default; set `LEADS_DB_PATH` to use a persistent mounted volume.
