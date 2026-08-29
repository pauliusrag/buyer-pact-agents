/**
 * Demand bucketing SPA.
 *
 * Takes a JSON object of {customer: "what they want"}, sends it to the grouping
 * agents, and renders what came back. No framework, no build step: the point is to
 * make the agents' work legible, not to demonstrate a frontend stack.
 */

const EXAMPLE = {
  "anna@example.com":
    "I want a smart ring that tracks my sleep and HRV. No monthly subscription please. Under €300.",
  "ben@example.com":
    "Looking for a sleep tracking ring, works with my iPhone, at least a week of battery. Max €320.",
  "cara@example.com":
    "A ring for sleep and recovery, I refuse to pay a subscription. Around €280.",
  "dmitri@example.com":
    "I want the Oura ring, sleep and temperature tracking, up to €380.",
  "eva@example.com":
    "Fitness band with GPS and heart rate for running, waterproof, around €200.",
};

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/** Accept the documented shape, and the obvious near-misses, without guessing. */
function parseInput(raw) {
  const text = raw.trim();
  if (!text) throw new Error("Paste some customer requests first.");

  let value;
  try {
    value = JSON.parse(text);
  } catch (cause) {
    throw new Error(`That is not valid JSON — ${cause.message}`);
  }

  let users;
  if (Array.isArray(value)) {
    users = value;
  } else if (value && typeof value === "object") {
    users = Array.isArray(value.users) ? value.users : value.users ?? value;
  } else {
    throw new Error("Expected an object of {customer: request} pairs.");
  }

  const count = Array.isArray(users) ? users.length : Object.keys(users).length;
  if (!count) throw new Error("No customers found in that JSON.");
  return users;
}

async function runAgents(users) {
  const response = await fetch("/api/v1/demand/group", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ users }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`The agents could not run (${response.status}). ${detail.slice(0, 300)}`);
  }
  return response.json();
}

/* -------------------------------------------------------------------------- */
/* Rendering                                                                   */
/* -------------------------------------------------------------------------- */

function renderBucket(group) {
  const card = el("div", "card bucket");

  card.append(el("h3", null, group.label));

  const people = group.size === 1 ? "1 customer" : `${group.size} customers`;
  const ceiling =
    group.price_ceiling != null
      ? ` · group ceiling ${group.currency} ${group.price_ceiling}`
      : "";
  card.append(el("p", "meta", `${people}${ceiling} · ${group.category}`));

  const chips = el("div", "chips");
  for (const member of group.member_user_ids) chips.append(el("span", "chip", member));
  card.append(chips);

  if (group.requirements.length) {
    const list = el("ul", "reqs");
    for (const requirement of group.requirements) list.append(el("li", null, requirement));
    card.append(list);
  }

  card.append(el("p", "why", group.explanation));

  if (group.members.length) {
    const details = el("details");
    details.append(el("summary", null, "Why each customer is in this group"));
    for (const member of group.members) {
      const row = el("div", "member");
      row.append(el("b", null, member.user_id));
      row.append(el("p", null, member.explanation));
      if (member.inherited_requirements.length) {
        row.append(
          el(
            "p",
            null,
            `Also getting, because other members require it: ${member.inherited_requirements.join(
              ", ",
            )}`,
          ),
        );
      }
      details.append(row);
    }
    card.append(details);
  }

  return card;
}

function renderParsed(parsed) {
  const body = $("parsed-table").querySelector("tbody");
  body.replaceChildren();
  for (const entry of parsed) {
    const row = el("tr");
    row.append(el("td", null, entry.user_id));
    row.append(el("td", "said", entry.prompt || entry.summary));

    const understood = el("td");
    if (entry.hard_requirements.length) {
      for (const requirement of entry.hard_requirements) {
        understood.append(el("code", null, requirement));
        understood.append(document.createTextNode(" "));
      }
    } else {
      understood.append(el("span", "empty", "nothing stated as a hard requirement"));
    }
    row.append(understood);

    row.append(el("td", null, entry.max_budget != null ? `≤ ${entry.max_budget}` : "—"));
    body.append(row);
  }
}

function renderTrace(trace) {
  const list = $("trace");
  list.replaceChildren();
  for (const step of trace) {
    const item = el("li");
    item.append(el("span", "who", step.agent));
    const what = el("span", "what", step.message);
    if (step.decision && step.decision !== step.message) {
      what.append(el("em", null, step.decision));
    }
    item.append(what);
    list.append(item);
  }
}

function renderWarnings(warnings) {
  const host = $("warnings");
  host.replaceChildren();
  if (!warnings.length) return;
  const banner = el("div", "banner");
  banner.append(el("strong", null, "Notes from the agents: "));
  banner.append(document.createTextNode(warnings.join(" · ")));
  host.append(banner);
}

function render(result, customerCount) {
  $("n-customers").textContent = customerCount;
  $("n-groups").textContent = result.groups.length;
  $("n-largest").textContent = result.groups.reduce((max, g) => Math.max(max, g.size), 0);
  $("n-engine").textContent = result.engine === "deterministic" ? "rules" : result.engine;

  renderWarnings(result.warnings ?? []);

  const host = $("buckets");
  host.replaceChildren();
  const ordered = [...result.groups].sort((a, b) => b.size - a.size);
  for (const group of ordered) host.append(renderBucket(group));

  renderParsed(result.parsed ?? []);
  renderTrace(result.trace ?? []);
  $("results").hidden = false;
}

/* -------------------------------------------------------------------------- */
/* Wiring                                                                      */
/* -------------------------------------------------------------------------- */

function showError(message) {
  const box = $("err");
  box.textContent = message;
  box.hidden = false;
}

function clearError() {
  $("err").hidden = true;
}

$("sample").addEventListener("click", () => {
  $("input").value = JSON.stringify(EXAMPLE, null, 2);
  clearError();
});

$("clear").addEventListener("click", () => {
  $("input").value = "";
  $("results").hidden = true;
  clearError();
});

$("run").addEventListener("click", async () => {
  clearError();
  const button = $("run");
  const status = $("status");
  let users;
  try {
    users = parseInput($("input").value);
  } catch (error) {
    showError(error.message);
    return;
  }

  const count = Array.isArray(users) ? users.length : Object.keys(users).length;
  button.disabled = true;
  status.textContent = `Reading ${count} request${count === 1 ? "" : "s"}…`;
  const started = performance.now();

  try {
    const result = await runAgents(users);
    render(result, count);
    status.textContent = `Grouped in ${Math.round(performance.now() - started)} ms`;
  } catch (error) {
    showError(error.message);
    status.textContent = "";
  } finally {
    button.disabled = false;
  }
});

// Start with the example in place, so the page is never a blank prompt.
$("input").value = JSON.stringify(EXAMPLE, null, 2);
