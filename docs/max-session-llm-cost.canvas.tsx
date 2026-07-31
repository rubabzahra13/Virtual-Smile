import {
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  PieChart,
  Row,
  Spacer,
  Stack,
  Stat,
  Table,
  Text,
} from "cursor/canvas";

/** Live remeasure 2026-07-31 — Gemini analysis/sim + Groq chat. */
const MEASURED_AT = "2026-07-31T07:44:57Z";
/** Mid-market approx as of 2026-07-31 (Xe / market feeds ~277.5–278.6). */
const USD_TO_PKR = 278;

const SESSION_COST = 0.07616831;
const PREV_SESSION_COST = 0.07495536;
const TOTAL_TOKENS = 20811;
const INPUT_TOKENS = 17263;
const OUTPUT_TOKENS = 3548;
const SCORE = 64;
const REPORT_CHARS = 4317;
const CHAT_COST = 0.00032441;
const OLD_CHAT_COST = 0.0025955;
const SIM_COST = 0.0695675;
const PREV_SIM_COST = 0.06843;
const QWEN_SIM_COST = 0.02;
const SESSION_COST_QWEN = SESSION_COST - SIM_COST + QWEN_SIM_COST;

const CLEAN = {
  cost: 0.0743488,
  tokens: 15676,
  score: 100,
  reportChars: 1930,
};

const RATES = {
  liteIn: 0.3,
  liteOut: 2.5,
  groqIn: 0.05,
  groqOut: 0.08,
  imgIn: 0.5,
  imgTextOut: 3.0,
  imgImageOut: 60.0,
};

function pkrAmount(usd: number): number {
  return usd * USD_TO_PKR;
}

/** PKR first, then USD in parentheses. */
function money(usd: number, usdDigits = 5): string {
  const p = pkrAmount(usd);
  const pkrStr =
    p >= 100
      ? p.toLocaleString(undefined, {
          minimumFractionDigits: 0,
          maximumFractionDigits: 0,
        })
      : p >= 1
        ? p.toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })
        : p >= 0.01
          ? p.toFixed(2)
          : p.toFixed(4);
  return `Rs ${pkrStr} ($${usd.toFixed(usdDigits)})`;
}

function moneyLoose(usd: number): string {
  const digits = usd >= 1 ? 2 : usd >= 0.01 ? 4 : 5;
  return money(usd, digits);
}

const STEPS = [
  {
    step: "1. Quality",
    model: "gemini-3.5-flash-lite",
    in: 3475,
    out: 114,
    formula: "(3475/1e6)×$0.30 + (114/1e6)×$2.50",
    cost: 0.0013275,
  },
  {
    step: "2. Detection",
    model: "gemini-3.5-flash-lite",
    in: 5395,
    out: 674,
    formula: "(5395/1e6)×$0.30 + (674/1e6)×$2.50",
    cost: 0.0033035,
  },
  {
    step: "3. Explanation / report",
    model: "gemini-3.5-flash-lite",
    in: 843,
    out: 557,
    formula: "(843/1e6)×$0.30 + (557/1e6)×$2.50",
    cost: 0.0016454,
  },
  {
    step: "4. Simulation",
    model: "gemini-3.1-flash-image",
    in: 2089,
    out: 1561,
    formula: "(2089/1e6)×$0.50 + (441/1e6)×$3.00 + (1120/1e6)×$60.00",
    cost: 0.0695675,
  },
  {
    step: "5. Chat #1",
    model: "groq/llama-3.1-8b-instant",
    in: 1090,
    out: 208,
    formula: "(1090/1e6)×$0.05 + (208/1e6)×$0.08",
    cost: 0.00007114,
  },
  {
    step: "6. Chat #2",
    model: "groq/llama-3.1-8b-instant",
    in: 1093,
    out: 106,
    formula: "(1093/1e6)×$0.05 + (106/1e6)×$0.08",
    cost: 0.00006313,
  },
  {
    step: "7. Chat #3",
    model: "groq/llama-3.1-8b-instant",
    in: 1092,
    out: 78,
    formula: "(1092/1e6)×$0.05 + (78/1e6)×$0.08",
    cost: 0.00006084,
  },
  {
    step: "8. Chat #4",
    model: "groq/llama-3.1-8b-instant",
    in: 1092,
    out: 85,
    formula: "(1092/1e6)×$0.05 + (85/1e6)×$0.08",
    cost: 0.0000614,
  },
  {
    step: "9. Chat #5",
    model: "groq/llama-3.1-8b-instant",
    in: 1094,
    out: 165,
    formula: "(1094/1e6)×$0.05 + (165/1e6)×$0.08",
    cost: 0.0000679,
  },
];

