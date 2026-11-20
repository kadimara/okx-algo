# MS strategy short (Pine v6)

```pinescript
//@version=6
strategy("Market structure strategy - short", "MS strategy short", overlay = true, scale = scale.right,
     initial_capital = 10000, currency = currency.USD, pyramiding = 0,
     commission_type = strategy.commission.percent, commission_value = 0.05, slippage = 2,
     margin_long = 1, margin_short = 1,
     max_labels_count = 500, max_lines_count = 500, max_bars_back = 5000)

// =============================================================================
// Market structure strategy: trades LTF pullbacks in the direction of the
// structure, optionally only with the HTF trend.
// Structure
//  - Swing size n = "Swing size (candles)" (24 on a 2h chart = 2 days).
//    Swing high: the candle n bars ago has a high strictly
//    above the n candles before it and >= the n candles after it (lows mirrored).
//    A pivot is only known n candles later, at its confirmation candle.
//  - Labels: HH / LH vs the previous high, LL / HL vs the previous low. A new
//    high while the last swing was a high replaces it only if higher (lows
//    mirrored).
// Trend
//  - Bullish: last high HH + last low HL. Bearish: last high LH + last low LL.
//  - Break of structure (BOS): a candle closing below the last swing low turns
//    the trend bearish at once (above the last swing high: bullish). Each BOS
//    is drawn as a dashed line from the broken swing to the break.
//  - CHoCH (change of character): a break against the current trend, so the
//    trend rotates (bullish -> bearish or bearish -> bullish). Drawn as a
//    solid line labelled "CHoCH". A break in the trend direction stays "BOS".
//  - Possible swings: the candle that becomes the next swing if nothing beats
//    it, shown faded as "LL? (5)" = possible LL, confirmed in 5 candles. It
//    moves when a more extreme candle comes and disappears when confirmed.
//  - "Internal structure" on: if no opposite swing was confirmed since the
//    broken one, the most extreme candle since it becomes the LH / HL right
//    away (default). Off: only real swings are labelled.
// HTF structure
//  - The same swing rules with a bigger swing size (default 84 candles), drawn as
//    bigger white labels. Its own trend (same rules: HH + HL / LH + LL, or a
//    close beyond the last HTF swing) is shown top right next to the normal
//    (LTF) trend. BOS and CHoCH lines come from the normal structure only.
// Trading: SHORTS ONLY (longs are in "MS strategy long")
//  - Setups (each can be switched on / off per side):
//      Continuation (BOS): a break in the trend direction (BOS up in a bullish
//        trend -> long from the HL behind it; BOS down -> short from the LH).
//        Select: none / first BOS only / first and second BOS / all BOS. The
//        BOS number counts the breaks since the trend turned (BOS 1, 2, ...).
//      Pullback end (CHoCH): a break that turns the trend (CHoCH up -> long
//        from the low of the pullback; CHoCH down -> short from the high).
//  - "Only with the HTF trend": longs only while the HTF is bullish, shorts
//    only while it is bearish. A waiting order is cancelled when the HTF
//    flips; an open trade is left to the stop / trailing stop.
//  - Entry (always a limit): long at the lower of the HL candle's open /
//    close + entry %, short at the higher of the LH candle's open / close -
//    entry %. Stop: the stop % beyond the HL low / LH high.
//  - Target / R filter: the newest swing on the other side (body edge -
//    target %); right after a break, the further of the broken swing and the
//    swing before it (short: the lower low, long: the higher high). Setups
//    below "Skip trades below" R are skipped.
//  - The order stays until it fills, the setup is no longer valid (trend
//    flips, HTF filter fails, a newer HL / LH appears without a new break).
//    At most one trade per HL / LH.
//  - Size: 1 % of equity at risk at the stop, capped at 2x leverage.
//  - Exits (separate for longs and shorts): optional target, break-even after
//    x R, candle trailing stop (lowest low / highest high of the last N
//    candles) after x R.
//    Exit signals: TP, SL, BE, TS.
// =============================================================================

// ---------------------------------------------------------------- inputs
n          = input.int(24, "Swing size (candles)", minval = 1, maxval = 2000, group = "═══ INDICATOR ═══ Structure",
     tooltip = "A swing high needs this many candles on each side with lower highs (lows mirrored). 24 on a 2h chart = 2 days.")
showLabels = input.bool(true, "Show HH / HL / LH / LL labels", group = "═══ INDICATOR ═══ Structure")
showBos    = input.bool(true, "Show BOS / CHoCH", group = "═══ INDICATOR ═══ Structure")
useInt     = input.bool(true, "Internal structure (swing at BOS)", group = "═══ INDICATOR ═══ Structure",
     tooltip = "On: when a BOS happens before the next swing is confirmed, the highest (BOS down) / lowest (BOS up) candle since the broken swing becomes an LH / HL right away. Off: only real swings (swing size on each side) are used.")
showPend   = input.bool(true, "Show possible swings (not confirmed yet)", group = "═══ INDICATOR ═══ Structure",
     tooltip = "Faded label on the candle that will become the next swing if no more extreme candle comes. The number is how many candles are left until it is confirmed.")

n2         = input.int(84, "HTF swing size (candles)", minval = 1, maxval = 2000, group = "═══ INDICATOR ═══ HTF structure",
     tooltip = "Same rule as the swing size, with more candles. 84 on a 2h chart = 7 days.")
showHtf    = input.bool(true, "Show HTF labels", group = "═══ INDICATOR ═══ HTF structure")

// longs are not traded in this script
bool   allowLong  = false
string lCont      = "None"
bool   lChoch     = false
bool   lHtf       = false
float  lEntryPct  = 0.0
float  lStopPct   = 0.0
float  lMinRR     = 0.0
float  lTgtPct    = 0.0
bool   lUseTgt    = false
float  lBeR       = 0.0
bool   lTrailOn   = false
float  lTrailR    = 0.0
int    lTrailLen  = 1

bool allowShort = true
sCont      = input.string("All BOS", "Setup: continuation (BOS down)", options = ["None", "First BOS only", "First and second BOS", "All BOS"], group = "═══ TRADE ═══ Short",
     tooltip = "A BOS down while the trend is already bearish: short from the LH behind the break. The BOS number counts the breaks down since the trend turned bearish (shown on the chart as BOS 1, BOS 2, ...).")
sChoch     = input.bool(true, "Setup: rally end (CHoCH down)", group = "═══ TRADE ═══ Short",
     tooltip = "A CHoCH down (bullish -> bearish): short from the high of the rally.")
sHtf       = input.bool(false, "Only when the HTF is bearish", group = "═══ TRADE ═══ Short")
sEntryPct  = input.float(0.2, "Entry offset (%)", minval = 0.0, step = 0.05, group = "═══ TRADE ═══ Short",
     tooltip = "Sell limit this % below the LH candle's open or close, whichever is higher.")
sStopPct   = input.float(0.0, "Stop offset (%)", minval = 0.0, step = 0.05, group = "═══ TRADE ═══ Short",
     tooltip = "Stop this % above the LH high (also used for the trailing stop).")
sMinRR     = input.float(2.0, "Skip trades below (R, 0 = off)", minval = 0.0, step = 0.25, group = "═══ TRADE ═══ Short")
sTgtPct    = input.float(0.0, "Target offset (%)", minval = 0.0, step = 0.05, group = "═══ TRADE ═══ Short",
     tooltip = "Target this % above the swing low candle's open or close, whichever is lower.")

sUseTgt    = input.bool(false, "Use target", group = "═══ TRADE ═══ Short exit",
     tooltip = "Off: no take-profit order; shorts only close at the stop (initial, break-even or trailing).")
sBeR       = input.float(2.0, "Move stop to entry after (R, 0 = off)", minval = 0.0, step = 0.25, group = "═══ TRADE ═══ Short exit")
sTrailOn   = input.bool(true, "Candle trailing stop", group = "═══ TRADE ═══ Short exit")
sTrailR    = input.float(2.0, "Start trailing after (R)", minval = 0.0, step = 0.25, group = "═══ TRADE ═══ Short exit")
sTrailLen  = input.int(2, "Trail behind the last N candles", minval = 1, group = "═══ TRADE ═══ Short exit",
     tooltip = "Stop above the highest high of the last N candles (+ short stop offset %). Only moves down.")

float riskPct = 1.0              // risk per trade, % of equity
float maxLev  = 2.0              // max position size / equity

color BULL_COL = #1D9E75
color BEAR_COL = #E24B4A
color HTF_COL  = color.white


// ---------------------------------------------------------------- pivots
isPivotHigh(int len) =>
    bool ok = bar_index >= 2 * len
    if ok
        float c = high[len]
        for i = 1 to len
            if high[len + i] >= c or high[len - i] > c
                ok := false
                break
    ok

isPivotLow(int len) =>
    bool ok = bar_index >= 2 * len
    if ok
        float c = low[len]
        for i = 1 to len
            if low[len + i] <= c or low[len - i] < c
                ok := false
                break
    ok

var float  lastHighP  = na
var int    lastHighB  = -1
var float  prevHighP  = na
var string highTag    = ""
var label  highLbl    = na
var float  lastLowP   = na
var int    lastLowB   = -1
var float  prevLowP   = na
var string lowTag     = ""
var label  lowLbl     = na
var float  highBody   = na      // body top of the last swing-high candle (higher of open / close)
var float  prevHighBody = na    // body top of the swing high before it
var float  lowBody    = na      // body bottom of the last swing-low candle (lower of open / close)
var float  prevLowBody = na     // body bottom of the swing low before it
var int    lastType   = 0       // 1 = last pivot was a high, -1 = a low
var bool   lowBroken  = false   // a candle closed below the last swing low
var bool   highBroken = false   // a candle closed above the last swing high
var bool   bosL       = false   // a break up is active (long target = the higher of the broken high and the one before)
var float  bosLTgt    = na
var bool   bosS       = false   // a break down is active (short target = the lower of the broken low and the one before)
var float  bosSTgt    = na
var int    validLowB  = -1      // swing low confirmed by a break up after it (long allowed)
var bool   validLowCh = false   //   that break was a CHoCH (else a continuation BOS)
var int    validHighB = -1      // swing high confirmed by a break down after it (short allowed)
var bool   validHighCh = false
var int    bosUpCount  = 0      // continuation BOS up since the trend turned bullish
var int    bosDnCount  = 0      // continuation BOS down since the trend turned bearish
var int    validLowNum  = 0     // BOS number of the break that confirmed the HL (0 = CHoCH)
var int    validHighNum = 0

bool confHigh = false
bool confLow  = false
bool bosDown = false            // a break (BOS or CHoCH) happened on this candle
bool bosUp   = false
bool chochDown = false          // the break turned the trend (bullish -> bearish)
bool chochUp   = false

if isPivotHigh(n)
    float p = high[n]
    if lastType != 1 or p > lastHighP
        if lastType == 1
            label.delete(highLbl)              // higher high in the same swing: replace
        else
            prevHighP    := lastHighP
            prevHighBody := highBody
        lastHighP  := p
        lastHighB  := bar_index - n
        highBody   := math.max(open[n], close[n])
        highBroken := false
        lastType   := 1
        confHigh   := true
        highTag    := na(prevHighP) ? "H" : p > prevHighP ? "HH" : "LH"
        if showLabels
            highLbl := label.new(bar_index - n, p, highTag, color = color(na), style = label.style_label_down, textcolor = BEAR_COL)

if isPivotLow(n)
    float p = low[n]
    if lastType != -1 or p < lastLowP
        if lastType == -1
            label.delete(lowLbl)               // lower low in the same swing: replace
        else
            prevLowP    := lastLowP
            prevLowBody := lowBody
        lastLowP  := p
        lastLowB  := bar_index - n
        lowBody   := math.min(open[n], close[n])
        lowBroken := false
        lastType  := -1
        confLow   := true
        lowTag    := na(prevLowP) ? "L" : p < prevLowP ? "LL" : "HL"
        if showLabels
            lowLbl := label.new(bar_index - n, p, lowTag, color = color(na), style = label.style_label_up, textcolor = BULL_COL)

// ---------------------------------------------------------------- trend + BOS
var string trend = "neutral"
if confHigh or confLow
    if highTag == "HH" and lowTag == "HL"
        trend := "bullish"
    else if highTag == "LH" and lowTag == "LL"
        trend := "bearish"

// the BOS count starts again whenever the trend turns
if trend == "bullish"
    bosDnCount := 0
if trend == "bearish"
    bosUpCount := 0

// a newly confirmed swing ends the break target on that side
if confHigh
    bosL := false
if confLow
    bosS := false

// draw a break: line from the broken swing to the break candle (BOS dashed, CHoCH solid)
drawBos(int fromB, float level, bool down, bool choch, int num) =>
    color c = down ? BEAR_COL : BULL_COL
    line.new(fromB, level, bar_index, level, color = c, style = choch ? line.style_solid : line.style_dashed, width = 1)
    label.new(math.round((fromB + bar_index) / 2), level, choch ? "CHoCH" : "BOS " + str.tostring(num), color = color(na), textcolor = c,
         style = down ? label.style_label_up : label.style_label_down, size = size.small)

// down: close below the last swing low -> bearish now; if no swing high has
//       been confirmed since that low, the highest candle since it becomes the LH
if not lowBroken and not na(lastLowP) and close < lastLowP
    lowBroken := true
    bosDown   := true
    chochDown := trend == "bullish"
    bosDnCount := chochDown ? 0 : bosDnCount + 1
    bosUpCount := 0
    trend     := "bearish"
    if showBos
        drawBos(lastLowB, lastLowP, true, chochDown, bosDnCount)
    bosL    := false
    bosS    := true
    bosSTgt := math.min(lowBody, nz(prevLowBody, lowBody))   // the lowest of the broken low and the low before it
    if useInt and lastType == -1
        int   len = math.min(bar_index - lastLowB, 4999)
        int   hb  = bar_index
        float hp  = high
        float hc  = close
        for i = 0 to len - 1
            if high[i] > hp
                hp := high[i]
                hb := bar_index - i
                hc := close[i]
        prevHighP    := lastHighP
        prevHighBody := highBody
        lastHighP    := hp
        lastHighB    := hb
        highBody     := math.max(open[bar_index - hb], hc)
        highBroken := false
        lastType   := 1
        highTag    := na(prevHighP) ? "H" : hp > prevHighP ? "HH" : "LH"
        if showLabels
            highLbl := label.new(hb, hp, highTag, color = color(na), style = label.style_label_down, textcolor = BEAR_COL)
    validHighB  := lastHighB                    // this LH is now confirmed by the break down
    validHighCh := chochDown
    validHighNum := bosDnCount

// up: close above the last swing high -> bullish now; if no swing low has been
//     confirmed since that high, the lowest candle since it becomes the HL
if not highBroken and not na(lastHighP) and close > lastHighP
    highBroken := true
    bosUp      := true
    chochUp    := trend == "bearish"
    bosUpCount := chochUp ? 0 : bosUpCount + 1
    bosDnCount := 0
    trend      := "bullish"
    if showBos
        drawBos(lastHighB, lastHighP, false, chochUp, bosUpCount)
    bosS    := false
    bosL    := true
    bosLTgt := math.max(highBody, nz(prevHighBody, highBody)) // the highest of the broken high and the high before it
    if useInt and lastType == 1
        int   len = math.min(bar_index - lastHighB, 4999)
        int   lb  = bar_index
        float lp  = low
        float lc  = close
        for i = 0 to len - 1
            if low[i] < lp
                lp := low[i]
                lb := bar_index - i
                lc := close[i]
        prevLowP    := lastLowP
        prevLowBody := lowBody
        lastLowP    := lp
        lastLowB    := lb
        lowBody     := math.min(open[bar_index - lb], lc)
        lowBroken := false
        lastType  := -1
        lowTag    := na(prevLowP) ? "L" : lp < prevLowP ? "LL" : "HL"
        if showLabels
            lowLbl := label.new(lb, lp, lowTag, color = color(na), style = label.style_label_up, textcolor = BULL_COL)
    validLowB  := lastLowB                      // this HL is now confirmed by the break up
    validLowCh := chochUp
    validLowNum := bosUpCount

// ---------------------------------------------------------------- possible swings (not confirmed yet)
// candidate = most extreme candle of the last n (oldest on a tie), already beyond
// the n candles before it; it is confirmed if the next candles don't beat it
findCand(bool hi) =>
    int   k = -1
    float p = na
    for i = 0 to n - 1
        float v = hi ? high[i] : low[i]
        if na(p) or (hi ? v >= p : v <= p)
            p := v
            k := i
    bool ok = k >= 0 and bar_index >= k + n
    if ok
        for i = 1 to n
            float v = hi ? high[k + i] : low[k + i]
            if hi ? v >= p : v <= p
                ok := false
                break
    [ok, k, p]

[okH, kH, pH] = findCand(true)
[okL, kL, pL] = findCand(false)
var label pendHiLbl = na
var label pendLoLbl = na
if barstate.islast
    label.delete(pendHiLbl)
    label.delete(pendLoLbl)
    int lastB = math.max(lastHighB, lastLowB)
    if showPend and okH and bar_index - kH > lastB and (lastType != 1 or pH > lastHighP)
        float ref = lastType == 1 ? prevHighP : lastHighP
        string tg = na(ref) ? "H" : pH > ref ? "HH" : "LH"
        pendHiLbl := label.new(bar_index - kH, pH, tg + "? (" + str.tostring(n - kH) + ")", color = color(na),
             style = label.style_label_down, textcolor = color.new(BEAR_COL, 50),
             tooltip = "Possible " + tg + ": confirmed in " + str.tostring(n - kH) + " candles if no higher high comes")
    if showPend and okL and bar_index - kL > lastB and (lastType != -1 or pL < lastLowP)
        float ref = lastType == -1 ? prevLowP : lastLowP
        string tg = na(ref) ? "L" : pL < ref ? "LL" : "HL"
        pendLoLbl := label.new(bar_index - kL, pL, tg + "? (" + str.tostring(n - kL) + ")", color = color(na),
             style = label.style_label_up, textcolor = color.new(BULL_COL, 50),
             tooltip = "Possible " + tg + ": confirmed in " + str.tostring(n - kL) + " candles if no lower low comes")

// ---------------------------------------------------------------- HTF structure (labels + trend)
var float  htfHighP    = na
var float  htfPrevHigh = na
var string htfHighTag  = ""
var label  htfHighLbl  = na
var float  htfLowP     = na
var float  htfPrevLow  = na
var string htfLowTag   = ""
var label  htfLowLbl   = na
var int    htfType     = 0      // 1 = last HTF pivot was a high, -1 = a low
var bool   htfHighBroken = false
var bool   htfLowBroken  = false
var string htfTrend    = "neutral"
bool htfConfHigh = false
bool htfConfLow  = false

if isPivotHigh(n2)
    float p = high[n2]
    if htfType != 1 or p > htfHighP
        if htfType == 1
            label.delete(htfHighLbl)
        else
            htfPrevHigh := htfHighP
        htfHighP    := p
        htfHighBroken := false
        htfType     := 1
        htfConfHigh := true
        htfHighTag  := na(htfPrevHigh) ? "H" : p > htfPrevHigh ? "HH" : "LH"
        if showHtf     // the extra line lifts the text above a normal label on the same candle
            htfHighLbl := label.new(bar_index - n2, p, htfHighTag + "\n ", color = color(na), style = label.style_label_down,
                 textcolor = HTF_COL, size = size.large, tooltip = "HTF " + htfHighTag)

if isPivotLow(n2)
    float p = low[n2]
    if htfType != -1 or p < htfLowP
        if htfType == -1
            label.delete(htfLowLbl)
        else
            htfPrevLow := htfLowP
        htfLowP    := p
        htfLowBroken := false
        htfType    := -1
        htfConfLow := true
        htfLowTag  := na(htfPrevLow) ? "L" : p < htfPrevLow ? "LL" : "HL"
        if showHtf
            htfLowLbl := label.new(bar_index - n2, p, " \n" + htfLowTag, color = color(na), style = label.style_label_up,
                 textcolor = HTF_COL, size = size.large, tooltip = "HTF " + htfLowTag)

// HTF trend: labels, or a close beyond the last HTF swing
if htfConfHigh or htfConfLow
    if htfHighTag == "HH" and htfLowTag == "HL"
        htfTrend := "bullish"
    else if htfHighTag == "LH" and htfLowTag == "LL"
        htfTrend := "bearish"
if not htfLowBroken and not na(htfLowP) and close < htfLowP
    htfLowBroken := true
    htfTrend     := "bearish"
if not htfHighBroken and not na(htfHighP) and close > htfHighP
    htfHighBroken := true
    htfTrend      := "bullish"

// ---------------------------------------------------------------- trend panel (top right)
var table panel = table.new(position.top_right, 2, 2, bgcolor = color.new(color.black, 30), border_width = 1)
trendCol(string t) =>
    t == "bullish" ? BULL_COL : t == "bearish" ? BEAR_COL : color.gray
trendTxt(string t) =>
    t == "bullish" ? "Bullish ▲" : t == "bearish" ? "Bearish ▼" : "Neutral"
if barstate.islast
    table.cell(panel, 0, 0, "HTF (" + str.tostring(n2) + ")", text_color = HTF_COL, text_halign = text.align_left)
    table.cell(panel, 1, 0, trendTxt(htfTrend), text_color = trendCol(htfTrend), text_halign = text.align_left)
    table.cell(panel, 0, 1, "LTF (" + str.tostring(n) + ")", text_color = HTF_COL, text_halign = text.align_left)
    table.cell(panel, 1, 1, trendTxt(trend), text_color = trendCol(trend), text_halign = text.align_left)

// ---------------------------------------------------------------- trading
var int   usedLowB  = -1        // HL candle already traded (one long per HL)
var int   usedHighB = -1        // LH candle already traded (one short per LH)
var bool  pendLong  = false     // a long limit was working after the previous candle
var bool  pendShort = false
var float lPlanE    = na        // long plan: entry, stop, target, HL pivot candle
var float lPlanS    = na
var float lPlanT    = na
var int   lPlanB    = -1
var float sPlanE    = na        // short plan: entry, stop, target, LH pivot candle
var float sPlanS    = na
var float sPlanT    = na
var int   sPlanB    = -1
var float lInitS    = na        // initial stops, used to measure R while in the trade
var float sInitS    = na

// an entry filled: mark its pivot as used
int entries = strategy.closedtrades + strategy.opentrades
if entries > nz(entries[1])
    if pendLong
        usedLowB := lPlanB
    if pendShort
        usedHighB := sPlanB

// entry: the HL / LH candle's body (long: lower of open / close, short: higher)
// stop:  just past the HL low (long) / LH high (short)
// target: the newest swing on the other side; right after a break, the further of the broken swing and the one before it
float lEntry = lowBody  * (1 + lEntryPct / 100)
float lStop  = lastLowP * (1 - lStopPct / 100)
float lTgt   = (bosL ? bosLTgt : highBody) * (1 - lTgtPct / 100)
int   lId    = lastLowB
float sEntry = highBody * (1 - sEntryPct / 100)
float sStop  = lastHighP * (1 + sStopPct / 100)
float sTgt   = (bosS ? bosSTgt : lowBody)  * (1 + sTgtPct / 100)
int   sId    = lastHighB

// continuation filter: which BOS numbers are traded
bosOk(string mode, int num) =>
    mode == "All BOS" or (mode == "First BOS only" and num == 1) or (mode == "First and second BOS" and num <= 2)
bool lSetupOn = validLowCh ? lChoch : bosOk(lCont, validLowNum)
bool sSetupOn = validHighCh ? sChoch : bosOk(sCont, validHighNum)
bool longSetup  = allowLong and trend == "bullish" and lId == validLowB and lId != usedLowB and lSetupOn and
     (not lHtf or htfTrend == "bullish") and lEntry > lStop and lTgt > lEntry and close > lEntry and
     (lTgt - lEntry) / (lEntry - lStop) >= lMinRR
bool shortSetup = allowShort and trend == "bearish" and sId == validHighB and sId != usedHighB and sSetupOn and
     (not sHtf or htfTrend == "bearish") and sStop > sEntry and sTgt < sEntry and close < sEntry and
     (sEntry - sTgt) / (sStop - sEntry) >= sMinRR

bool flat = strategy.position_size == 0

// position size: risk % of equity at the stop, capped at max leverage
posQty(float e, float st) =>
    math.min(strategy.equity * riskPct / 100 / math.abs(e - st), strategy.equity * maxLev / e)

// trade stats for the List of Trades (Signal column): setup, potential R, stop distance %, leverage
tradeTag(string side, float e, float st, float tg) =>
    float lev = posQty(e, st) * e / strategy.equity
    side + " " + str.tostring(math.abs(tg - e) / math.abs(e - st), "#.##") + "R | SL " +
         str.tostring(math.abs(e - st) / e * 100, "#.##") + "% | " + str.tostring(lev, "#.##") + "x"

if flat and longSetup
    lPlanE := lEntry
    lPlanS := lStop
    lPlanT := lTgt
    lPlanB := lId
    lInitS := lStop
    strategy.entry("Long", strategy.long, posQty(lEntry, lStop), limit = lEntry,
         comment = tradeTag(validLowCh ? "Long CHoCH" : "Long BOS", lEntry, lStop, lTgt))
    strategy.exit("Long exit", "Long", stop = lStop, limit = lUseTgt ? lTgt : na, comment_profit = "TP", comment_loss = "SL")
else
    strategy.cancel("Long")

if flat and shortSetup
    sPlanE := sEntry
    sPlanS := sStop
    sPlanT := sTgt
    sPlanB := sId
    sInitS := sStop
    strategy.entry("Short", strategy.short, posQty(sEntry, sStop), limit = sEntry,
         comment = tradeTag(validHighCh ? "Short CHoCH" : "Short BOS", sEntry, sStop, sTgt))
    strategy.exit("Short exit", "Short", stop = sStop, limit = sUseTgt ? sTgt : na, comment_profit = "TP", comment_loss = "SL")
else
    strategy.cancel("Short")

pendLong  := flat and longSetup
pendShort := flat and shortSetup

// stop management, from the candle after the fill:
//  - break-even: stop to entry once price has moved the side's break-even R in your favour
//  - candle trail: once price has moved the side's trail R, the stop follows the lowest low
//    (long) / highest high (short) of the side's last N candles, only in your favour
var bool  beDone  = false
var bool  trailed = false
var float bestPx  = na          // best price reached since the fill
float lowN  = ta.lowest(low, lTrailLen)
float highN = ta.highest(high, sTrailLen)
if flat
    beDone  := false
    trailed := false
    bestPx  := na
bool heldSincePrev = strategy.position_size != 0 and strategy.position_size[1] != 0
if heldSincePrev
    bool  isLong = strategy.position_size > 0
    float ep     = strategy.position_avg_price
    bestPx := na(bestPx) ? (isLong ? high : low) : isLong ? math.max(bestPx, high) : math.min(bestPx, low)
    float r0     = isLong ? ep - lInitS : sInitS - ep
    float moveR  = r0 > 0 ? (isLong ? bestPx - ep : ep - bestPx) / r0 : 0.0
    float cur    = isLong ? lPlanS : sPlanS
    float newS   = cur
    float beR_ = isLong ? lBeR : sBeR
    if beR_ > 0 and not beDone and moveR >= beR_
        beDone := true
        newS   := isLong ? math.max(newS, ep) : math.min(newS, ep)
    if (isLong ? lTrailOn : sTrailOn) and moveR >= (isLong ? lTrailR : sTrailR)
        float tS = isLong ? lowN * (1 - lStopPct / 100) : highN * (1 + sStopPct / 100)
        if isLong ? tS > newS : tS < newS
            newS    := tS
            trailed := true
    if newS != cur
        string tag = trailed ? "TS" : "BE"
        if isLong
            lPlanS := newS
            strategy.exit("Long exit", "Long", stop = newS, limit = lUseTgt ? lPlanT : na, comment_profit = "TP", comment_loss = tag)
        else
            sPlanS := newS
            strategy.exit("Short exit", "Short", stop = newS, limit = sUseTgt ? sPlanT : na, comment_profit = "TP", comment_loss = tag)

// levels: the working order while waiting, the trade while in it
bool  showL = strategy.position_size > 0 or pendLong
bool  showS = strategy.position_size < 0 or pendShort
float pE0 = showL ? lPlanE : showS ? sPlanE : na
float pS0 = showL ? lPlanS : showS ? sPlanS : na
float pT0 = showL ? (lUseTgt ? lPlanT : na) : showS ? (sUseTgt ? sPlanT : na) : na
// leave a gap of one candle wherever the levels change, so they aren't joined
bool  lvlChg = not na(pE0) and not na(pE0[1]) and (pE0 != pE0[1] or pS0 != pS0[1] or pT0 != pT0[1])
float pE = lvlChg ? na : pE0
float pS = lvlChg ? na : pS0
float pT = lvlChg ? na : pT0
plot(pE, "Entry",  color.gray, 1, plot.style_linebr)
plot(pS, "Stop",   BEAR_COL,   1, plot.style_linebr)
plot(pT, "Target", BULL_COL,   1, plot.style_linebr)

```
