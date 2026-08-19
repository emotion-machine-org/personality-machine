# Local Observability Setup

Distributed tracing for the Emotion Machine backend using Jaeger and OpenTelemetry.

## Quick Start

```bash
# 1. Start Jaeger
cd server
docker compose -f docker-compose.observability.yml up -d

# 2. Install dependencies (if not already done)
uv sync

# 3. Start the backend with tracing enabled
OTEL_TRACING_ENABLED=true uv run uvicorn app.main:app --port 8100

# 4. Open Jaeger UI
open http://localhost:16686
```

## What Gets Traced

With tracing enabled, you'll automatically see spans for:

| Component | What's Traced |
|-----------|---------------|
| **FastAPI** | All HTTP requests with route, method, status, duration |
| **asyncpg** | Database queries with SQL statements |
| **httpx** | External HTTP calls (OpenAI, etc.) |
| **Logging** | Trace IDs injected into log records |

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `OTEL_TRACING_ENABLED` | `false` | Enable/disable tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Jaeger OTLP endpoint |
| `OTEL_SERVICE_NAME` | `emotion-machine-backend` | Service name in traces |

## Using with Load Tests

Run load tests and view traces to identify bottlenecks:

```bash
# Terminal 1: Start Jaeger
docker compose -f docker-compose.observability.yml up -d

# Terminal 2: Start backend with tracing
OTEL_TRACING_ENABLED=true uv run uvicorn app.main:app --port 8100

# Terminal 3: Run load tests
cd loadtests
python run_test.py --quick

# View traces at http://localhost:16686
```

### Analyzing Traces in Jaeger

1. Select **"emotion-machine-backend"** from the Service dropdown
2. Click **"Find Traces"**
3. Click on a trace to see the full breakdown
4. Look for:
   - **Long spans** → Performance bottlenecks
   - **Many small DB spans** → N+1 query problems
   - **External API spans** → Third-party latency


## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   FastAPI App   │────▶│  OTLP Exporter  │────▶│     Jaeger      │
│  (instrumented) │     │   (gRPC:4317)   │     │   (UI:16686)    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

- **FastAPI App**: Auto-instrumented with OpenTelemetry
- **OTLP Exporter**: Sends traces via gRPC to Jaeger
- **Jaeger**: Stores and visualizes traces

## Stopping Jaeger

```bash
docker compose -f docker-compose.observability.yml down
```
