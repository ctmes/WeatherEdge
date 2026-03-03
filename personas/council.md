# WeatherEdge LLM Council — Master Operating Document
### How to Use the Four Personas as a Decision-Making System
**Version:** 1.0 | March 2026  
**Project:** WeatherEdge — August Labs  
**Author:** August Labs  

---

## 1. WHAT THE COUNCIL IS

### The Core Idea
The WeatherEdge LLM Council is a structured decision-making framework that uses four distinct AI personas to pressure-test every major decision before it is executed. Each persona represents a different critical perspective — quantitative rigour, domain expertise, competitive intelligence, and risk management. No single persona has all the answers. The council works because the four perspectives are in productive tension with each other.

Think of it as having four specialist advisors in the room for every important decision. The Quant asks if the edge is statistically real. The Meteorologist asks if the weather situation is actually forecastable. The Adversary asks if the edge will survive competition. The Risk Manager asks if a wrong answer will end the operation. Only when all four give a green light should WeatherEdge proceed with confidence.

### Why You Need This
Building an automated trading system alone is dangerous for a specific reason: confirmation bias. When you build a system yourself, you naturally want it to work. You interpret ambiguous signals as positive. You explain away losses. You extrapolate short winning streaks into long-term edge validation. The council exists to counteract this by forcing you to articulate the case against your own decisions before you execute them.

The council does not make decisions for you. It surfaces blind spots, forces rigorous thinking, and ensures that when you do deploy real capital, you have genuinely stress-tested the strategy from multiple angles.

### What the Council Is Not
The council is not a replacement for your own judgment. It is not an oracle that tells you what to do. It is not a bureaucratic process that slows everything down. It is a structured thinking tool that you invoke when the stakes are high enough to warrant it. For small, routine decisions — executing a well-validated signal in a familiar market — the council does not need to be convened. For major decisions — strategy changes, scaling up capital, entering new markets — it is essential.

---

## 2. THE FOUR PERSONAS — QUICK REFERENCE

### The Quant
**File:** `PERSONA_QUANT.md`  
**Core question:** Is the edge statistically real or is it noise?  
**Invoked for:** Edge validation, performance review, sizing decisions, overfitting detection  
**Signature move:** Demands confidence intervals on every win rate claim  
**Red flag phrase:** "That sample size is insufficient to conclude anything"  

### The Meteorologist
**File:** `PERSONA_METEOROLOGIST.md`  
**Core question:** Is this weather situation actually forecastable?  
**Invoked for:** Individual market assessment, model disagreement interpretation, station-specific risk  
**Signature move:** Flags dangerous atmospheric regimes before bets are placed  
**Red flag phrase:** "This is a convective day — no model on earth can reliably predict this"  

### The Adversary
**File:** `PERSONA_ADVERSARY.md`  
**Core question:** How will competition erode this edge and how fast?  
**Invoked for:** Market selection, strategy sustainability, execution pattern review  
**Signature move:** Identifies specific attack vectors competitors would use against the current strategy  
**Red flag phrase:** "Institutional bots have been in this market for 18 months — the edge is already gone"  

### The Risk Manager
**File:** `PERSONA_RISK_MANAGER.md`  
**Core question:** What is the worst realistic outcome and does it kill the operation?  
**Invoked for:** Position sizing, circuit breaker review, bug risk assessment, black swan planning  
**Signature move:** Quantifies every risk in concrete dollar terms at current bankroll  
**Red flag phrase:** "A 40% drawdown requires 67% recovery — that is not a survivable scenario at this scale"  

---

## 3. DECISION TAXONOMY — WHEN TO USE THE COUNCIL

Not every decision needs the full council. Use this taxonomy to determine the appropriate level of engagement.

### Level 1 — Routine Execution (No Council Required)
These decisions are made automatically by the WeatherEdge system according to pre-validated rules. The council has already signed off on the framework. Individual instances within that framework do not require re-review.

Examples:
- Executing a signal that meets all pre-defined criteria in a familiar market
- Skipping a market because ensemble spread exceeds the no-bet threshold
- Applying standard Kelly sizing to a validated signal type
- Closing a position after a 20% adverse move per stop-loss rules

**Process:** System executes automatically. Logged for periodic review.

### Level 2 — Tactical Decisions (Single Persona)
These decisions require one specific perspective but not a full council review. Identify which persona owns the question and invoke only that one.

