# TECHNOLOGY SECTOR EQUITY ANALYST SKILL FILE
### Reference Document for AI-Assisted 10-K and Financial Filing Analysis
*Inject into system prompt before analyzing any Technology company filing.*

---

## OVERVIEW & ANALYST MINDSET

When analyzing a Technology company, prioritize **unit economics and scalability** over near-term profitability. Technology businesses are valued primarily on future cash flow trajectories, competitive durability, and the defensibility of their growth. Ask three orienting questions before any analysis:

1. **Is this a software, hardware, semiconductor, or services business?** The financial model differs dramatically by subsector.
2. **Where is the company in its lifecycle?** Hypergrowth companies burn cash intentionally; mature platforms generate and return it.
3. **What is the nature of its revenue?** Recurring vs. transactional, subscription vs. license, consumption-based vs. seat-based — these distinctions determine revenue quality and predictability.

---

## SECTION 1: THE 8 MOST IMPORTANT FINANCIAL METRICS

### 1. Revenue Growth Rate (YoY and QoQ)
**Formula:** `(Current Period Revenue − Prior Period Revenue) / Prior Period Revenue`

**How to interpret:**
- For hypergrowth SaaS (ARR < $1B): expect 40–80%+ YoY; deceleration below 30% triggers valuation re-rating.
- For large-cap software ($10B+ ARR): 10–20% is healthy; below 8% suggests market saturation or competitive pressure.
- Watch for **organic vs. inorganic growth** — acquisitions inflate topline without proving product-market fit. Isolate organic growth from MD&A.
- Decelerating growth on a larger base is expected (law of large numbers), but the *rate* of deceleration matters. A sharp step-down in a single quarter warrants investigation of churn, pricing, or macro headwinds.

---

### 2. Gross Margin (GAAP and Non-GAAP)
**Formula:** `(Revenue − Cost of Revenue) / Revenue`

**How to interpret:**
- **Software/SaaS:** 65–80%+ is standard; below 60% suggests heavy infrastructure costs, services drag, or pricing pressure.
- **Semiconductors:** 50–65% is typical; fabless companies (NVIDIA, Qualcomm) trend higher than IDMs (Intel).
- **Hardware/Devices:** 30–45%; Apple's ~43% is exceptional for hardware.
- Rising gross margins signal **operating leverage kicking in** — fixed cost of revenue spread across growing revenue. Falling gross margins in a growing company signal COGS scaling faster than revenue, a red flag for the unit economics thesis.
- Always reconcile GAAP vs. non-GAAP gross margin. Stock-based compensation (SBC) and amortization of acquired intangibles are the most common exclusions. Large gaps (>5 pts) deserve scrutiny.

---

### 3. Annual Recurring Revenue (ARR) and Net Revenue Retention (NRR)
**ARR Formula:** `MRR × 12` or as disclosed by the company

**NRR Formula:** `(Beginning ARR + Expansion − Contraction − Churn) / Beginning ARR`

**How to interpret:**
- ARR is the **North Star metric** for subscription software businesses. Prioritize ARR trajectory over GAAP revenue when evaluating SaaS companies.
- **NRR > 120%:** Elite. Company grows revenue from existing customers alone (Snowflake, Datadog at peak). Product has deep workflow integration.
- **NRR 110–120%:** Healthy. Strong upsell motion with manageable churn.
- **NRR 100–110%:** Adequate but thin. Net new ARR carries the growth burden.
- **NRR < 100%:** Contraction. Existing customers are churning or downgrading faster than expansion. Requires urgent investigation.
- Note: many companies disclose NRR only annually or stop disclosing it when it deteriorates — absence of disclosure is itself a signal.

---

### 4. Rule of 40
**Formula:** `YoY Revenue Growth Rate (%) + Free Cash Flow Margin (%)`

**How to interpret:**
- A score ≥ 40 indicates a well-balanced SaaS business balancing growth and profitability.
- **Score > 60:** Best-in-class (e.g., Palantir, Veeva at peak). Command premium multiples.
- **Score 40–60:** Healthy; investable at reasonable multiples.
- **Score 20–40:** Watch zone. Acceptable in early stages but must trend toward 40.
- **Score < 20:** Value destructive unless the company has a credible path to efficiency.
- Do not apply Rule of 40 to hardware, semiconductor, or marketplace businesses — the metric is calibrated for subscription software.

---

### 5. Free Cash Flow (FCF) Margin
**Formula:** `(Operating Cash Flow − Capital Expenditures) / Revenue`