const CATEGORY_COST = [
  { label: "Simulation", value: SIM_COST },
  { label: "Detection", value: 0.0033035 },
  { label: "Explanation", value: 0.0016454 },
  { label: "Quality", value: 0.0013275 },
  { label: "Chat (5 msgs, Groq)", value: CHAT_COST },
];

const MONTHLY = [
  { sessions: "100", cost: SESSION_COST * 100 },
  { sessions: "500", cost: SESSION_COST * 500 },
  { sessions: "1,000", cost: SESSION_COST * 1000 },
  { sessions: "5,000", cost: SESSION_COST * 5000 },
  { sessions: "10,000", cost: SESSION_COST * 10000 },
];

export default function SessionCostBadTeeth() {
  const simShare = ((SIM_COST / SESSION_COST) * 100).toFixed(1);
  const chatSave = OLD_CHAT_COST - CHAT_COST;
  const simSave = SIM_COST - QWEN_SIM_COST;

  return (
    <Stack gap={24} style={{ padding: 24, maxWidth: 980 }}>
      <Stack gap={8}>
        <H1>Max-session LLM cost — complex teeth case</H1>
        <Text tone="secondary">
          Live remeasure {MEASURED_AT}: oral changes 2.jpg (1280px) ×3 views + report + Gemini
          simulation + 5 Groq chats. Score {SCORE}/100. All in/out tokens from live API usage.
        </Text>
        <Text tone="tertiary" style={{ fontSize: 12 }}>
          Models: gemini-3.5-flash-lite · gemini-3.1-flash-image · groq/llama-3.1-8b-instant · FX: 1
          USD = {USD_TO_PKR} PKR
        </Text>
      </Stack>

      <Grid columns={4} gap={12}>
        <Stat
          label="Cost / max session (Gemini sim)"
          value={money(SESSION_COST)}
          tone="warning"
        />
        <Stat
          label="If Qwen sim instead"
          value={money(SESSION_COST_QWEN)}
          tone="success"
        />
        <Stat label="Smile score" value={`${SCORE}/100`} />
        <Stat label="Total tokens" value={TOTAL_TOKENS.toLocaleString()} />
      </Grid>

      <Callout tone="info" title="Just remeasured (live APIs)">
        Quality/detection/explanation/simulation = Gemini usage_metadata. Chat = real Groq prompt +
        completion tokens (not Gemini tokens repriced). Session {money(SESSION_COST)} vs prior
        canvas {money(PREV_SESSION_COST)} — sim input rose (longer edit prompt: 810 → 2,089 in) so
        sim is now {money(SIM_COST)} vs prior {money(PREV_SIM_COST)}.
      </Callout>

      <Callout tone="info" title="Chat billed as Groq (live)">
        5× llama-3.1-8b-instant on Groq. Chat total {money(CHAT_COST)} vs historical Gemini Flash-Lite
        chats ~{money(OLD_CHAT_COST)} (saves ~{money(chatSave)}/session on chat alone).
      </Callout>

      <Callout tone="warning" title="If we change simulation to Qwen (WaveSpeed)">
        Not applied to headline. WaveSpeed qwen-image/edit-2511 ≈ {money(QWEN_SIM_COST, 2)}/edit
        (confirmed on your usage: avg $0.0200). Sim {money(SIM_COST)} → {money(QWEN_SIM_COST, 2)}.
        Max session {money(SESSION_COST)} → {money(SESSION_COST_QWEN)}. 100 sessions:{" "}
        {moneyLoose(SESSION_COST * 100)} → {moneyLoose(SESSION_COST_QWEN * 100)} (saves ~
        {moneyLoose(simSave * 100)}).
      </Callout>

      <Callout tone="warning" title="Simulation still dominates (Gemini path)">
        Simulation is {money(SIM_COST)} of {money(SESSION_COST)} (~{simShare}%). Image out still
        1,120 tokens @ $60/1M = {money(0.0672, 4)}.
      </Callout>

      <Grid columns={2} gap={16}>
        <Card>
          <CardHeader>Cost share by stage (USD chart · PKR = ×{USD_TO_PKR})</CardHeader>
          <CardBody>
            <PieChart data={CATEGORY_COST} donut />
            <Text tone="tertiary" style={{ fontSize: 12, marginTop: 8 }}>
              Live measure · in {INPUT_TOKENS.toLocaleString()} / out {OUTPUT_TOKENS.toLocaleString()}{" "}
              · report {REPORT_CHARS.toLocaleString()} chars
            </Text>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>Monthly API cost if every session is this max path (USD)</CardHeader>
          <CardBody>
            <BarChart
              categories={MONTHLY.map((m) => m.sessions)}
              series={[
                {
                  name: "Monthly API cost (USD)",
                  data: MONTHLY.map((m) => Number(m.cost.toFixed(4))),
                },
              ]}
              valuePrefix="$"
              showValues
            />
            <Text tone="tertiary" style={{ fontSize: 12, marginTop: 8 }}>
              X: max sessions/month · Y: USD · 100 ≈ {moneyLoose(SESSION_COST * 100)}
            </Text>
          </CardBody>
        </Card>
      </Grid>

      <Stack gap={8}>
        <H2>Step-by-step calculation (live tokens)</H2>
        <Text tone="secondary">
          Flash-Lite {money(0.3, 2)} / {money(2.5, 2)} per 1M. Image: {money(0.5, 2)} in /{" "}
          {money(3.0, 2)} text / {money(60.0, 2)} image out. Groq chat {money(0.05, 2)} /{" "}
          {money(0.08, 2)} per 1M.
        </Text>
        <Table
          headers={["Step", "Model", "In", "Out", "Formula", "Cost (PKR / USD)"]}
          columnAlign={["left", "left", "right", "right", "left", "right"]}
          rows={STEPS.map((s) => [
            s.step,
            s.model,
            s.in.toLocaleString(),
            s.out.toLocaleString(),
            s.formula,
            money(s.cost, 6),
          ])}
          rowTone={STEPS.map((s) =>
            s.step.includes("Simulation")
              ? "warning"
              : s.step.includes("Chat")
                ? "info"
                : undefined
          )}
        />
      </Stack>

      <Card>
        <CardHeader>Session total arithmetic</CardHeader>
        <CardBody>
          <Stack gap={6}>
            <Text>
              Sum = $0.0013275 + $0.0033035 + $0.0016454 + $0.0695675 + $0.00007114 + $0.00006313 +
              $0.00006084 + $0.0000614 + $0.0000679
            </Text>
            <Text weight="semibold">= {money(SESSION_COST, 7)} per max session</Text>
            <Divider />
            <H3>Chat (Groq, live) vs previous Gemini chat</H3>
            <Text>Previous 5× Flash-Lite chats: {money(OLD_CHAT_COST, 7)}</Text>
            <Text>Now 5× Groq 8B Instant (live usage): {money(CHAT_COST, 7)}</Text>
            <Text weight="semibold">Saved {money(chatSave, 7)} / session on chat alone</Text>
            <Divider />
            <H3>Simulation (Gemini) vs if we change to Qwen</H3>
            <Text>Live Gemini Flash Image: {money(SIM_COST, 7)}</Text>
            <Text>
              If WaveSpeed Qwen (~{money(QWEN_SIM_COST, 2)}/edit): {money(QWEN_SIM_COST, 7)}
            </Text>
            <Text weight="semibold">
              Would save {money(simSave, 7)} / session on simulation alone
            </Text>
            <Text>
              Max session if changed: {money(SESSION_COST, 7)} → {money(SESSION_COST_QWEN, 7)}
            </Text>
            <Text>
              100 max sessions/month: {moneyLoose(SESSION_COST * 100)} →{" "}
              {moneyLoose(SESSION_COST_QWEN * 100)} (saves {moneyLoose(simSave * 100)})
            </Text>
            <Divider />
            <H3>Simulation breakdown (live Gemini)</H3>
            <Text>Input: 2089 × $0.50 / 1M = {money(0.0010445, 6)}</Text>
            <Text>Text residual: 441 × $3.00 / 1M = {money(0.001323, 6)}</Text>
            <Text>Image output (1K): 1120 × $60.00 / 1M = {money(0.0672, 6)}</Text>
            <Text weight="semibold">Simulation subtotal = {money(SIM_COST, 6)}</Text>
          </Stack>
        </CardBody>
      </Card>

      <Stack gap={8}>
        <H2>Monthly projections (Gemini sim vs if Qwen sim)</H2>
        <Table
          headers={[
            "Max sessions / month",
            "Gemini sim path",
            "If Qwen sim",
            "Would save",
          ]}
          columnAlign={["left", "right", "right", "right"]}
          rows={MONTHLY.map((m) => {
            const n = Number(m.sessions.replace(/,/g, ""));
            const gemini = SESSION_COST * n;
            const qwen = SESSION_COST_QWEN * n;
            return [
              m.sessions,
              moneyLoose(gemini),
              moneyLoose(qwen),
              moneyLoose(gemini - qwen),
            ];
          })}
        />
        <Text tone="tertiary" style={{ fontSize: 12 }}>
          Per-session: Gemini {money(SESSION_COST)} · Qwen {money(SESSION_COST_QWEN)}. Source:{" "}
          docs/session_cost_measured_rerun.json
        </Text>
      </Stack>

      <Stack gap={8}>
        <H2>Clean vs this remeasure</H2>
        <Table
          headers={["Metric", "Clean (100/100)", "Complex remeasure", "Delta"]}
          columnAlign={["left", "right", "right", "right"]}
          rows={[
            [
              "Session cost (Gemini sim)",
              money(CLEAN.cost),
              money(SESSION_COST),
              `+${money(SESSION_COST - CLEAN.cost)}`,
            ],
            [
              "Total tokens",
              CLEAN.tokens.toLocaleString(),
              TOTAL_TOKENS.toLocaleString(),
              `+${(TOTAL_TOKENS - CLEAN.tokens).toLocaleString()}`,
            ],
            [
              "Report chars",
              CLEAN.reportChars.toLocaleString(),
              REPORT_CHARS.toLocaleString(),
              `+${(REPORT_CHARS - CLEAN.reportChars).toLocaleString()}`,
            ],
            ["Chat provider", "Gemini hist.", "Groq live", "measured"],
            ["Chat cost (5 msgs)", "~Gemini hist.", money(CHAT_COST), "—"],
            ["Simulation cost", money(0.06867), money(SIM_COST), "live"],
            [
              "If Qwen sim session",
              "—",
              money(SESSION_COST_QWEN),
              `−${money(simSave)} sim`,
            ],
            [
              "100 users / month (Gemini)",
              moneyLoose(CLEAN.cost * 100),
              moneyLoose(SESSION_COST * 100),
              "—",
            ],
            [
              "100 users / month (if Qwen)",
              "—",
              moneyLoose(SESSION_COST_QWEN * 100),
              `−${moneyLoose(simSave * 100)}`,
            ],
          ]}
        />
      </Stack>

      <Callout tone="info" title="How this was measured">
        Script: backend/measure_session_cost.py. Image: frontend/assets/oral changes 2.jpg upscaled
        to 1280 long-edge, sent as front/left/right. Quality passed. Prefer ~
        {money(SESSION_COST, 3)}/session for Gemini-sim budgeting; ~
        {money(SESSION_COST_QWEN, 3)} if WaveSpeed Qwen sim. PKR uses 1 USD = {USD_TO_PKR} PKR.
      </Callout>

      <Row gap={16}>
        <Stack gap={4} style={{ flex: 1 }}>
          <H3>Flash-Lite rates</H3>
          <Text>Input {money(RATES.liteIn, 2)}/1M</Text>
          <Text>Output {money(RATES.liteOut, 2)}/1M</Text>
        </Stack>
        <Stack gap={4} style={{ flex: 1 }}>
          <H3>Groq chat rates</H3>
          <Text>llama-3.1-8b-instant</Text>
          <Text>Input {money(RATES.groqIn, 2)}/1M</Text>
          <Text>Output {money(RATES.groqOut, 2)}/1M</Text>
        </Stack>
        <Stack gap={4} style={{ flex: 1 }}>
          <H3>Flash Image rates</H3>
          <Text>Input {money(RATES.imgIn, 2)}/1M</Text>
          <Text>Text out {money(RATES.imgTextOut, 2)}/1M</Text>
          <Text>Image out {money(RATES.imgImageOut, 2)}/1M</Text>
        </Stack>
        <Stack gap={4} style={{ flex: 1 }}>
          <H3>Qwen sim (if changed)</H3>
          <Text>WaveSpeed edit-2511</Text>
          <Text>~{money(QWEN_SIM_COST, 2)} / edit</Text>
          <Text>Session → {money(SESSION_COST_QWEN)}</Text>
        </Stack>
      </Row>

      <Spacer />
    </Stack>
  );
}