Examples:
- A specific market has unusual atmospheric conditions → **Meteorologist only**
- A new station is being added to the system → **Meteorologist + Quant**
- Performance has been weak for two weeks → **Quant only**
- A competitor appears to be front-running a specific market → **Adversary only**
- Today's total exposure is approaching circuit breaker limits → **Risk Manager only**

**Process:** Invoke relevant persona prompt. Document response. Make decision. Log outcome.

### Level 3 — Strategic Decisions (Full Council)
These decisions have significant consequences and require all four perspectives before proceeding. Do not skip personas even if one seems less relevant — the most important insight often comes from the persona you thought was least applicable.

Examples:
- Transitioning from paper trading to live trading
- Scaling up from $500 to $3,000 total exposure
- Adding a new city or market type to the system
- Responding to a significant drawdown (>10%)
- Changing the primary forecast model
- Modifying position sizing rules
- Identifying a potential new edge type

**Process:** Full council convening protocol (see Section 4). Document all four responses. Synthesise. Make decision. Record reasoning.

### Level 4 — Emergency Decisions (Risk Manager First, Then Council)
These decisions need immediate action but also need the council's input as soon as possible.

Examples:
- Circuit breaker triggered
- Suspected bug causing abnormal trading behaviour
- Polymarket access disrupted with open positions
- Single day loss exceeding 5% of bankroll
- Discovery of systematic error in calibration model

**Process:** Risk Manager immediately for action decision. Full council review within 24 hours.

---

## 4. FULL COUNCIL CONVENING PROTOCOL

Use this exact process whenever a Level 3 decision requires the full council.

### Step 1 — Frame the Decision (5 minutes)
Write a clear, specific statement of the decision being made. Vague questions produce vague answers. Be precise about what you are deciding and what the options are.

**Template:**
```
DECISION: [Specific action being considered]
CONTEXT: [Current system state, recent performance, relevant data]
OPTIONS: [Option A vs Option B, or Go vs No-Go]
STAKES: [What happens if this decision is wrong?]
TIMELINE: [When does this decision need to be made?]
```

**Bad framing:** "Should I scale up?"  
**Good framing:** "Should I increase total exposure from $500 to $2,000 given 6 weeks of paper trading showing 62% win rate across 84 resolved markets with an estimated Brier Skill Score of 0.07?"

### Step 2 — The Quant Review (10 minutes)
Invoke The Quant persona with the framed decision. Ask specifically:
- Is the evidence sufficient to support this decision?
- What are the confidence intervals on the performance metrics cited?
- Is there any sign of overfitting in the results so far?
- What additional data would make this decision more defensible?

Paste The Quant's system prompt from `PERSONA_QUANT.md` followed by the framed decision. Record the full response.

**Green light conditions from The Quant:**
- Sample size is statistically sufficient for the claimed conclusion
- Win rate confidence interval does not include 50%
- Brier Skill Score is positive and meaningful
- No signs of overfitting detected

**Red light conditions from The Quant:**
- Insufficient sample size
- Performance driven by small number of outlier markets
- Edge estimate has wide uncertainty bounds
- Walk-forward testing not completed

### Step 3 — The Meteorologist Review (10 minutes)
Invoke The Meteorologist persona with the framed decision plus any relevant market-specific context. Ask specifically:
- Are there atmospheric conditions or seasonal patterns that affect this decision?
- What forecast regimes are coming up that should change the sizing?
- Are there station-specific factors not yet captured in the calibration model?
- What seasonal period are we entering and how does that affect reliability?

Paste The Meteorologist's system prompt from `PERSONA_METEOROLOGIST.md` followed by the framed decision. Record the full response.

**Green light conditions from The Meteorologist:**
- Current and upcoming seasonal period is relatively predictable for target markets
- No dangerous atmospheric regimes flagged for the next 2 weeks
- Station calibration is physically plausible for target stations
- Model agreement is generally good for the markets in question

**Red light conditions from The Meteorologist:**
- Transition season with high forecast uncertainty
- Known station microclimate factors not yet captured in calibration
- Active severe weather regime in target markets
- Model disagreement patterns suggest unreliable period ahead

### Step 4 — The Adversary Review (10 minutes)
Invoke The Adversary persona with the framed decision. Ask specifically:
- Who else is likely competing in the target markets?
- How quickly could the identified edge be replicated by a well-resourced competitor?
- What attack vectors does the current strategy expose?
- Is there evidence that competition has already compressed the edge?