**How to interpret:**
- FCF margin is the **ultimate profitability test** for technology companies because it strips out non-cash accounting distortions.
- Mature software companies (Microsoft, Oracle) should produce 25–40% FCF margins. This is what justifies premium P/E multiples.
- For growth-stage companies, watch the **FCF margin trajectory**, not the absolute level. Improving FCF margin from -20% to -5% over two years is more important than the current figure.
- Red flag: **widening FCF losses alongside slowing revenue growth.** This breaks the "invest now, harvest later" thesis.
- Adjust for one-time tax payments, working capital swings, and lumpy capex (data center builds) to assess normalized FCF.

---

### 6. Sales Efficiency / Magic Number
**Formula:** `(Net New ARR in Quarter × 4) / Prior Quarter S&M Expense`

**How to interpret:**
- **> 1.0:** Highly efficient; company generates $1+ of ARR for every $1 spent on sales and marketing.
- **0.5–1.0:** Reasonable; invest selectively.
- **< 0.5:** Inefficient go-to-market; revisit before scaling spend.
- Declining magic number in a growth company signals **market saturation or increasing CAC** (Customer Acquisition Cost) — a leading indicator of growth deceleration.
- Compare to CAC Payback Period: `S&M Expense / (New ARR × Gross Margin %)`. Best-in-class: < 12 months. Concerning: > 24 months.

---

### 7. R&D as a Percentage of Revenue
**Formula:** `R&D Expense / Revenue`

**How to interpret:**
- **Platform software companies:** 15–25% of revenue is typical and necessary to maintain product leadership.
- **Semiconductors:** 20–30% is common; this is the primary moat investment.
- **Mature enterprise software:** 10–18%; lower R&D intensity is acceptable if the product is entrenched and switching costs are high.
- Rising R&D% with flat revenue growth is not inherently bearish — it may signal intentional investment ahead of a product cycle. Context matters.
- Falling R&D% in a competitive market is a yellow flag: the company may be harvesting its installed base without reinvesting in the product.
- Always check whether R&D includes capitalized software development costs (which flow to the balance sheet rather than the income statement), which flatters reported R&D ratios.

---

### 8. Stock-Based Compensation (SBC) as a Percentage of Revenue
**Formula:** `SBC Expense / Revenue`

**How to interpret:**
- SBC is a real economic cost to shareholders (dilution) even though it is non-cash. Analysts who add it back without scrutiny are overstating true profitability.
- **< 5% of revenue:** Well-managed, especially for mature companies.
- **5–12%:** Typical for growth-stage software; acceptable if NRR and growth justify it.
- **> 15%:** Concerning. Scrutinize diluted share count growth. If fully diluted shares outstanding grow > 3–4% per year, buybacks are not keeping pace with dilution.
- Compare SBC to FCF: if SBC is 80%+ of reported FCF, "FCF profitability" is largely an accounting artifact.

---

## SECTION 2: LANGUAGE PATTERNS IN TECHNOLOGY 10-K FILINGS

### Signals of Strength — Read These as Positive Indicators

| Language Pattern | What It Signals |
|---|---|
| *"Platform approach"* or *"unified platform"* | Cross-sell potential; higher switching costs; land-and-expand motion |
| *"Customer cohort analysis"* or *"cohort revenue retention"* | Management is tracking unit economics rigorously; confident in NRR |
| *"Consumption-based revenue"* | Revenue scales directly with customer usage/growth — high-quality ARR tied to customer success |
| *"We have not experienced material customer concentration"* | Revenue is diversified; no single customer >10% is a resilience signal |
| *"Increased operating leverage"* | Gross margins expanding while opex grows slower than revenue — the scaling thesis is working |
| *"Significant portion of revenue is recurring"* | Predictable revenue; lower risk of sharp revenue decline |
| *"TAM of $X billion"* supported by third-party data | Management has a credible, sized market opportunity; not just aspirational |

### Signals of Caution — Read These as Yellow or Red Flags

