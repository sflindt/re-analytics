# re-analytics

Real estate investment property finder. Search for multi-family and single-family listings across multiple sources.

## Setup

```bash
pip install -e ".[dev]"
playwright install chromium
```

Optional: Copy `.env.example` to `.env` and add your UtahRealEstate.com RESO API token.

## Usage

```bash
# Interactive mode — prompts for city, price range, property type
re-analytics

# Command-line mode
re-analytics --city Ogden --state UT --min-price 200000 --max-price 500000 --type multi-family

# Export to CSV
re-analytics --city Ogden -o listings.csv

# Use a specific source
re-analytics --city Ogden --source zillow
```

## Data Sources

| Source | Coverage | Data Quality | Auth Required |
|--------|----------|-------------|---------------|
| UtahRealEstate.com (browser) | Utah | Good — price, sqft, beds/baths, units | No |
| UtahRealEstate.com (API) | Utah | Best — includes rent/NOI data | Yes (RESO API token) |
| Zillow (browser) | Nationwide | Good — price, sqft, beds/baths | No |

For Utah searches, the tool automatically uses UtahRealEstate.com. For other states, it uses Zillow.

## Tests

```bash
pytest
```