Paste The Adversary's system prompt from `PERSONA_ADVERSARY.md` followed by the framed decision. Record the full response.

**Green light conditions from The Adversary:**
- Target markets show evidence of dumb money price-setting
- Strategy has differentiated elements that take time to replicate
- No evidence of front-running in recent execution data
- Markets are not yet dominated by institutional bots

**Red light conditions from The Adversary:**
- Target markets are already efficient — tight spreads, fast repricing
- Strategy is easily replicable with no unique data or calibration advantage
- Evidence of front-running in order book patterns
- Institutional bots are visible in target markets

### Step 5 — The Risk Manager Review (10 minutes)
Invoke The Risk Manager persona with the framed decision plus current bankroll state. Ask specifically:
- What is the worst realistic outcome of this decision in dollar terms?
- Does the position sizing comply with all limits?
- Are there any technical risks (bugs, stale data, execution failures) relevant to this decision?
- What is the recovery plan if the worst case occurs?

Paste The Risk Manager's system prompt from `PERSONA_RISK_MANAGER.md` followed by the framed decision plus current bankroll and exposure. Record the full response.

**Green light conditions from The Risk Manager:**
- Worst case loss is survivable and recoverable within reasonable timeframe
- All position limits are respected
- Circuit breakers are in place and functional
- Technical risk mitigations are adequate for the scale being considered

**Red light conditions from The Risk Manager:**
- Worst case loss exceeds 15% of bankroll
- Position limits would be strained by this decision
- No circuit breaker for the specific failure mode being introduced
- Technical risks not mitigated at proposed scale

### Step 6 — Synthesis and Decision (10 minutes)
Review all four responses. Tabulate the signals:

```
Council Summary Table:
┌─────────────────┬──────────────┬─────────────────────────────┐
│ Persona         │ Signal       │ Key Concern or Confirmation │
├─────────────────┼──────────────┼─────────────────────────────┤
│ The Quant       │ GREEN/RED    │ [One sentence summary]      │
│ The Meteorolog. │ GREEN/RED    │ [One sentence summary]      │
│ The Adversary   │ GREEN/RED    │ [One sentence summary]      │
│ The Risk Mgr    │ GREEN/RED    │ [One sentence summary]      │
└─────────────────┴──────────────┴─────────────────────────────┘
```

**Decision Rules:**
- 4 Green lights → Proceed with confidence
- 3 Green, 1 Red → Proceed with modifications addressing the red light concern
- 2 Green, 2 Red → Do not proceed. Address red light concerns and reconvene
- 1 or 0 Green → Do not proceed. Fundamental reassessment required

**The Override Rule:**
A single hard veto from any persona overrides the other three greens if the concern is existential — meaning it could permanently end the operation. The Risk Manager's circuit breakers are always hard vetoes. The Quant's "this edge is not statistically real" is a hard veto for scaling decisions. These are not negotiable.

### Step 7 — Document and Execute
Record the full council session:

```
COUNCIL SESSION LOG
Date: 
Decision: 
Quant signal: [GREEN/RED] — [Summary]
Meteorologist signal: [GREEN/RED] — [Summary]
Adversary signal: [GREEN/RED] — [Summary]
Risk Manager signal: [GREEN/RED] — [Summary]
Final decision: [GO/NO-GO/MODIFIED GO]
Modifications made: [If any]
Review date: [When will this decision be revisited?]
```

Store this log in the WeatherEdge database. When things go wrong — and they will — this log is how you identify whether the decision was reasonable given available information or whether the council process failed.

---

## 5. COUNCIL USE CASES — WORKED EXAMPLES

### Use Case 1: Transition from Paper to Live Trading

**Decision framing:**
```
DECISION: Begin live trading with $200 initial exposure
CONTEXT: 5 weeks paper trading, 91 resolved markets, 58% win rate, 
         BSS 0.06, primarily Wellington and Sydney markets
OPTIONS: Go live now vs continue paper trading another 4 weeks
STAKES: If edge is not real, lose $200. If we delay unnecessarily, 
        lose 4 more weeks of potential compounding.
TIMELINE: End of current month
```

**Expected council responses:**

*Quant:* "91 markets is borderline. Win rate confidence interval at 58% on 91 observations is approximately 47-68% — includes 50%. BSS of 0.06 is positive but marginal. Recommend continuing paper trading to reach 150+ markets before going live. If going live now, size at absolute minimum — $50 max per market."