| Language Pattern | What It Signals |
|---|---|
| *"We may never achieve or maintain profitability"* | Standard boilerplate, but pay attention to how long this has appeared in successive filings |
| *"Significant customer concentration"* or *"Customer A represented X% of revenue"* | Revenue cliff risk; renewal/churn of one customer is material |
| *"We have experienced and may continue to experience negative cash flows"* | Burn rate sustainability — check runway and next financing need |
| *"Increased competition from well-capitalized competitors"* | Margin and growth pressure ahead; evaluate whether competitive moat is narrowing |
| *"Our pricing may not reflect our costs"* | Pricing power is limited; gross margin expansion is harder |
| *"We rely on third-party cloud infrastructure"* | AWS/GCP/Azure dependency — margin and operational risk |
| *"Cybersecurity incident"* or *"unauthorized access"* in Risk Factors | Active threat surface; evaluate whether disclosure is routine or incident-specific |
| *"We changed our revenue recognition methodology"* | Scrutinize the change for whether it accelerates or smooths revenue recognition artificially |
| *"We did not meet previously disclosed guidance"* | Trust in management's forward visibility is impaired |

### Neutral Patterns That Require Context

- **"We are investing in headcount"** — bullish if revenue acceleration follows; bearish if FCF margin widens negative without growth improvement.
- **"We completed the acquisition of X"** — neutral until integration risk and purchase price are assessed. Check goodwill and intangible amortization added to COGS.
- **"We repurchased X shares"** — positive signal for capital discipline in mature companies; potentially a distraction from R&D investment in early-stage companies.

---

## SECTION 3: INTERPRETING MARGIN TRENDS VS. PEERS

### Framework: The Four Margin Questions

**1. What is the gross margin level vs. subsector peers?**

Contextualize gross margin within the correct peer group — cross-subsector comparisons are misleading.

| Subsector | Typical Gross Margin Range |
|---|---|
| Pure SaaS (subscription) | 70–82% |
| Cloud infrastructure (IaaS/PaaS) | 55–70% |
| Enterprise software (license + maintenance) | 68–78% |
| Fabless semiconductors | 50–65% |
| Integrated device manufacturers (IDMs) | 40–55% |
| IT services / consulting | 25–40% |
| Consumer electronics (hardware) | 30–45% |

**2. Is the gross margin expanding or contracting — and why?**

- **Expanding gross margin** in software: favorable. SaaS businesses have high fixed COGS (hosting, customer support infrastructure); as revenue scales, unit COGS declines. This is the operating leverage mechanism.
- **Contracting gross margin** in a growing company: investigate whether it is (a) strategic — investing in customer success to reduce churn; (b) competitive — pricing pressure from rivals; or (c) structural — services revenue (lower-margin) growing faster than subscription revenue.
- **Margin compression from hyperscaler dependency:** If COGS contains significant third-party cloud hosting costs, watch for margin improvement as companies negotiate volume discounts or build proprietary infrastructure.

**3. How do operating margins compare, and what explains the gap?**

Operating margin = Gross margin − Operating expenses (R&D + S&M + G&A). A company can have peer-level gross margins but inferior operating margins due to:
- Excessive S&M spend (inefficient go-to-market or immature market)
- High G&A (fragmented back-office post-acquisition, or IPO-related public company costs not yet optimized)
- Elevated R&D (intentional product investment — evaluate whether this is offensive or defensive spending)

**4. What is the margin expansion trajectory vs. the company's own history?**

Peer comparison matters, but self-comparison matters more for trending. A company at 55% gross margin that has expanded 300 bps per year for three years is a better investment than one at 72% that has been flat. Always build a 5-year margin history table when analyzing a filing.

### Peer Benchmarking Protocol

1. Identify 5–7 direct peers (same subsector, similar ARR or revenue scale, similar go-to-market).
2. Standardize margins on a non-GAAP basis (add back SBC, D&A of intangibles, one-time charges) for cross-company comparability.
3. Plot gross margin, operating margin, and FCF margin on a 3-year trend. Note inflection points.
4. Identify the **margin gap** vs. the best-in-class peer and articulate the structural vs. temporary reasons for the gap.
5. If the company has a higher gross margin but lower operating margin than peers, S&M and R&D efficiency is the investment thesis lever.

---

## SECTION 4: TOP 5 RISK CATEGORIES UNIQUE TO TECHNOLOGY COMPANIES

### Risk 1: Technological Obsolescence
**Nature:** Technology companies face the risk that their core product or platform is displaced by a paradigm shift — not by a competitor playing the same game, but by a new game entirely.

**Indicators to monitor:**
- Age of core technology stack (legacy monolithic vs. cloud-native architecture)
- R&D investment in next-generation capabilities (AI/ML, edge computing, quantum)
- Customer cohort migration rates to new product versions
- Language in 10-K Risk Factors: *"disruptive technologies"*, *"our products may become obsolete"*, *"rapid technological change"*

**Analyst action:** Assess whether the company's R&D spend is defensive (maintaining parity) or offensive (creating new category leadership). Defensive R&D in a paradigm-shift environment is insufficient.

