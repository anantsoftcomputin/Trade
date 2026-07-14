# ArthAI

ArthAI is a responsive PWA for risk-first NSE/BSE stock research. The authenticated application is live at https://trade-56777.web.app. Firebase Authentication, Analytics, Firestore persistence, restrictive rules, indexes and Hosting are configured. Model cards, signals, advisor conversations and paper trades are read from owner-scoped Firestore records; fabricated model results are not shown.

### Current deployment boundary

- Live: Hosting, PWA, email/password and Google Authentication, Analytics, Firestore, Cloud Storage, owner-only rules, indexes and paper-trade persistence.
- Live: second-generation `startTraining` callable Function and the Firestore/Eventarc `dispatchTraining` function in `asia-south1`. Artifact images have a seven-day cleanup policy.
- Live: the private `arthai-training` Cloud Run worker in `asia-south1`, using a dedicated least-privilege service account. It accepts daily Yahoo research jobs, validates the data and writes immutable Parquet snapshots to Storage.
- Implemented but awaiting credentials: Upstox V3 historical candles and corporate actions. Recent NSE UDiFF bhavcopy reconciliation and Yahoo research history are active for temporary research runs.
- Implemented: a searchable official NIFTY 50 picker, configurable Indian cash-equity costs, NIFTY benchmark/regime features, TCN–BiGRU–attention training, mutating GA policy search, three expanding purged walk-forward folds, a locked test, model registry, approval gates, daily signals, grounded Gemini tools and paper-trade monitoring.
- Awaiting external inputs: an Upstox read-only Analytics Token, broker-specific cost overrides and future Groww credentials.

The system intentionally fails closed when these inputs are unavailable. It never substitutes scraped or synthetic prices for a real training decision.

## Run it

```bash
npm install
npm run dev
```

Firebase client configuration is stored in the ignored `.env.local`; `.env.example` documents required keys. A production build is created with `npm run build`.

### Netlify

Netlify builds the Vite frontend with `npm run build` and publishes `dist`; SPA redirects and production security headers are defined in `netlify.toml`. Add every `VITE_FIREBASE_*` value from `.env.example` in **Netlify → Site configuration → Environment variables**. Never upload `.env.local` or any server/provider token. After Netlify assigns the production hostname, add that hostname to **Firebase Authentication → Settings → Authorized domains** so Google and email authentication can complete.

Deployment after enabling Blaze and Storage:

```bash
npm run build
npm run build --prefix functions
firebase deploy --only firestore,storage,functions,hosting
gcloud run deploy arthai-training --source training --region asia-south1 --no-allow-unauthenticated
# Add TRAINING_RUNNER_URL=https://YOUR_PRIVATE_RUNNER_URL to functions/.env.trade-56777
firebase deploy --only functions
```

### Market-data worker configuration

Create a read-only Upstox Analytics Token and add it to Google Secret Manager; never place it in React, Firestore, `.env.local`, source control or chat. Deploy the Cloud Run worker with the secret exposed as `UPSTOX_ANALYTICS_TOKEN`. Configure:

```text
MARKET_DATA_BUCKET=trade-56777.firebasestorage.app
ENABLE_NSE_RECONCILIATION=true
NSE_VERIFY_SESSIONS=5
ENABLE_YAHOO_RECONCILIATION=false
ALLOW_YAHOO_RESEARCH_PRIMARY=false
TORCH_NUM_THREADS=2
```

Upstox is the required primary source. NSE public UDiFF bhavcopies verify recent NSE daily closes. Yahoo is an unofficial, research-only comparison source and is disabled by default. A missing secondary source leaves a dataset `research_only_unverified`; a material cross-source mismatch quarantines the run. Neither secondary source silently replaces Upstox history.

While Upstox account activation is pending, daily research runs may explicitly set `ALLOW_YAHOO_RESEARCH_PRIMARY=true`. These snapshots use Yahoo adjusted-close factors, are permanently marked `research_only_*` and set `productionEligible=false`. A quantitatively valid model may be released only to paper research; live advice/order automation remains prohibited. Intraday Yahoo-primary runs remain disabled. Remove this flag as soon as the Upstox Analytics Token is connected.

### Research, signals and advisor

Each training run versions its dataset, NIFTY benchmark, cost schedule, seeds, selected features, GA champion, locked-test metrics and gate results. A registered model has either `paper_approved` or `rejected` release status. Rejected models generate `ABSTAIN`, never a tradable recommendation.

The weekday 18:30 Asia/Kolkata scheduler generates end-of-day signals for paper-approved models and marks planned/open paper trades against the latest daily candle. The advisor runs server-side on Vertex AI Gemini and must use owner-scoped tools for model cards, backtests, signals, signal history and position sizing. Gemini explains structured results but cannot create or modify signal fields.