*Meteorologist:* "Entering Southern Hemisphere autumn transition in April. Wellington and Sydney both entering higher uncertainty period. Recommend delaying live trading until May-June when winter settles in and predictability improves for these stations."

*Adversary:* "91 markets in Wellington and Sydney — these are not the most competitive markets on the platform. Dumb money is still present. Speed edge is available. No major concerns about competition for these specific markets at this scale."

*Risk Manager:* "$200 exposure with $50 max per market is within Phase 1 parameters. Survivable full loss. Circuit breakers in place. Green light on risk parameters — the question is whether the edge is real, not whether the risk is manageable."

**Synthesis:** 2 red, 1 green, 1 conditional green. Decision: Continue paper trading 4 more weeks. Target 150+ markets before going live. Revisit in May when weather is more predictable.

---

### Use Case 2: Single Market Assessment

**Decision framing:**
```
DECISION: Bet $150 YES on Wellington 20-22°C bin for March 15
CONTEXT: ECMWF shows 68% probability for 20-22°C. Market prices at 45%.
         Calculated edge: 23%. Lead time: 3 days.
OPTIONS: Bet $150 / Bet $75 (half sizing) / No bet
STAKES: $150 maximum loss
TIMELINE: Immediate
```

This is a Level 2 decision — primarily meteorological with risk sizing check.

**Meteorologist invoked:**
"March 15 is mid-autumn in Wellington. Check ECMWF ensemble spread — if below 3°C, confidence is reasonable. Wellington northerly foehn risk needs assessment for this date. If wind forecast shows northerly component at 850hPa, temperature could exceed 22°C, pushing into higher bin. Recommend checking wind direction forecast before committing to 20-22°C bin specifically."

**Risk Manager invoked:**
"$150 on a single bin is 1.5% of $10,000 bankroll — within the 3% per bin limit. However, check total Wellington exposure across all open positions. If you already have $500 in Wellington markets, this adds to correlated exposure. Verify correlated total before executing."

