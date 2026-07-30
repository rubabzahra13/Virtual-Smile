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

const MEASURED_AT = "2026-07-28T17:18:15Z";
const SESSION_COST = 0.0772452;
const TOTAL_TOKENS = 19137;
const INPUT_TOKENS = 16044;
const OUTPUT_TOKENS = 3093;
const SCORE = 66;

const CLEAN = {
  cost: 0.0743488,
  tokens: 15676,
  score: 100,
  reportChars: 1930,
};

const RATES = {
  liteIn: 0.3,
  liteOut: 2.5,
  imgIn: 0.5,
  imgTextOut: 3.0,
  imgImageOut: 60.0,
};

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
    out: 662,
    formula: "(5395/1e6)×$0.30 + (662/1e6)×$2.50",
    cost: 0.0032735,
  },
  {
    step: "3. Explanation / report",
    model: "gemini-3.5-flash-lite",
    in: 854,
    out: 545,
    formula: "(854/1e6)×$0.30 + (545/1e6)×$2.50",
    cost: 0.0016187,
  },
  {
    step: "4. Simulation",
    model: "gemini-3.1-flash-image-preview",
    in: 810,
    out: 1395,
    formula: "(810/1e6)×$0.50 + (275/1e6)×$3.00 + (1120/1e6)×$60.00",
    cost: 0.06843,
  },
  {
    step: "5. Chat #1",
    model: "gemini-3.5-flash-lite",
    in: 1100,
    out: 80,
    formula: "(1100/1e6)×$0.30 + (80/1e6)×$2.50",
    cost: 0.00053,
  },
  {
    step: "6. Chat #2",
    model: "gemini-3.5-flash-lite",
    in: 1103,
    out: 88,
    formula: "(1103/1e6)×$0.30 + (88/1e6)×$2.50",
    cost: 0.0005509,
  },
  {
    step: "7. Chat #3",
    model: "gemini-3.5-flash-lite",
    in: 1102,
    out: 69,
    formula: "(1102/1e6)×$0.30 + (69/1e6)×$2.50",
    cost: 0.0005031,
  },
  {
    step: "8. Chat #4",
    model: "gemini-3.5-flash-lite",
    in: 1101,
    out: 58,
    formula: "(1101/1e6)×$0.30 + (58/1e6)×$2.50",
    cost: 0.0004753,
  },
  {
    step: "9. Chat #5",
    model: "gemini-3.5-flash-lite",
    in: 1104,
    out: 82,
    formula: "(1104/1e6)×$0.30 + (82/1e6)×$2.50",
    cost: 0.0005362,
  },
];

const CATEGORY_COST = [
  { label: "Simulation", value: 0.06843 },
  { label: "Detection", value: 0.0032735 },
  { label: "Chat (5 msgs)", value: 0.0025955 },
  { label: "Explanation", value: 0.0016187 },
  { label: "Quality", value: 0.0013275 },
];

const MONTHLY = [
  { sessions: "100", cost: 7.7245 },
  { sessions: "500", cost: 38.6226 },
  { sessions: "1,000", cost: 77.2452 },
  { sessions: "5,000", cost: 386.226 },
  { sessions: "10,000", cost: 772.452 },
];

