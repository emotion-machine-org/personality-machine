# Emotion Machine Load Tests

Load testing suite for the Emotion Machine API using [Locust](https://locust.io/).

## Setup

```bash
cd server
uv sync   # locust is already a dev dependency

# Or if using pip
pip install locust
```

## Configuration

Set environment variables before running tests:

```bash
export EM_API_KEY="emk_live_your_key_here"
export EM_TEST_COMPANION_ID="your-test-companion-uuid"
export EM_BASE_URL="http://localhost:8100"  # optional, defaults to http://localhost:8100/api/

# Or copy .env.example to .env in this directory — config.py loads it automatically.
# Also supported: EM_TEST_USER_PREFIX, EM_RATE_LIMIT
```

## Running Tests

### Quick Start

```bash
cd server/loadtests

# Run with web UI (default) - opens http://localhost:8089
uv run python run_test.py

# Quick smoke test (headless, 10 users, 1 minute)
uv run python run_test.py --quick

# Run headless with defaults (100 users, 5 minutes)
uv run python run_test.py --headless
```

### Custom Parameters

```bash
# Heavy load test (headless)
uv run python run_test.py --headless --users 500 --spawn-rate 50 --duration 15m

# Test specific scenarios only (works in both UI and headless)
uv run python run_test.py --tags messages,critical

# Target staging environment
uv run python run_test.py --host https://staging-api.emotionmachine.ai
```

### Baseline Comparisons

```bash
# Save current run as baseline (headless)
uv run python run_test.py --headless --baseline

# Compare future runs against baseline
uv run python run_test.py --headless --compare results/baseline.json
```

## Test Scenarios

| User Class | Weight | Tags | Description |
|------------|--------|------|-------------|
| `MessageUser` | 10 | `messages`, `critical` | Send messages (REST & SSE) |
| `RelationshipUser` | 5 | `relationships`, `high` | Create/get relationships |
| `ProfileUser` | 3 | `profile`, `high` | Read/update profiles |
| `InboxUser` | 3 | `inbox`, `high` | Poll for proactive messages |
| `SessionUser` | 2 | `sessions`, `medium` | Session lifecycle |
| `KnowledgeUser` | 2 | `knowledge`, `medium` | Knowledge base search |
| `CompanionUser` | 1 | `companions`, `low` | List/get companions |
| `ConfigUser` | 1 | `config`, `low` | Configuration retrieval |

Weights determine the proportion of each user type spawned.

## Directory Structure

```
loadtests/
├── config.py           # Configuration management
├── locustfile.py       # Main test scenarios
├── run_test.py         # Test runner with history tracking
├── results/            # CSV stats and JSON summaries
│   ├── summary_*.json  # Summaries for comparison
│   ├── baseline.json   # Baseline for comparison
│   └── loadtest_*.csv  # Detailed Locust CSV output
├── reports/            # HTML reports
│   └── report_*.html   # Visual reports
└── scenarios/          # (reserved for future modular scenarios)
```

## Results History

Results are stored in `results/` with timestamps for historical tracking:

- `summary_YYYYMMDD_HHMMSS.json` - JSON summary with key metrics
- `loadtest_YYYYMMDD_HHMMSS_stats.csv` - Detailed per-endpoint stats
- `loadtest_YYYYMMDD_HHMMSS_stats_history.csv` - Time-series data
- `loadtest_YYYYMMDD_HHMMSS_failures.csv` - Failure details

### Comparing Results Over Time

```bash
# Compare latest run against baseline
uv run python run_test.py --compare results/baseline.json

# Manual comparison
python -c "
from run_test import compare_results
compare_results('results/summary_20241215_143022.json', 'results/baseline.json')
"
```

## Key Metrics to Monitor

| Metric |
|--------|
| P95 Response Time 
| Failure Rate |
| Requests/sec |

## Tips

1. **Start small**: Begin with 10-50 users to establish baseline
2. **Ramp gradually**: Use spawn rate to avoid overwhelming the system
3. **Monitor server**: Watch server logs and metrics during tests
4. **Test in isolation**: Run against staging, not production
5. **Commit results**: Keep JSON summaries in git for tracking changes

## Troubleshooting

### "Configuration errors" on start

Ensure all required environment variables are set:

```bash
# Check current values
echo $EM_API_KEY
echo $EM_TEST_COMPANION_ID
```

### High failure rate on messages

- Check if companion exists and has valid configuration
- Verify API key has correct permissions
- LLM responses may timeout under heavy load (increase timeout)

### "Turn in progress" (409) errors

This is expected behavior - the API serializes turns per relationship. Under heavy load, you'll see these when multiple requests hit the same relationship.
