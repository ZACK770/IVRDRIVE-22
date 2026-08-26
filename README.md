# Mortgage refinance IVR

Standalone Hebrew IVR service for estimating refinance savings and capturing
interested callers.

## Calculation

The estimate scales linearly from the supplied baseline:

`100,000 / (400,000 x 20 years) = 0.0125 savings per currency unit-year`

The IVR asks for the mortgage amount, how many years ago it was taken, and its
original term. It derives:

`remaining_years = original_term_years - years_since_origination`

For example, 1,500,000 taken 5 years ago for 20 years leaves 15 years and
produces an estimated opportunity of 281,250.

## Endpoints

- `POST /voice/mortgage` - start the IVR flow from a telephony provider.
- `GET /health` - health check.
- `GET /leads` - JSON lead list containing every exact input and derived value
  for CRM/import integration and manual viewing.

Configure the telephony provider's voice webhook to the deployed
`/voice/mortgage` URL. Leads are stored in SQLite at `data/leads.db` by
default; set `LEADS_DB_PATH` to use a persistent mounted volume.