---

### Risk 2: Cybersecurity and Data Privacy
**Nature:** Technology companies hold concentrated repositories of customer data and operate critical digital infrastructure, making them high-value targets. A material breach can cause customer churn, regulatory fines, litigation, and reputational damage.

**Indicators to monitor:**
- Existence of a Chief Information Security Officer (CISO) and board-level cybersecurity oversight
- Disclosure of prior incidents in the 10-K vs. only in footnotes
- GDPR, CCPA, and emerging state-level privacy law exposure
- Third-party audit certifications (SOC 2 Type II, ISO 27001)
- Revenue concentration in regulated industries (healthcare, financial services) that impose stricter standards

**Regulatory dimension:** FTC enforcement, SEC cybersecurity disclosure rules (effective 2023), and EU AI Act compliance are increasing the legal surface area of this risk.

---

### Risk 3: Platform/Ecosystem Dependency
**Nature:** Many technology companies are built on, or distributed through, third-party platforms (Apple App Store, Google Play, AWS Marketplace, Salesforce AppExchange). Dependency creates margin risk (platform fees), go-to-market risk (algorithm/policy changes), and existential risk (de-platforming).

**Indicators to monitor:**
- Revenue share or royalty payments to platform owners in COGS
- Concentration of customer acquisition through a single channel
- History of adversarial platform policy changes (Apple's App Tracking Transparency devastated mobile ad businesses overnight)
- Whether the company has a direct distribution alternative

**Analyst action:** Quantify the margin impact of platform fees and model scenarios where fees increase by 200–300 bps.

---

### Risk 4: Talent Concentration and Attrition
**Nature:** Technology companies are human capital businesses. The top 10% of engineers often produce disproportionate output. Attrition of key technical talent — especially in AI/ML, security, and core platform engineering — can impair product roadmaps and competitive position faster than in any other sector.

**Indicators to monitor:**
- SBC as a retention mechanism — are grants competitive with peer companies?
- Glassdoor ratings, employee review trends, and CEO approval ratings (qualitative leading indicators)
- Headcount growth vs. revenue growth — if headcount grows faster persistently, efficiency is declining
- 10-K disclosure of key person risk in founders, CTO, or chief scientist roles
- Non-compete and IP assignment agreements (relevant in states where enforceable)

---

### Risk 5: Regulatory and Antitrust Exposure
**Nature:** Large technology platforms face increasing global regulatory scrutiny around antitrust (self-preferencing, acquisitions), data sovereignty, content moderation, AI liability, and cross-border data transfer restrictions.

**Indicators to monitor:**
- Active DOJ, EU DG COMP, or FTC investigations disclosed in Legal Proceedings
- Pending or completed acquisitions that may attract regulatory challenge
- Revenue concentration in markets with restrictive data localization laws (EU, China, India)
- AI-specific regulatory risk: EU AI Act high-risk classification, liability exposure for AI-generated outputs
- History of consent decrees or settlement agreements with regulators

**Analyst action:** Model regulatory scenarios — a forced divestiture, a fine up to 10% of global revenue (EU standard), or a mandated interoperability requirement. Assess which scenario is most likely and most material.

---

## SECTION 5: ASSESSING COMPETITIVE MOATS IN TECHNOLOGY

### The Five Moat Archetypes in Technology

**Moat 1: Network Effects**
*Definition:* The product becomes more valuable as more users or participants join.

- **Direct network effects:** Social networks, messaging platforms (WhatsApp, LinkedIn). Value increases with every additional user on the same side.
- **Indirect/cross-side network effects:** Marketplace platforms (Uber, Airbnb, App Store). More drivers attract riders; more riders attract drivers.
- **Data network effects:** AI/ML platforms improve as more data is generated (Google Search, Spotify recommendations). This is the emerging moat for the AI era.

*Assessment questions:* Is the network effect local or global? Can it be replicated with capital (i.e., a competitor subsidizes users)? Has the network effect peaked (MySpace effect)?

---

**Moat 2: Switching Costs**
*Definition:* The cost (time, money, risk, workflow disruption) of replacing the product with a competitor's exceeds the perceived benefit.

- **High switching costs:** ERP systems (SAP, Oracle), core banking software, CRM (Salesforce), developer toolchains
- **Moderate switching costs:** Email/productivity suites, cloud infrastructure
- **Low switching costs:** Point solutions with easy data export, API-first tools with no workflow lock-in

*Assessment questions:* What is the average contract length? Is the product embedded in mission-critical workflows? Does the company's NRR reflect switching cost strength (NRR > 110% strongly correlates with high switching costs)?

---

**Moat 3: Intellectual Property (Patents, Trade Secrets, Proprietary Algorithms)**
*Definition:* Legal or technical barriers that prevent replication of core technology.

- Strongest in semiconductors (TSMC's process technology), specialized defense tech, and biotech-adjacent software
- Weakest in pure software, where functionality can often be re-implemented without infringing patents
- Trade secrets (proprietary training data, model weights, algorithms) are increasingly important for AI companies

*Assessment questions:* How broad is the patent portfolio and what is its remaining life? Is the core IP protectable as a trade secret if patents expire? Has the company successfully enforced IP in litigation?

---

**Moat 4: Scale and Cost Advantages**
*Definition:* The company's size enables unit economics that smaller competitors cannot replicate.

- **Cloud infrastructure:** AWS, Azure, and Google Cloud have capital and engineering scale that prevents new entrants from competing on cost
- **Semiconductor manufacturing:** TSMC's scale in leading-edge process nodes (3nm, 2nm) is a 5–10 year lead that cannot be closed quickly
- **Distribution scale:** Microsoft's enterprise sales force and Azure credits create bundling leverage that pure-play SaaS companies cannot match

*Assessment questions:* What is the minimum efficient scale for this market? Is the company's cost advantage growing or shrinking as competitors invest? Can a hyperscaler replicate this at marginal cost?

---

**Moat 5: Proprietary Data Assets**
*Definition:* Unique, non-replicable datasets that train better models, improve product performance, or enable better decisions than competitors can achieve.

- Increasingly the **dominant moat in the AI era**
- Examples: Google's search query data, Bloomberg's financial data terminal, Epic's clinical health records, Palantir's government data integration
- Proprietary data moats compound: more users generate more data, which trains better models, which attract more users

*Assessment questions:* Is the data exclusive or can competitors acquire equivalent data? Is the data labeled and structured for ML use cases, or raw? Does the company own the data or merely license it from customers (creating contractual risk)?

---

### Moat Scoring Template

When analyzing a Technology company, score each moat dimension from 0–3:

| Moat Type | Score (0–3) | Evidence |
|---|---|---|
| Network Effects | | |
| Switching Costs | | |
| Intellectual Property | | |
| Scale Advantages | | |
| Proprietary Data | | |
| **Total (max 15)** | | |

**Interpretation:**
- **12–15:** Fortress moat. Justifies significant multiple premium. Focus analysis on whether moat is widening or narrowing.
- **8–11:** Durable competitive advantage. Moat exists but is not impenetrable. Monitor competitive dynamics closely.
- **4–7:** Narrow moat. Competitive advantage is real but limited in scope or duration. Multiple compression risk if any moat erodes.
- **0–3:** No meaningful moat. Commodity product. Growth story only; no durability premium warranted.

---

## SECTION 6: TECHNOLOGY SECTOR GLOSSARY

**1. ARR (Annual Recurring Revenue)**
The annualized value of all active subscription contracts at a point in time. Excludes one-time fees and professional services. The primary revenue metric for SaaS businesses. Distinguish from revenue recognized under GAAP (ASC 606), which may differ due to timing of recognition.

**2. Churn Rate (Logo and Revenue)**
*Logo churn:* Percentage of customers who cancel in a period. *Revenue churn (gross):* Percentage of ARR lost from cancellations and downgrades, before expansion. High logo churn is acceptable in SMB-focused businesses (natural attrition) but is a red flag in enterprise SaaS.

**3. CAC (Customer Acquisition Cost)**
Total sales and marketing expense divided by the number of new customers acquired in a period. Compare to LTV (Lifetime Value) for the LTV:CAC ratio. Best-in-class: LTV:CAC > 3x with CAC payback < 18 months.

**4. Hyperscaler**
The three dominant public cloud infrastructure providers: Amazon Web Services (AWS), Microsoft Azure, and Google Cloud Platform (GCP). Technology companies often depend on hyperscalers for compute/storage infrastructure, creating both cost risk (margin dependency) and distribution opportunity (marketplace co-sell arrangements).

**5. NRR (Net Revenue Retention)**
Also called NDR (Net Dollar Retention). Measures revenue retained from existing customers including expansion (upsells, cross-sells) and minus contraction (downgrades, churn). NRR > 100% means a company can grow ARR even with zero new customer acquisition. The single best proxy for product-market fit in enterprise SaaS.

**6. Rule of 40**
A heuristic for evaluating SaaS company health: Revenue Growth Rate (%) + FCF Margin (%) ≥ 40. Higher scores indicate a superior balance between growth and profitability. Popularized by Brad Feld; widely used by venture and public market investors.

**7. TAM / SAM / SOM**
*Total Addressable Market:* The revenue opportunity if a company captured 100% of its target market. *Serviceable Addressable Market:* The portion of TAM the company can realistically reach with its current product and go-to-market. *Serviceable Obtainable Market:* The near-term realistic capture. Analysts should stress-test management's TAM claims against independent research (Gartner, IDC, Forrester).

**8. Land and Expand**
A go-to-market model in which the company acquires customers with a small initial contract (the "land") and grows revenue within the account over time through upsell and cross-sell (the "expand"). High NRR businesses typically operate on this model. Success depends on product breadth, customer success investment, and account management quality.

**9. Consumption-Based / Usage-Based Pricing (UBP)**
A pricing model in which revenue scales with customer usage (API calls, data processed, compute hours) rather than fixed seat counts. More aligned with customer value but creates revenue volatility — customer budget cuts directly reduce revenue. Examples: Snowflake, Twilio, AWS.

**10. SBC (Stock-Based Compensation)**
Non-cash compensation paid to employees and executives in the form of equity grants (RSUs, options). Excluded from non-GAAP earnings but represents real shareholder dilution. Should be evaluated as a percentage of revenue and compared to FCF to assess true cash profitability.

**11. Gross Retention Rate (GRR)**
ARR retained from existing customers *excluding* expansion. GRR measures pure churn/contraction. NRR = GRR + Expansion Rate. GRR > 90% is considered strong in enterprise; GRR > 80% in SMB. GRR declining while NRR holds signals that expansion is masking deteriorating core retention — a quality-of-revenue concern.

**12. Platform vs. Point Solution**
A *platform* provides multiple integrated product modules (CRM + marketing automation + analytics on a single data model). A *point solution* solves one specific problem. Platforms command higher NRR and switching costs; point solutions are easier to deploy but more vulnerable to displacement by platforms. Salesforce, ServiceNow, and Microsoft 365 are platform businesses.

**13. Technical Debt**
Accumulated shortcuts, legacy code, and architectural decisions that reduce future development velocity and require costly remediation. Not directly visible in financial statements but manifests as higher R&D spend per feature released, more frequent outages, and slower product velocity vs. competitors. Look for management discussion of "modernizing infrastructure" or "re-architecting" as signals.

**14. API Economy / API-First**
A product architecture in which core functionality is exposed via APIs (Application Programming Interfaces), enabling third-party developers to build on top of the platform. API-first companies create developer ecosystems (Stripe, Twilio, Plaid) that drive distribution without direct sales. Monetization risk: API products can be undercut by hyperscalers bundling equivalent functionality at marginal cost.

**15. Generative AI / Foundation Models**
Large-scale machine learning models (GPT-4, Claude, Gemini, Llama) trained on broad datasets capable of generating text, code, images, and other content. The technology is reshaping competitive dynamics across all Technology subsectors: incumbents must integrate AI into products to maintain relevance; new entrants are using AI to compress the time-to-market for competitive products. Assess every Technology company's AI strategy along two dimensions: (a) AI as a product (selling AI-powered features) and (b) AI as a threat (competitors using AI to undercut the company's core value proposition).

---

## ANALYST QUICK-REFERENCE: RED FLAGS SUMMARY

Before completing any Technology 10-K analysis, verify none of the following are present without adequate explanation:

- [ ] Revenue growth decelerating more than 10 ppts YoY without a clear macro explanation
- [ ] NRR declining below 100% or no longer disclosed
- [ ] Gross margin contracting > 200 bps YoY
- [ ] SBC > 15% of revenue with diluted share count growing > 4% YoY
- [ ] FCF margin worsening alongside slowing growth (breaking the investment thesis)
- [ ] Customer concentration > 15% in a single customer
- [ ] Active SEC investigation, material weakness disclosure, or auditor change
- [ ] Goodwill > 50% of total assets (acquisition-heavy growth model with impairment risk)
- [ ] Magic Number < 0.5 for two or more consecutive quarters
- [ ] Management guidance withdrawn or materially lowered mid-year

---

*End of Technology Sector Analyst Skill File. This document should be re-read at the start of each new filing analysis. All metrics should be interpreted in the context of the company's subsector, lifecycle stage, and competitive position.*