export default function SessionCostBadTeeth() {
  return (
    <Stack gap={24} style={{ padding: 24, maxWidth: 980 }}>
      <Stack gap={8}>
        <H1>Max-session LLM cost — complex teeth case</H1>
        <Text tone="secondary">
          Live re-measure using oral changes 2.jpg (upscaled to 1280px, used as front/left/right).
          Full path: 3 images + report + simulation + 5 chats. Score {SCORE}/100.
        </Text>
        <Text tone="tertiary" style={{ fontSize: 12 }}>
          Measured {MEASURED_AT} · Source: ai.google.dev/gemini-api/docs/pricing · Case: bad teeth
        </Text>
      </Stack>

      <Grid columns={4} gap={12}>
        <Stat label="Cost / max session" value={`$${SESSION_COST.toFixed(5)}`} tone="warning" />
        <Stat label="Total tokens" value={TOTAL_TOKENS.toLocaleString()} />
        <Stat label="Smile score" value={`${SCORE}/100`} />
        <Stat label="Report length" value="4,314 chars" />
      </Grid>

      <Callout tone="info" title="vs clean 100/100 case">
        Clean case was ${CLEAN.cost.toFixed(5)} ({CLEAN.tokens.toLocaleString()} tokens, score{" "}
        {CLEAN.score}). Complex case is ${SESSION_COST.toFixed(5)} — only about $
        {(SESSION_COST - CLEAN.cost).toFixed(4)} more (+
        {(((SESSION_COST - CLEAN.cost) / CLEAN.cost) * 100).toFixed(1)}%). Report grew{" "}
        {CLEAN.reportChars} → 4,314 chars; simulation still dominates (~88.6%).
      </Callout>

      <Callout tone="warning" title="Simulation still dominates">
        Treatment simulation is $0.06843 of $0.07725 (~88.6%). Detection + explanation + chat rose
        with more findings, but stay small versus image generation.
      </Callout>

      <Grid columns={2} gap={16}>
        <Card>
          <CardHeader>Cost share by stage (USD)</CardHeader>
          <CardBody>
            <PieChart data={CATEGORY_COST} donut />
            <Text tone="tertiary" style={{ fontSize: 12, marginTop: 8 }}>
              Source: live Gemini usage_metadata · bad-teeth measured run
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
                  data: MONTHLY.map((m) => m.cost),
                },
              ]}
              valuePrefix="$"
              showValues
            />
            <Text tone="tertiary" style={{ fontSize: 12, marginTop: 8 }}>
              X: max sessions/month · Y: USD · Formula: session_cost × sessions
            </Text>
          </CardBody>
        </Card>
      </Grid>

      <Stack gap={8}>
        <H2>Step-by-step calculation</H2>
        <Text tone="secondary">
          Rates used: Flash-Lite $0.30 / $2.50 per 1M in/out. Image model $0.50 input, $3.00 text
          residual, $60.00 image output per 1M tokens.
        </Text>
        <Table
          headers={["Step", "Model", "In", "Out", "Formula", "Cost (USD)"]}
          columnAlign={["left", "left", "right", "right", "left", "right"]}
          rows={STEPS.map((s) => [
            s.step,
            s.model,
            s.in.toLocaleString(),
            s.out.toLocaleString(),
            s.formula,
            `$${s.cost.toFixed(6)}`,
          ])}
          rowTone={STEPS.map((s) => (s.step.includes("Simulation") ? "warning" : undefined))}
        />
      </Stack>

      <Card>
        <CardHeader>Session total arithmetic</CardHeader>
        <CardBody>
          <Stack gap={6}>
            <Text>
              Sum of 9 call costs = $0.0013275 + $0.0032735 + $0.0016187 + $0.06843 + $0.0005300 +
              $0.0005509 + $0.0005031 + $0.0004753 + $0.0005362
            </Text>
            <Text weight="semibold">= ${SESSION_COST.toFixed(7)} per max session</Text>
            <Divider />
            <H3>Simulation breakdown</H3>
            <Text>Input: 810 × $0.50 / 1M = $0.000405</Text>
            <Text>Residual non-image candidates: 275 × $3.00 / 1M = $0.000825</Text>
            <Text>Image output (1K class): 1120 × $60.00 / 1M = $0.067200</Text>
            <Text weight="semibold">Simulation subtotal = $0.068430</Text>
          </Stack>
        </CardBody>
      </Card>

      <Stack gap={8}>
        <H2>Monthly projections (budget with complex case)</H2>
        <Table
          headers={["Max sessions / month", "LLM API cost (USD)", "Per-session"]}
          columnAlign={["left", "right", "right"]}
          rows={MONTHLY.map((m) => [
            m.sessions,
            `$${m.cost.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 4,
            })}`,
            `$${SESSION_COST.toFixed(5)}`,
          ])}
        />
      </Stack>

      <Stack gap={8}>
        <H2>Clean vs complex comparison</H2>
        <Table
          headers={["Metric", "Clean (100/100)", "Complex (66/100)", "Delta"]}
          columnAlign={["left", "right", "right", "right"]}
          rows={[
            [
              "Session cost",
              `$${CLEAN.cost.toFixed(5)}`,
              `$${SESSION_COST.toFixed(5)}`,
              `+$${(SESSION_COST - CLEAN.cost).toFixed(5)}`,
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
              "4,314",
              "+2,384",
            ],
            ["Detection out tokens", "244", "662", "+418"],
            ["Explanation out tokens", "98", "545", "+447"],
            ["Chat in tokens (5 msgs)", "3,105", "5,510", "+2,405"],
            ["Simulation cost", "$0.06867", "$0.06843", "~same"],
            ["100 users / month", "$7.43", "$7.72", "+$0.29"],
          ]}
        />
      </Stack>

      <Callout tone="info" title="How this was measured">
        Used frontend/assets/oral changes 2.jpg, upscaled to long-edge 1280 and sent as all 3
        views. Same production prompts/models. Raw usage_metadata recorded for each call. For
        budgeting, prefer this complex-case number (~$0.077/session) over the clean 100/100 run.
        Re-run script against this image path as needed.
      </Callout>

      <Row gap={16}>
        <Stack gap={4} style={{ flex: 1 }}>
          <H3>Flash-Lite rates</H3>
          <Text>Input ${RATES.liteIn}/1M</Text>
          <Text>Output ${RATES.liteOut}/1M</Text>
        </Stack>
        <Stack gap={4} style={{ flex: 1 }}>
          <H3>Flash Image rates</H3>
          <Text>Input ${RATES.imgIn}/1M</Text>
          <Text>Text/thinking out ${RATES.imgTextOut}/1M</Text>
          <Text>Image out ${RATES.imgImageOut}/1M</Text>
        </Stack>
      </Row>

      <Spacer />
    </Stack>
  );
}