**Decision:** Check ensemble spread and wind forecast. Verify total Wellington exposure. If both clear, bet $75 (half sizing given transition season uncertainty per Meteorologist's flag).

---

### Use Case 3: Responding to 12% Drawdown

**Decision framing:**
```
DECISION: How to respond to 12% drawdown over 3 weeks
CONTEXT: Started at $10,000. Now at $8,800. Primary losses in Melbourne 
         markets during spring transition period. Win rate dropped to 
         48% over the drawdown period from previous 57%.
OPTIONS: Continue at current sizing / Reduce sizing 50% / 
         Pause and review / Switch focus to different markets
STAKES: If edge has decayed, continuing loses more. If it's normal 
        variance, pausing means missed recovery.
TIMELINE: Decision needed before next trading week
```

**Quant:** "Win rate drop from 57% to 48% over 3 weeks. Sample during drawdown: approximately 40 markets. Too small to conclude edge has decayed — 48% on 40 observations has a wide confidence interval (32-64%). Melbourne spring losses are consistent with what The Meteorologist would predict. Do not conclude edge is gone. Reduce sizing to control drawdown while gathering more data."

**Meteorologist:** "Melbourne spring transition is the highest variance period for that station. Losses here are entirely expected and do not indicate model failure. Recommend pausing Melbourne markets until November when weather stabilises. Redirect to Wellington winter markets which are in their most predictable period right now."

**Adversary:** "12% drawdown in volatile markets is not a competitive signal. No evidence this is edge erosion from competition — it is seasonal variance. Competitors in Melbourne markets face the same forecast uncertainty. Hold strategy, change market selection."

**Risk Manager:** "12% drawdown approaching the 15% circuit breaker. Reduce all position sizes by 25% immediately per protocol. Pause Melbourne markets. This is not an emergency but it requires disciplined response. Do not attempt to recover losses aggressively — that is how 12% becomes 25%."

**Synthesis:** 4 green lights for a specific action: Reduce sizing 25%, pause Melbourne, redirect to Wellington winter markets. Reconvene in 3 weeks with new performance data.

---

## 6. PERSONA INTERACTION DYNAMICS

### Where Personas Agree — Strong Signal
When all four personas point in the same direction, the signal is strong. This happens less often than you might expect because the personas have genuinely different concerns. When it does happen, it is meaningful.

**Strong GO signals (all four aligned):**
- Well-validated edge, predictable weather regime, no competitive pressure, manageable risk
- Rare but decisive — when all four agree, execute with confidence

**Strong NO-GO signals (all four aligned):**
- Unvalidated edge, unpredictable weather, competitive market, existential risk
- Also rare — when all four say no, do not override with optimism

### Where Personas Disagree — Information Signal
Disagreement between personas is not a problem — it is information. It tells you which dimension of the decision is the real uncertainty.

**Quant GREEN, Meteorologist RED:**
The statistics look good but the atmospheric situation is dangerous. The edge may be real in general but this specific market on this specific day is high risk. Honour the Meteorologist's veto on individual market decisions.

**Adversary RED, Risk Manager GREEN:**
The competition is concerning but the risk is manageable. This means the edge might compress but a wrong bet won't kill you. Consider reduced sizing — enough to gather data on competition dynamics without existential exposure.

**Quant RED, all others GREEN:**
The most important disagreement. The Quant saying the edge is not statistically real overrides all other positive signals for scaling decisions. Do not increase capital commitment on unvalidated edges. The other personas can only do their jobs if the edge is real.

**Risk Manager RED, all others GREEN:**
Also a hard veto for the specific concern raised. The Risk Manager's circuit breakers exist precisely for situations where everything looks good but one failure mode could be catastrophic. Always honour the Risk Manager's hard limits.

### The Productive Tension
The Adversary and the Risk Manager are natural partners — both are conservative and protective. The Quant and the Meteorologist are natural partners — both are domain-focused. The healthy council dynamic is:

- Quant and Meteorologist validate that the opportunity is real
- Adversary and Risk Manager validate that the operation can survive pursuing it

Both halves need to be satisfied. Domain validity without risk management produces blowups. Risk management without domain validity produces no returns.

---

## 7. MAINTAINING THE COUNCIL OVER TIME

### Updating Persona Documents
The persona documents are living references. Update them when:
- A new atmospheric failure mode is discovered → Update Meteorologist
- A new attack vector from competitors is identified → Update Adversary
- A new risk limit is calibrated from experience → Update Risk Manager
- New statistical methodology is adopted → Update Quant

**Update protocol:** Never edit the original document in place without versioning. Add a changelog section at the bottom of each persona document recording what changed and why.

### Seasonal Persona Weighting
Different seasons call for different persona emphasis:

**Southern Hemisphere Winter (June-August):**
- Meteorologist concern is low (predictable period)
- Quant and Adversary dominate decision-making
- Good period to validate edge statistically

**Southern Hemisphere Spring (September-November):**
- Meteorologist concern is high (transition period)
- Risk Manager tightens position sizing
- Adversary assesses whether to shift markets northward

**Southern Hemisphere Summer (December-February):**
- Meteorologist flags convective risk for inland markets
- Coastal stations remain tradeable with Meteorologist oversight
- Normal council weighting

### Council Fatigue Prevention
The biggest risk to the council process is that it becomes a rubber stamp — you go through the motions but already know what decision you want to make. Prevent this by:

- Writing the decision framing before invoking any persona — commit to the question before seeing the answers
- Recording sessions where personas gave red lights and you proceeded anyway — track outcome
- Quarterly review of all council sessions to identify patterns in overrides
- Never invoke personas in the order that builds toward your preferred answer — always Quant first, then Meteorologist, then Adversary, then Risk Manager

### When to Retire a Persona
Personas become obsolete if:
- The domain knowledge is significantly out of date
- The competitive landscape has shifted so dramatically that the Adversary's mental model is wrong
- New research invalidates key assumptions in the Quant's statistical framework

Review all four persona documents quarterly. If a core assumption is more than 12 months old and unverified, flag it for update.

---

## 8. COUNCIL SESSIONS LOG TEMPLATE

Use this template for every Level 3 council session. Store all logs in `/logs/council/` with filename format `COUNCIL_YYYYMMDD_[DECISION_TYPE].md`.

```markdown
# Council Session Log
Date: 
Session ID: 
Decision type: [SCALE_UP / NEW_MARKET / STRATEGY_CHANGE / DRAWDOWN_RESPONSE / OTHER]

## Decision Framing
Decision: 
Context: 
Options: 
Stakes: 
Timeline: 

## Quant Review
Signal: [GREEN / RED / CONDITIONAL]
Key finding: 
Full response: [Paste full LLM response here]

## Meteorologist Review
Signal: [GREEN / RED / CONDITIONAL]
Key finding: 
Full response: [Paste full LLM response here]

## Adversary Review
Signal: [GREEN / RED / CONDITIONAL]
Key finding: 
Full response: [Paste full LLM response here]

## Risk Manager Review
Signal: [GREEN / RED / CONDITIONAL]
Key finding: 
Full response: [Paste full LLM response here]

## Synthesis
| Persona | Signal | Key Concern |
|---------|--------|-------------|
| Quant | | |
| Meteorologist | | |
| Adversary | | |
| Risk Manager | | |

## Decision
Final decision: [GO / NO-GO / MODIFIED GO]
Modifications: 
Rationale: 
Review date: 

## Outcome (complete after resolution)
Date completed: 
Was decision correct: [YES / NO / UNCLEAR]
What we learned: 
Council process improvement: 
```

---

## 9. QUICK REFERENCE CARD

Print or save this section separately as a daily reference.

### Decision Routing
| Situation | Level | Personas |
|-----------|-------|---------|
| Routine signal execution | 1 | None |
| Unusual atmospheric conditions | 2 | Meteorologist |
| Performance review | 2 | Quant |
| Suspected front-running | 2 | Adversary |
| Approaching exposure limits | 2 | Risk Manager |
| Go live with real money | 3 | Full council |
| Scale up capital | 3 | Full council |
| New market type | 3 | Full council |
| Drawdown >10% | 3 | Full council |
| Strategy change | 3 | Full council |
| Circuit breaker triggered | 4 | Risk Manager first |
| Suspected bug | 4 | Risk Manager first |

### Hard Veto Conditions (Always Honoured)
- Quant: Edge not statistically validated → No capital scaling
- Meteorologist: Model divergence >3°C OR ensemble spread >6°C → No bet
- Adversary: Evidence of active front-running → Pause execution, review
- Risk Manager: Any circuit breaker threshold breached → Stop per protocol

### Council Green Light Summary
All four green = proceed with confidence  
Three green, one conditional = proceed with modification  
Two green, two red = do not proceed, address concerns  
One or zero green = fundamental reassessment required  

### Persona Prompt Files
- `PERSONA_QUANT.md` — Statistical validation
- `PERSONA_METEOROLOGIST.md` — Forecast domain expertise
- `PERSONA_ADVERSARY.md` — Competitive intelligence
- `PERSONA_RISK_MANAGER.md` — Bankroll protection

### Council Session Log Location
`/logs/council/COUNCIL_YYYYMMDD_[TYPE].md`

---

## 10. PHILOSOPHY — WHY THIS WORKS

The WeatherEdge LLM Council is effective because it operationalises something that is otherwise very difficult to do alone: genuine adversarial thinking about your own strategy.

When you build something yourself, you are cognitively invested in it. You want it to work. Every positive result feels like confirmation and every negative result feels like an anomaly. The personas do not share your investment. They have different priors, different concerns, different definitions of success. They will tell you things you do not want to hear.

The Quant does not care that you spent three weeks building the calibration model — it cares whether the model works.

The Meteorologist does not care about your edge calculation — it cares whether the atmosphere will cooperate.

The Adversary does not care about your returns so far — it cares whether those returns will continue when someone smarter notices what you are doing.

The Risk Manager does not care about any of the above — it cares about whether you will still be operational in 24 months.

Together they represent the full picture. A strategy that satisfies all four is genuinely robust. A strategy that only satisfies one or two is fragile in ways you have not yet seen.

Use the council consistently, document the sessions honestly, and update the personas as you learn. Over time this system becomes your most valuable asset — not the trading algorithm itself, but the decision-making framework that keeps it alive and improving.

---

*This document governs the use of the WeatherEdge LLM Council. Read in conjunction with all four persona documents. Update version number when making changes to the council structure or decision protocols.*

**Document Index:**
- `WEATHER_BOT_SPEC.md` — Full system specification
- `WEATHEREDGE_LLM_COUNCIL.md` — This document
- `PERSONA_QUANT.md` — The Quant persona
- `PERSONA_METEOROLOGIST.md` — The Meteorologist persona
- `PERSONA_ADVERSARY.md` — The Adversary persona
- `PERSONA_RISK_MANAGER.md` — The Risk Manager persona