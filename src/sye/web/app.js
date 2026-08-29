/**
 * Stepped walkthrough of the demand agents.
 *
 * One step at a time, each revealed by finishing the one before it: requests →
 * understood → grouped → products. The data all comes from one call to
 * /api/v1/demand/research; the steps control how much of it is on screen.
 */

const EXAMPLE = {
  "anna@example.com":
    "I want a smart ring that tracks my sleep and HRV. No monthly subscription please. Under €300.",
  "ben@example.com":
    "Looking for a sleep tracking ring, works with my iPhone, at least a week of battery. Max €320.",
  "cara@example.com":
    "A ring for sleep and recovery, I refuse to pay a subscription. Around €280.",
  "dmitri@example.com": "I want the Oura ring, sleep and temperature tracking, up to €380.",
  "eva@example.com":
    "Fitness band with GPS and heart rate for running, waterproof, around €200.",
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

let data = null;

/* ---------------------------------------------------------------- steps -- */

function showStep(n) {
  for (const section of document.querySelectorAll(".step")) section.classList.remove("active");
  $(`s${n}`).classList.add("active");

  for (const pip of document.querySelectorAll(".pip")) {
    const index = Number(pip.dataset.pip);
    pip.classList.toggle("done", index < n);
    pip.classList.toggle("now", index === n);
    pip.classList.toggle("future", index > n);
  }
  window.scrollTo({ top: 0, behavior: n === 1 ? "auto" : "smooth" });
}

function countUp(node, target) {
  const duration = 550;
  const started = performance.now();
  const tick = (now) => {
    const progress = Math.min((now - started) / duration, 1);
    node.textContent = Math.round(target * (1 - Math.pow(1 - progress, 3)));
    if (progress < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/* ------------------------------------------------------------- fetching -- */

function parseInput(raw) {
  const text = raw.trim();
  if (!text) throw new Error("Paste some customer requests first.");
  let value;
  try {
    value = JSON.parse(text);
  } catch (cause) {
    throw new Error(`That is not valid JSON — ${cause.message}`);
  }
  const users =
    Array.isArray(value) || !value.users ? value : value.users;
  const count = Array.isArray(users) ? users.length : Object.keys(users ?? {}).length;
  if (!count) throw new Error("No customers found in that JSON.");
  return { users, count };
}

async function research(users, live) {
  const response = await fetch("/api/v1/demand/research", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ users, live }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`The agents could not run (${response.status}). ${detail.slice(0, 240)}`);
  }
  return response.json();
}

/* ------------------------------------------------------------ rendering -- */

function initials(id) {
  const name = id.split("@")[0].replace(/[._-]+/g, " ");
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

function renderGroups(groups) {
  const host = $("groups");
  host.replaceChildren();

  for (const group of [...groups].sort((a, b) => b.size - a.size)) {
    const card = el("div", "card group");

    const top = el("div", "top");
    const heading = el("div");
    heading.append(el("h3", null, group.label));
    heading.append(
      el(
        "p",
        "note",
        group.price_ceiling != null
          ? `ceiling ${group.currency} ${group.price_ceiling}`
          : "no stated ceiling",
      ),
    );
    top.append(heading);

    const stat = el("div", "big");
    const number = el("span", null, "0");
    stat.append(number);
    stat.append(el("small", null, group.size === 1 ? "customer" : "customers"));
    top.append(stat);
    card.append(top);
    countUp(number, group.size);

    const members = el("div", "members");
    const SHOWN = 16;
    for (const id of group.member_user_ids.slice(0, SHOWN)) {
      const avatar = el("div", "avatar", initials(id));
      avatar.title = id;
      members.append(avatar);
    }
    if (group.member_user_ids.length > SHOWN) {
      const rest = el("div", "avatar rest", `+${group.member_user_ids.length - SHOWN}`);
      rest.title = group.member_user_ids.slice(SHOWN).join(", ");
      members.append(rest);
    }
    card.append(members);

    const chips = el("div", "chips");
    for (const requirement of group.requirements) {
      const match = requirement.match(/^(.*?)\s*\(required by (\d+)\/(\d+) buyers\)$/);
      if (match) {
        const chip = el("span", "chip count", match[1]);
        chip.dataset.count = `${match[2]}/${match[3]}`;
        chips.append(chip);
      } else {
        chips.append(el("span", "chip", requirement));
      }
    }
    card.append(chips);

    if (group.members.length) {
      const why = el("details", "why");
      why.append(el("summary", null, "Why each customer is here"));
      for (const member of group.members) {
        const row = el("div", "whyrow");
        row.append(el("b", null, member.user_id));
        row.append(el("p", null, member.explanation));
        if (member.inherited_requirements.length) {
          row.append(
            el("p", null, `Inherited from others: ${member.inherited_requirements.join(", ")}`),
          );
        }
        why.append(row);
      }
      card.append(why);
    }

    host.append(card);
  }
}

function productCard(candidate, bucket) {
  const card = el("div", "product");
  if (candidate.product_id === bucket.winner_id) card.classList.add("win");
  if (candidate.verdict === "rejected") card.classList.add("out");

  card.append(
    el(
      "span",
      `verdict v-${candidate.verdict}`,
      candidate.product_id === bucket.winner_id ? "best fit" : candidate.verdict.replace("_", " "),
    ),
  );
  card.append(el("div", "name", candidate.name));
  if (candidate.price_implausible) {
    card.append(el("span", "flag", "price looks too low to trust"));
  }

  if (candidate.price != null) {
    const price = el("div", "price", `${candidate.price}`);
    price.append(el("small", null, candidate.currency ?? ""));
    card.append(price);
  } else {
    card.append(el("div", "price", "—"));
  }

  const bar = el("div", "bar");
  const fill = el("i");
  bar.append(fill);
  card.append(bar);
  requestAnimationFrame(() => {
    fill.style.width = `${(candidate.passed / Math.max(candidate.total, 1)) * 100}%`;
  });
  card.append(
    el("p", "reason", `${candidate.passed}/${candidate.total} requirements · ${candidate.reason}`),
  );

  if (candidate.sources.length) {
    const src = el("div", "srcs");
    src.append(document.createTextNode("source: "));
    const link = el("a", null, sourceLabel(candidate.sources[0]));
    link.href = candidate.sources[0];
    link.target = "_blank";
    link.rel = "noreferrer";
    src.append(link);
    card.append(src);
  }
  return card;
}

function sourceLabel(url) {
  try {
    return new URL(url).hostname.replace("www.", "");
  } catch {
    return url.replace("fixture://", "local catalogue · ");
  }
}

function renderSuppliers(research) {
  const host = $("suppliers");
  host.replaceChildren();
  for (const bucket of research) {
    if (!bucket.suppliers.length) continue;
    const card = el("div", "card");
    card.append(el("h3", null, bucket.label));
    card.append(
      el(
        "p",
        "note",
        `${bucket.demand_quantity} unit${bucket.demand_quantity === 1 ? "" : "s"} of pooled demand · ` +
          `${bucket.suppliers.length} plausible supplier${bucket.suppliers.length === 1 ? "" : "s"}`,
      ),
    );
    for (const supplier of bucket.suppliers) {
      const row = el("div", "supplier");
      const left = el("div");
      left.append(el("div", "nm", supplier.name));
      if (supplier.sources.length) {
        const link = el("a", null, sourceLabel(supplier.sources[0]));
        link.href = supplier.sources[0];
        link.target = "_blank";
        link.rel = "noreferrer";
        left.append(link);
      }
      row.append(left);
      row.append(el("span", "kind", supplier.type.replace("_", " ")));
      card.append(row);
    }
    host.append(card);
  }
  if (!host.childElementCount) {
    const card = el("div", "card");
    card.append(el("p", "empty", "No supplier was found for these groups."));
    host.append(card);
  }
}

function renderResearch(research) {
  const host = $("research");
  host.replaceChildren();

  for (const bucket of research) {
    const block = el("div", "card");
    block.append(el("h3", null, bucket.label));

    const qualified = bucket.candidates.filter((c) => c.verdict !== "rejected").length;
    block.append(
      el(
        "p",
        "note",
        `${bucket.candidates.length} candidates researched · ${qualified} fit the group`,
      ),
    );

    if (bucket.queries.length) {
      block.append(el("p", "querylabel", "What the agent asked the web"));
      block.append(el("div", "query", bucket.queries[bucket.queries.length - 1]));
    }

    const fits = bucket.candidates.filter((c) => c.verdict !== "rejected");
    const rejected = bucket.candidates.filter((c) => c.verdict === "rejected");

    const grid = el("div", "grid");
    for (const candidate of fits.length ? fits : rejected.slice(0, 3)) {
      const card = productCard(candidate, bucket);
      grid.append(card);
    }
    block.append(grid);

    if (fits.length && rejected.length) {
      const rest = el("div", "grid");
      rest.hidden = true;
      for (const candidate of rejected) rest.append(productCard(candidate, bucket));
      const toggle = el(
        "button",
        "more",
        `Show ${rejected.length} candidate${rejected.length === 1 ? "" : "s"} that did not fit`,
      );
      toggle.addEventListener("click", () => {
        rest.hidden = !rest.hidden;
        toggle.textContent = rest.hidden
          ? `Show ${rejected.length} candidate${rejected.length === 1 ? "" : "s"} that did not fit`
          : "Hide the ones that did not fit";
      });
      block.append(toggle);
      block.append(rest);
    }

    host.append(block);
  }
}

/**
 * Per-customer notes are useful in the data and useless on screen: at 500 customers
 * the same sentence repeats forty times. Group them by what they say.
 */
function renderWarnings(warnings) {
  const host = $("warnings");
  host.replaceChildren();
  if (!warnings.length) return;

  const kinds = new Map();
  for (const warning of warnings) {
    // strip the leading identifier so identical notes collapse together
    const kind = warning.replace(/^\S+?@?\S*\s/, "").replace(/\(.*?\)/g, "").trim();
    const entry = kinds.get(kind) ?? { count: 0, sample: warning };
    entry.count += 1;
    kinds.set(kind, entry);
  }

  const banner = el("div", "banner");
  const lines = [...kinds.entries()].sort((a, b) => b[1].count - a[1].count);
  for (const [kind, entry] of lines.slice(0, 3)) {
    banner.append(
      el("div", null, entry.count > 1 ? `${entry.count} customers — ${kind}` : entry.sample),
    );
  }
  if (lines.length > 3) {
    banner.append(el("div", null, `and ${lines.length - 3} other kinds of note`));
  }
  host.append(banner);
}

/* --------------------------------------------------------------- wiring -- */

function showError(message) {
  const box = $("err");
  box.textContent = message;
  box.hidden = false;
}

$("sample").addEventListener("click", () => {
  $("input").value = JSON.stringify(EXAMPLE, null, 2);
  $("err").hidden = true;
});

$("sample500").addEventListener("click", async () => {
  const button = $("sample500");
  button.disabled = true;
  button.textContent = "loading…";
  try {
    const response = await fetch("/sample-customers.json");
    $("input").value = JSON.stringify(await response.json(), null, 1);
    $("err").hidden = true;
  } catch {
    showError("Could not load the 500-customer sample.");
  } finally {
    button.disabled = false;
    button.textContent = "500 customers";
  }
});

$("run").addEventListener("click", async () => {
  $("err").hidden = true;
  let input;
  try {
    input = parseInput($("input").value);
  } catch (error) {
    showError(error.message);
    return;
  }

  const button = $("run");
  const live = $("live").checked;
  button.disabled = true;
  const started = performance.now();

  // Live web research takes the better part of a minute, so say what is happening
  // rather than showing a spinner that means nothing.
  const stages = live
    ? ["Reading the requests", "Grouping compatible demand", "Searching the live web", "Judging every candidate", "Looking for suppliers"]
    : ["Reading the requests", "Grouping compatible demand", "Checking the catalogue"];
  let stage = 0;
  const setStage = () => {
    button.replaceChildren(el("span", "spin"), document.createTextNode(` ${stages[stage]}…`));
  };
  setStage();
  const ticker = setInterval(() => {
    stage = Math.min(stage + 1, stages.length - 1);
    setStage();
  }, live ? 9000 : 400);

  try {
    data = await research(input.users, live);
    const elapsed = Math.round(performance.now() - started);

    renderWarnings(data.warnings ?? []);
    renderGroups(data.groups);
    renderResearch(data.research ?? []);
    renderSuppliers(data.research ?? []);
    countUp($("n-groups"), data.groups.length);
    $("group-note").textContent =
      data.groups.length > (data.research ?? []).length
        ? `Showing every group. The ${data.groups_researched} largest go to market research — the rest wait for more demand.`
        : "";
    $("provider-note").textContent =
      data.provider === "linkup"
        ? "Searched the live web with Linkup. Every candidate keeps the source it came from."
        : "Searched the local demo catalogue. Turn on “search the live web with Linkup” to hit the real web.";
    $("timing").textContent =
      `${input.count} requests → ${data.groups.length} groups · ` +
      `${data.groups_researched} researched in ${(elapsed / 1000).toFixed(1)}s`;

    showStep(2);
  } catch (error) {
    showError(error.message);
  } finally {
    clearInterval(ticker);
    button.disabled = false;
    button.textContent = "Read the requests";
  }
});

$("to-products").addEventListener("click", () => showStep(3));
$("to-suppliers").addEventListener("click", () => showStep(4));
for (const button of document.querySelectorAll("#restart")) {
  button.addEventListener("click", () => showStep(1));
}

$("input").value = JSON.stringify(EXAMPLE, null, 2);