Default costs are configurable with `COST_BROKERAGE_RATE`, `COST_BROKERAGE_CAP`, `COST_STT_BUY`, `COST_STT_SELL`, `COST_EXCHANGE_RATE`, `COST_SEBI_RATE`, `COST_STAMP_BUY`, `COST_GST_RATE` and `COST_SLIPPAGE_BPS` Cloud Run environment variables.

## Recommended production architecture

Firebase should be the product backend, not the numerical training runtime:

- Firebase Auth: accounts, MFA, device/session management.
- Firestore: workspaces, stock registry, experiments, immutable model cards, signals, portfolios, paper trades, audit trail and advisor conversations.
- Cloud Storage: partitioned Parquet candle/features data, ANN artifacts, GA populations, reports and explainability bundles.
- Cloud Functions: validated API boundary, job creation, notifications, scheduled ingestion triggers and broker webhooks.
- Cloud Run Jobs (Python/PyTorch): feature generation, ANN training, GA search, backtests and walk-forward evaluation. Trigger through Cloud Tasks/Pub/Sub; never keep training inside an HTTP request.
- BigQuery: long-range analytics, fleet comparisons and cost-efficient research queries. Avoid storing millions of candle documents in Firestore.
- Secret Manager: data-provider and future Groww credentials. Broker tokens must never enter the React bundle or Firestore documents readable by clients.

Suggested event flow:

`licensed data provider → raw immutable storage → validation/adjustment job → feature snapshot → GA + ANN candidates → purged walk-forward evaluation → locked unseen test → model gates → paper trading → monitored live release`

## Model design

Use a compact ensemble, not one oversized ANN:

1. A temporal convolutional network for local price/volume patterns.
2. A GRU for longer state and regime memory.
3. Cross-feature attention over stock, NIFTY and sector sequences.
4. Separate heads for direction probability, expected return quantiles and volatility/risk.
5. A calibrated meta-model that can abstain when confidence, liquidity or regime coverage is inadequate.

The genetic algorithm optimizes a multi-objective fitness function across feature subsets, lookback windows, architecture width/dropout, entry thresholds and stop/target policy. It penalizes drawdown, insufficient trades, turnover, instability between folds, model complexity and stressed slippage. Position sizing remains a separate user risk constraint and is not optimized on historical returns. The locked test period never influences GA selection.

Validation uses three purged expanding walk-forward folds and reports net return after current Upstox delivery brokerage/DP/statutory costs, maximum drawdown, profit factor, payoff ratio, profitable months, regime performance, trade count, stressed slippage, worst loss streak, stability across folds and excess performance over buy-and-hold. The locked backtest and BUY generator use the same probability-plus-positive-expected-return entry rule. SELL is an exit/avoid advisory with zero quantity; multi-day naked cash-equity shorts are not simulated. Classification accuracy is not a release gate.

## Data and safety boundaries

- Use a licensed or explicitly permitted NSE/BSE data source. Do not base a commercial product on scraping exchange pages.
- NSE's official production feeds are subscription products, not a public browser API. Use Capital Market EOD data for daily training and add Level 1 or 1-minute snapshot data only for intraday/live features.
- Official real-time delivery is multicast over a dedicated line, an authorized vendor, or interval snapshot files over SFTP. Official EOD/historical delivery is through SFTP, the NDAL downloader, or NDAL cloud delivery.
- NDAL cloud delivery currently targets a customer AWS S3 bucket in Mumbai. Import those immutable files into Firebase Storage after checksum, schema and entitlement validation; do not expose provider credentials to the PWA.
- Keep raw provider files immutable and record source, entitlement, exchange timestamp, ingestion timestamp, checksum and schema version. Derived adjusted candles belong in a separate versioned prefix.
- Adjust history point-in-time for splits, bonuses, dividends, symbol changes, delistings and survivorship bias.
- Version every candle snapshot, feature definition, cost schedule, seed and source-code hash.
- News and corporate events must be timestamped by when information became available, not the event date.
- Start with advisory research and paper trading. Live automation needs explicit user confirmation, broker-side risk limits, idempotent orders, kill switches, reconciliation and a complete audit log.
- The UI must distinguish delayed, end-of-day and live data and show the exact data timestamp on every answer.

## Firestore shape

```text
users/{uid}
trainingJobs/{jobId}
models/{modelId}
signals/{signalId}
paperTrades/{tradeId}
conversations/{conversationId}
auditEvents/{eventId}
rateLimits/{uid_action}
```

Use security rules that authorize through workspace membership and immutable server-created fields for metrics, artifacts, signal evidence and audits.
