/* =====================================================================
   Prestige — OWL ontology editor - web UI (vanilla JS, no build step).
   ===================================================================== */

// ----- utilities ------------------------------------------------------

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function escapeAttr(s) { return escapeHTML(s); }
function localName(q) { return q.includes(":") ? q.split(":", 2)[1] : q; }

async function jsonFetch(url, opts) {
  const r = await fetch(url, opts);
  let data;
  try { data = await r.json(); }
  catch { data = {}; }
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}
function postJSON(url, body)  { return jsonFetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body: body ? JSON.stringify(body) : null}); }
function getJSON(url)         { return jsonFetch(url); }
async function getText(url)   { const r = await fetch(url); if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.text(); }

// ----- API ------------------------------------------------------------

const api = {
  state:       () => getJSON("/api/state"),
  tree:        () => getJSON("/api/tree"),
  entities:    () => getJSON("/api/entities"),
  entity:      (q) => getJSON("/api/entity?qname=" + encodeURIComponent(q)),
  createClass: (b) => postJSON("/api/class", b),
  rename:      (b) => postJSON("/api/rename", b),
  move:        (b) => postJSON("/api/move", b),
  del:         (b) => postJSON("/api/delete", b),
  addParent:   (b) => postJSON("/api/parent", b),
  rmParent:    (b) => postJSON("/api/unparent", b),
  annotation:  (entity, prop, text) => postJSON("/api/annotation", {entity, prop, text}),
  restriction: (b) => postJSON("/api/restriction", b),
  disjoint:    (b) => postJSON("/api/disjoint", b),
  equivalent:  (b) => postJSON("/api/equivalent", b),
  newObjProp:  (b) => postJSON("/api/object-property", b),
  newDataProp: (b) => postJSON("/api/data-property", b),
  newIndiv:    (b) => postJSON("/api/individual", b),
  rawAxiom:    (b) => postJSON("/api/raw-axiom", b),
  undo:        () => postJSON("/api/undo"),
  redo:        () => postJSON("/api/redo"),
  save:        () => postJSON("/api/save"),
  validate:    () => getJSON("/api/validate"),
  pitfalls:    () => getJSON("/api/pitfalls"),
  statistics:  () => getJSON("/api/statistics"),
  metrics:     () => getJSON("/api/metrics"),
  outline:     (root, labels=true) => getText(`/api/outline?labels=${labels}${root?`&root=${encodeURIComponent(root)}`:""}`),
  glossary:    (fmt) => getText(`/api/glossary?fmt=${fmt}`),
  diff:        (file) => { const fd = new FormData(); fd.append("file", file); return jsonFetch("/api/diff", {method:"POST", body: fd}); },
  queryTypes:  () => getJSON("/api/query-types"),
  query:       (b) => postJSON("/api/query", b),
  reasoners:   () => getJSON("/api/reasoners"),
  reason:      (b) => postJSON("/api/reason", b),
  createBorn:  (b) => postJSON("/api/create-born", b),
  sparql:      (q) => postJSON("/api/sparql", {query: q}),
  graphEngines:() => getJSON("/api/graph-engines"),
  load:        (file) => {
    const fd = new FormData(); fd.append("file", file);
    return jsonFetch("/api/load", {method: "POST", body: fd});
  },
  objAssert:   (b) => postJSON("/api/object-property-assertion", b),
  dataAssert:  (b) => postJSON("/api/data-property-assertion", b),
  sameAs:      (b) => postJSON("/api/same-individuals", b),
  diffAs:      (b) => postJSON("/api/different-individuals", b),
  classAssert: (b) => postJSON("/api/class-assertion", b),
  metadata:    () => getJSON("/api/ontology-metadata"),
};

// ----- app state ------------------------------------------------------

const state = {
  ontoState: null,
  tree: {roots: []},
  entities: {classes: [], object_properties: [], data_properties: [],
             individuals: [], annotation_properties: [], datatypes: []},
  expanded: new Set(),
  selected: null,
  detail: null,
  activeTab: "classes",
  search: "",
};

// What kind a row's text should default to when it has no navigable target
// (e.g. restriction expressions like "hasShape value Circle", or characteristic
// keywords like "Functional"). Lets us colour the whole section consistently.
const SECTION_DEFAULT_KIND = {
  "Equivalent to":  "class",
  "SubClass of":    "class",
  "Disjoint with":  "class",
  "Subclasses":     "class",
  "Domain":         "class",
  "Domains":        "class",
  "Range":          "class",
  "Ranges":         "class",
  "Types":          "class",
  "Instances":      "indiv",
  "Same as":        "indiv",
  "Different from": "indiv",
  "Inverse of":     "objprop",
  "Characteristics":"keyword",
};

// ----- settings + AI assistant ----------------------------------------

const ai = {
  settings: { enabled: false, apiKey: "", model: "openai/gpt-4o-mini" },
  messages: [],         // {role:'user'|'ai', content, thinking?, error?}
  expanded: false,

  load() {
    try {
      const raw = localStorage.getItem("prestige.ai");
      if (raw) Object.assign(this.settings, JSON.parse(raw));
    } catch {}
  },
  save() {
    try { localStorage.setItem("prestige.ai", JSON.stringify(this.settings)); }
    catch {}
  },

  render() {
    let el = document.getElementById("ai-widget");
    if (!this.settings.enabled) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement("div");
      el.id = "ai-widget"; el.className = "ai-widget";
      document.body.appendChild(el);
    }
    el.innerHTML = "";
    el.appendChild(this.expanded ? this._panel() : this._bubble());
  },

  _bubble() {
    const b = document.createElement("button");
    b.className = "ai-bubble"; b.title = "Ask the AI assistant";
    b.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7
               8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8
               8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8z"/>
    </svg>`;
    b.onclick = () => { this.expanded = true; this.render(); };
    return b;
  },

  _panel() {
    const wrap = document.createElement("div");
    wrap.className = "ai-panel";
    wrap.innerHTML = `
      <div class="ai-header">
        <div class="ai-title">
          <span class="dot"></span>
          AI assistant
          <small>${escapeHTML(this.settings.model || "")}</small>
        </div>
        <button class="ghost icon-btn" data-min title="Minimise">−</button>
      </div>
      <div class="ai-messages" id="ai-msgs"></div>
      <div class="ai-input-row">
        <textarea id="ai-input" rows="2"
                  placeholder="Ask anything about the loaded ontology…"></textarea>
        <button class="accent" id="ai-send">Send</button>
      </div>
    `;
    wrap.querySelector("[data-min]").onclick = () => {
      this.expanded = false; this.render();
    };
    const msgs = wrap.querySelector("#ai-msgs");
    if (!this.messages.length) {
      msgs.innerHTML = `
        <div class="ai-empty">
          Ask about the loaded ontology.<br><br>
          <span class="hint">
            "Summarise this ontology."<br>
            "List the direct subclasses of the selected class."<br>
            "Which classes have a restriction on a given property?"
          </span>
        </div>`;
    } else {
      for (const m of this.messages) {
        const e = document.createElement("div");
        e.className = "ai-msg " + (m.role === "user" ? "user" : "ai")
                       + (m.thinking ? " thinking" : "")
                       + (m.error ? " error" : "");
        e.textContent = m.content;
        msgs.appendChild(e);
      }
      setTimeout(() => { msgs.scrollTop = msgs.scrollHeight; }, 0);
    }
    const inp = wrap.querySelector("#ai-input");
    const send = wrap.querySelector("#ai-send");
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault(); send.click();
      }
    });
    send.onclick = () => {
      const v = inp.value.trim();
      if (!v) return;
      inp.value = "";
      this.ask(v);
    };
    setTimeout(() => inp.focus(), 60);
    return wrap;
  },

  async ask(text) {
    if (!this.settings.apiKey) {
      this.messages.push({role:"ai", content:
        "No OpenRouter API key set. Open Settings (⚙) and paste your key.",
        error: true});
      this.render(); return;
    }
    this.messages.push({role:"user", content: text});
    this.messages.push({role:"ai", content: "Thinking…", thinking: true});
    this.render();
    try {
      const answer = await this._call(text);
      this.messages = this.messages.filter(m => !m.thinking);
      this.messages.push({role:"ai", content: answer});
    } catch (e) {
      this.messages = this.messages.filter(m => !m.thinking);
      this.messages.push({role:"ai",
                          content: "OpenRouter error: " + e.message,
                          error: true});
    }
    this.render();
  },

  async _call() {
    const ctx = await buildAIContext();
    const sys =
      "You are Prestige's AI assistant, embedded inside an OWL ontology " +
      "editor. The user's full ontology, in OWL 2 Functional Syntax, is " +
      "included below. Treat it as your source of truth: read the axioms " +
      "and reason from them. If a BORN-style copy of the file is also " +
      "included, it shows the same axioms with probability annotations on " +
      "SubClassOf — use it for any probability/Bayesian question.\n\n" +
      "Do NOT say you 'don't have access to the ontology' - the file IS " +
      "below. Quote axioms when helpful, keep answers tight and precise.\n\n" +
      ctx;
    const history = this.messages
      .filter(m => !m.thinking && !m.error)
      .map(m => ({role: m.role === "user" ? "user" : "assistant",
                  content: m.content}));
    const body = {
      model: this.settings.model,
      messages: [{role: "system", content: sys}, ...history],
    };
    const r = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type":  "application/json",
        "Authorization": "Bearer " + this.settings.apiKey,
        "HTTP-Referer":  location.origin,
        "X-Title":       "Prestige",
      },
      body: JSON.stringify(body),
    });
    let data;
    try { data = await r.json(); } catch { data = {}; }
    if (!r.ok) {
      const msg = data?.error?.message || `HTTP ${r.status}`;
      throw new Error(msg);
    }
    return data.choices?.[0]?.message?.content || "(empty response)";
  },
};

async function buildAIContext() {
  const lines = ["=== Ontology context (read-only) ==="];
  const s = state.ontoState;
  if (!s || !s.loaded) {
    lines.push("(no ontology loaded)");
    return lines.join("\n");
  }
  lines.push("Filename: " + (s.filename || "?"));
  lines.push("Ontology IRI: " + (s.ontology_iri || "?"));
  if (s.version_iri) lines.push("Version IRI: " + s.version_iri);
  const c = s.counts || {};
  lines.push("Counts: " + Object.entries(c)
    .map(([k, v]) => `${k}=${v}`).join(", "));

  // Currently-selected entity gets a quick summary up front so the model
  // notices the user's focus even when the file is huge.
  if (state.detail) {
    const d = state.detail;
    lines.push("");
    lines.push("Currently selected: " + d.name + " (" + d.kind + ")  -  " + d.qname);
    if (d.label) lines.push("  Label: " + d.label);
    const cmt = (d.annotations.find(a => a.prop === "rdfs:comment") || {}).text;
    if (cmt) lines.push("  Comment: " + cmt.replace(/\n/g, " "));
  }

  // Fetch the actual OWL file text - this is the heart of the context.
  try {
    const owl = await fetch("/api/raw-owl").then(r => r.ok ? r.text() : "");
    if (owl) {
      lines.push("");
      lines.push("=== Full ontology  (OWL 2 Functional Syntax) ===");
      lines.push(owl.trim());
    }
  } catch {}

  // If a BORN-style file has been created, append it too.
  try {
    const r = await fetch("/api/raw-born");
    if (r.ok) {
      const born = await r.text();
      lines.push("");
      lines.push("=== BORN-formatted ontology  (SubClassOf axioms carry a");
      lines.push("    probability annotation; otherwise identical to above) ===");
      lines.push(born.trim());
    }
  } catch {}

  return lines.join("\n");
}

async function openSettingsModal() {
  const content = document.createElement("div");

  const aiRow = document.createElement("div");
  aiRow.className = "settings-row";
  aiRow.innerHTML = `
    <div>
      <div class="label-main">AI Assistant</div>
      <div class="label-sub">
        Adds a floating chat panel (bottom-right) that you can ask about the
        currently-loaded ontology. Uses OpenRouter; your key stays in this
        browser.
      </div>
    </div>
    <div class="toggle-pill ${ai.settings.enabled ? "on" : ""}"
         role="switch" tabindex="0"
         aria-checked="${ai.settings.enabled}"></div>
  `;
  content.appendChild(aiRow);

  const sub = document.createElement("div");
  content.appendChild(sub);

  const POPULAR_MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "openai/gpt-4.1-mini",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-haiku",
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.1-70b-instruct",
    "mistralai/mistral-large",
  ];

  function renderFields() {
    sub.innerHTML = "";
    if (!ai.settings.enabled) return;
    const dl = `<datalist id="ai-model-dl">${
      POPULAR_MODELS.map(m => `<option value="${escapeAttr(m)}">`).join("")
    }</datalist>`;
    sub.innerHTML = `
      <div class="settings-row column">
        <div class="row-head">
          <div class="label-main">OpenRouter API key</div>
          <div class="label-sub">Stored locally in your browser (localStorage).
            Get one at <code>openrouter.ai</code>.</div>
        </div>
        <input type="password" id="ai-key"
               value="${escapeAttr(ai.settings.apiKey)}"
               placeholder="sk-or-…" autocomplete="off">
      </div>
      <div class="settings-row column">
        <div class="row-head">
          <div class="label-main">OpenRouter model</div>
          <div class="label-sub">e.g. <code>openai/gpt-4o-mini</code>,
            <code>anthropic/claude-3.5-sonnet</code>.</div>
        </div>
        <input type="text" id="ai-model" list="ai-model-dl"
               value="${escapeAttr(ai.settings.model)}"
               placeholder="openai/gpt-4o-mini">
        ${dl}
      </div>`;
  }
  renderFields();

  const togg = aiRow.querySelector(".toggle-pill");
  function flip() {
    ai.settings.enabled = !ai.settings.enabled;
    togg.classList.toggle("on", ai.settings.enabled);
    togg.setAttribute("aria-checked", String(ai.settings.enabled));
    renderFields();
  }
  togg.onclick = flip;
  togg.onkeydown = (e) => {
    if (e.key === " " || e.key === "Enter") { e.preventDefault(); flip(); }
  };

  await modal({
    title: "Settings", content, submitLabel: "Save",
    onSubmit: () => {
      if (ai.settings.enabled) {
        const k = (document.getElementById("ai-key")?.value || "").trim();
        const m = (document.getElementById("ai-model")?.value || "").trim();
        if (!k) throw new Error(
          "Please paste an OpenRouter API key, or toggle the assistant off.");
        if (!m) throw new Error("Please enter an OpenRouter model id.");
        ai.settings.apiKey = k;
        ai.settings.model  = m;
      }
      ai.save();
      ai.render();
      toast("Settings saved");
      return true;
    },
  });
}

// ----- toasts ---------------------------------------------------------

function toast(message, kind="ok", ms=2800) {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = message;
  $("#toast-root").appendChild(t);
  setTimeout(() => { t.style.transition = "opacity .25s"; t.style.opacity = "0";
                     setTimeout(() => t.remove(), 280); }, ms);
}

// ----- menus ----------------------------------------------------------

let currentMenu = null;
function closeMenu() { if (currentMenu) { currentMenu.remove(); currentMenu = null; }
                       document.removeEventListener("mousedown", onMenuOutside); }
function onMenuOutside(e) { if (currentMenu && !currentMenu.contains(e.target)) closeMenu(); }

function openMenu(anchorRect, items) {
  closeMenu();
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.style.top  = (anchorRect.bottom + 4) + "px";
  menu.style.left = anchorRect.left + "px";
  for (const it of items) {
    if (it.sep) {
      const d = document.createElement("div"); d.className = "menu-sep"; menu.appendChild(d);
    } else if (it.header) {
      const h = document.createElement("div"); h.className = "menu-header"; h.textContent = it.header; menu.appendChild(h);
    } else {
      const b = document.createElement("button");
      b.className = "menu-item";
      b.innerHTML = escapeHTML(it.label) + (it.kbd ? `<span class="kbd">${escapeHTML(it.kbd)}</span>` : "");
      if (it.disabled) b.disabled = true;
      b.onclick = () => { closeMenu(); it.onClick && it.onClick(); };
      menu.appendChild(b);
    }
  }
  $("#menu-root").appendChild(menu);
  currentMenu = menu;
  setTimeout(() => {
    // clamp horizontally
    const r = menu.getBoundingClientRect();
    if (r.right > window.innerWidth - 8) {
      menu.style.left = (window.innerWidth - r.width - 8) + "px";
    }
    document.addEventListener("mousedown", onMenuOutside);
  }, 0);
}

// ----- modal ----------------------------------------------------------

function modal({title, content, submitLabel="OK", cancelLabel="Cancel",
                wide=false, fullscreen=false, hideFooter=false, onSubmit=null}) {
  return new Promise(resolve => {
    const back = document.createElement("div");
    back.className = "modal-backdrop";
    const m = document.createElement("div");
    m.className = "modal" + (wide ? " wide" : "") + (fullscreen ? " fullscreen" : "");
    m.innerHTML = `
      <div class="modal-header">
        <h2>${escapeHTML(title)}</h2>
        <button class="ghost icon-btn" data-close aria-label="Close">×</button>
      </div>
    `;
    const body = document.createElement("div");
    body.className = "modal-body";
    if (typeof content === "string") body.innerHTML = content;
    else body.appendChild(content);
    const err = document.createElement("div");
    err.className = "error-msg"; err.style.display = "none";
    body.appendChild(err);
    m.appendChild(body);

    if (!hideFooter) {
      const foot = document.createElement("div");
      foot.className = "modal-footer";
      foot.innerHTML = `
        <button data-cancel>${escapeHTML(cancelLabel)}</button>
        <button class="accent" data-submit>${escapeHTML(submitLabel)}</button>
      `;
      m.appendChild(foot);
      foot.querySelector("[data-cancel]").onclick = () => close(null);
      foot.querySelector("[data-submit]").onclick = async () => {
        err.style.display = "none";
        try {
          const result = onSubmit ? await onSubmit() : true;
          if (result !== false) close(result ?? true);
        } catch (e) {
          err.textContent = e.message || String(e);
          err.style.display = "block";
        }
      };
    }
    back.appendChild(m);

    function close(val) {
      back.remove();
      document.removeEventListener("keydown", onKey);
      resolve(val);
    }
    function onKey(e) {
      if (e.key === "Escape") close(null);
      else if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        const sub = m.querySelector("[data-submit]"); if (sub) sub.click();
      }
    }
    document.addEventListener("keydown", onKey);
    back.addEventListener("mousedown", e => { if (e.target === back) close(null); });
    m.querySelector("[data-close]").onclick = () => close(null);
    $("#modal-root").appendChild(back);
    setTimeout(() => {
      const f = body.querySelector("input, textarea, select");
      if (f && !f.disabled) { f.focus(); if (f.select) try { f.select(); } catch {} }
    }, 50);
  });
}

// ----- form helpers ---------------------------------------------------

function intro(text) {
  const d = document.createElement("div"); d.className = "intro"; d.textContent = text;
  return d;
}
function textField({label, value="", placeholder="", list=null, multiline=false, rows=3}) {
  const wrap = document.createElement("div"); wrap.className = "field";
  const id = "f" + Math.random().toString(36).slice(2, 7);
  wrap.innerHTML = `<label for="${id}">${escapeHTML(label)}</label>`;
  let inp;
  if (multiline) {
    inp = document.createElement("textarea"); inp.rows = rows; inp.value = value;
  } else {
    inp = document.createElement("input"); inp.type = "text"; inp.value = value;
  }
  inp.id = id;
  if (placeholder) inp.placeholder = placeholder;
  if (list) inp.setAttribute("list", list);
  wrap.appendChild(inp);
  return {el: wrap, get: () => inp.value, input: inp};
}
function selectField({label, options=[], value=""}) {
  const wrap = document.createElement("div"); wrap.className = "field";
  const id = "f" + Math.random().toString(36).slice(2, 7);
  wrap.innerHTML = `<label for="${id}">${escapeHTML(label)}</label>`;
  const sel = document.createElement("select"); sel.id = id;
  for (const o of options) {
    const opt = document.createElement("option");
    if (typeof o === "string") { opt.value = o; opt.textContent = o; }
    else { opt.value = o.value; opt.textContent = o.label; }
    if ((typeof o === "string" ? o : o.value) === value) opt.selected = true;
    sel.appendChild(opt);
  }
  wrap.appendChild(sel);
  return {el: wrap, get: () => sel.value, input: sel};
}
function checkField({label, value=false}) {
  const wrap = document.createElement("div"); wrap.className = "field toggle-row";
  const id = "f" + Math.random().toString(36).slice(2, 7);
  const inp = document.createElement("input"); inp.type = "checkbox"; inp.id = id; inp.checked = value;
  const lbl = document.createElement("label"); lbl.htmlFor = id; lbl.textContent = label;
  lbl.style.marginBottom = "0"; lbl.style.color = "var(--text)";
  wrap.appendChild(inp); wrap.appendChild(lbl);
  return {el: wrap, get: () => inp.checked, input: inp};
}
function checklistField({label, items=[], value=[]}) {
  const wrap = document.createElement("div"); wrap.className = "field";
  wrap.innerHTML = `<label>${escapeHTML(label)}</label>`;
  const grid = document.createElement("div");
  const checks = {};
  for (const [key, lab] of items) {
    const row = document.createElement("div"); row.className = "toggle-row";
    const id = "f" + Math.random().toString(36).slice(2, 7);
    const inp = document.createElement("input"); inp.type = "checkbox"; inp.id = id;
    inp.checked = value.includes(key);
    const l = document.createElement("label"); l.htmlFor = id; l.textContent = lab;
    l.style.marginBottom = "0"; l.style.color = "var(--text)";
    row.appendChild(inp); row.appendChild(l);
    grid.appendChild(row);
    checks[key] = inp;
  }
  wrap.appendChild(grid);
  return {el: wrap, get: () => Object.entries(checks).filter(([k,i])=>i.checked).map(([k])=>k)};
}
function chipsField({label, candidates=[], value=[], placeholder="Type a name, Enter to add"}) {
  const wrap = document.createElement("div"); wrap.className = "field";
  wrap.innerHTML = `<label>${escapeHTML(label)}</label>`;
  const box = document.createElement("div"); box.className = "chips";
  const inp = document.createElement("input"); inp.type = "text"; inp.placeholder = placeholder;
  const dlId = "dl" + Math.random().toString(36).slice(2, 8);
  inp.setAttribute("list", dlId);
  const dl = document.createElement("datalist"); dl.id = dlId;
  for (const c of candidates) {
    const o = document.createElement("option"); o.value = c; dl.appendChild(o);
  }
  let values = [...value];
  function render() {
    box.innerHTML = "";
    for (const v of values) {
      const chip = document.createElement("span"); chip.className = "chip";
      chip.innerHTML = `${escapeHTML(v)} <button type="button" aria-label="Remove">×</button>`;
      chip.querySelector("button").onclick = (e) => { e.stopPropagation(); values = values.filter(x => x !== v); render(); };
      box.appendChild(chip);
    }
    box.appendChild(inp);
  }
  function commit() {
    const v = inp.value.trim();
    if (v && !values.includes(v)) { values.push(v); inp.value = ""; render(); }
  }
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commit(); }
    else if (e.key === "Backspace" && !inp.value && values.length) { values.pop(); render(); }
  });
  inp.addEventListener("blur", commit);
  box.onclick = () => inp.focus();
  render();
  wrap.appendChild(box); wrap.appendChild(dl);
  return {el: wrap, get: () => { commit(); return [...values]; }, input: inp};
}
function buildForm(fields, introText) {
  const wrap = document.createElement("div");
  if (introText) wrap.appendChild(intro(introText));
  for (const f of fields) wrap.appendChild(f.el);
  return wrap;
}

// ----- topbar ---------------------------------------------------------

function renderTopbar() {
  const s = state.ontoState;
  const fi = $("#file-info");
  if (s) {
    fi.innerHTML = (s.dirty ? `<span class="dirty-dot" title="Unsaved changes"></span>` : "") +
      escapeHTML(s.filename || "(unsaved)") +
      (s.ontology_iri ? `  <span style="color:var(--dim);">·  ${escapeHTML(s.ontology_iri)}</span>` : "");
  } else fi.textContent = "…";

  const tb = $("#toolbar");
  tb.innerHTML = "";
  function btn(label, opts={}) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = (opts.cls || "");
    b.innerHTML = (opts.icon ? `<span class="ic" data-ic="${opts.icon}"></span>` : "") + escapeHTML(label);
    if (opts.disabled) b.disabled = true;
    if (opts.onclick) b.onclick = opts.onclick;
    if (opts.title) b.title = opts.title;
    return b;
  }
  function sep() { const d = document.createElement("div"); d.className = "sep"; return d; }

  tb.appendChild(btn("Ontology", {onclick: (e) => openMenu(e.target.getBoundingClientRect(), ontologyMenu())}));
  tb.appendChild(btn("Tools",    {onclick: (e) => openMenu(e.target.getBoundingClientRect(), toolsMenu())}));
  tb.appendChild(btn("Extra tools", {onclick: (e) => openMenu(e.target.getBoundingClientRect(), examMenu())}));
  tb.appendChild(btn("Graph",    {icon: "net", onclick: openGraphModal}));
  tb.appendChild(sep());
  tb.appendChild(btn("Undo", {icon: "undo", cls: "compact",
    disabled: !s?.can_undo, onclick: doUndo, title: "Undo (Ctrl+Z)"}));
  tb.appendChild(btn("Redo", {icon: "redo", cls: "compact",
    disabled: !s?.can_redo, onclick: doRedo, title: "Redo (Ctrl+Y)"}));
  tb.appendChild(sep());
  tb.appendChild(btn("Save", {icon: "save", cls: "accent",
    onclick: doSave, title: "Save (Ctrl+S)"}));
  tb.appendChild(sep());
  tb.appendChild(btn("", {icon: "cog", cls: "icon-btn",
    onclick: openSettingsModal, title: "Settings"}));
}

function ontologyMenu() {
  return [
    {label: "New class…",          kbd: "Ctrl+N", onClick: () => openNewClassModal()},
    {label: "New subclass…",        onClick: () => openNewClassModal(state.selected)},
    {sep:true},
    {label: "Rename…",              onClick: () => state.detail && openRenameModal(state.detail)},
    {label: "Move (re-parent)…",    onClick: () => state.detail?.kind === "Class" && openMoveModal(state.detail)},
    {label: "Delete…",              onClick: () => state.detail?.kind === "Class" && openDeleteModal(state.detail)},
    {sep:true},
    {label: "Add restriction…",     onClick: () => state.detail?.kind === "Class" && openRestrictionModal(state.detail)},
    {label: "Add disjoint classes…", onClick: () => openDisjointModal()},
    {label: "Add equivalent classes…", onClick: () => openEquivalentModal()},
    {sep:true},
    {label: "New object property…",  onClick: () => openNewObjPropModal()},
    {label: "New data property…",    onClick: () => openNewDataPropModal()},
    {label: "New individual…",       onClick: () => openNewIndividualModal()},
    {sep:true},
    {label: "Mark individuals as same…",      onClick: () => openSameOrDiffAnywhere("same")},
    {label: "Mark individuals as different…", onClick: () => openSameOrDiffAnywhere("different")},
    {sep:true},
    {label: "Add raw axiom…",        onClick: () => openRawAxiomModal()},
  ];
}
function toolsMenu() {
  return [
    {label: "Open ontology…",             onClick: () => showUploadScreen()},
    {sep: true},
    {label: "Ontology metadata…",         onClick: openOntologyMetadataModal},
    {label: "Validate ontology",          onClick: openValidateModal},
    {label: "Statistics",                 onClick: openStatisticsModal},
    {sep: true},
    {label: "Run reasoner…",              onClick: openReasonerModal},
    {label: "Create BORN ontology file…", onClick: openCreateBornModal},
  ];
}
function examMenu() {
  return [
    {label: "Competency-question query…", onClick: openQueryModal},
    {label: "Pitfall & quality scan…",    onClick: openPitfallsModal},
    {label: "Hierarchy outline…",         onClick: openOutlineModal},
    {label: "Metrics report…",            onClick: openMetricsModal},
    {label: "Glossary / data dictionary…", onClick: openGlossaryModal},
    {label: "Compare with another file…", onClick: openDiffModal},
    {label: "SPARQL query…",              onClick: openSparqlModal},
  ];
}

// ----- status bar -----------------------------------------------------

function renderStatus(message) {
  const s = state.ontoState;
  if (message) $("#status").textContent = message;
  else if (!s?.loaded) $("#status").textContent = "No ontology loaded";
  else $("#status").textContent = s.dirty ? "Unsaved changes" : "Ready";
  if (!s || !s.loaded) { $("#counts").innerHTML = ""; return; }
  const c = s.counts || {};
  $("#counts").innerHTML = `
    <span><strong>${c.Classes ?? 0}</strong> classes</span>
    <span><strong>${c["Object properties"] ?? 0}</strong> obj props</span>
    <span><strong>${c["Data properties"] ?? 0}</strong> data props</span>
    <span><strong>${c["Named individuals"] ?? 0}</strong> individuals</span>
    <span><strong>${c["Total axioms"] ?? 0}</strong> axioms</span>
  `;
}

// ----- tabs + list pane -----------------------------------------------

function renderTabs() {
  const tabs = $("#tabs");
  tabs.innerHTML = "";
  const items = [
    {key: "classes",              label: "Classes",     kind: "class"},
    {key: "object_properties",    label: "Obj. props",  kind: "objprop"},
    {key: "data_properties",      label: "Data props",  kind: "dataprop"},
    {key: "individuals",          label: "Individuals", kind: "indiv"},
    {key: "annotation_properties",label: "Annotations", kind: "annoprop"},
    {key: "datatypes",            label: "Datatypes",   kind: "datatype"},
  ];
  for (const it of items) {
    const b = document.createElement("button");
    b.className = "tab kind-" + it.kind
                + (state.activeTab === it.key ? " active" : "");
    b.type = "button";
    b.textContent = it.label;
    b.onclick = () => { state.activeTab = it.key; renderTabs(); renderList(); };
    tabs.appendChild(b);
  }
}

function renderList() {
  if (state.activeTab === "classes") renderTree();
  else renderFlatList();
}

function renderTree() {
  const pane = $("#list-pane");
  pane.innerHTML = "";
  const q = state.search.toLowerCase().trim();
  function visible(node) {
    if (!q) return true;
    if (node.name.toLowerCase().includes(q)) return true;
    if ((node.label || "").toLowerCase().includes(q)) return true;
    return node.children.some(visible);
  }
  function render(node, container, depth=0) {
    if (q && !visible(node)) return;
    const row = document.createElement("div");
    row.className = "tree-row" + (node.cycle ? " cycle" : "") + (state.selected === node.qname ? " selected" : "");
    const hasKids = node.children.length > 0;
    // Collapsed by default; only the user (or a search match) opens nodes.
    const expanded = state.expanded.has(node.id) || (q && hasKids);
    if (expanded) row.classList.add("expanded");
    row.innerHTML = `
      <span class="toggle ${hasKids ? "" : "empty"}">▶</span>
      <span class="name">${escapeHTML(node.name)}</span>
      <span class="label-hint">${escapeHTML(node.label || "")}</span>
    `;
    row.dataset.qname = node.qname;
    row.dataset.id = node.id;
    row.onclick = (e) => {
      if (e.target.classList.contains("toggle") && hasKids) {
        toggleNode(node.id); return;
      }
      selectEntity(node.qname);
    };
    row.ondblclick = () => { if (hasKids) toggleNode(node.id); };
    row.oncontextmenu = (e) => {
      e.preventDefault(); selectEntity(node.qname);
      treeContextMenu(e, node.qname);
    };
    container.appendChild(row);
    if (hasKids) {
      const kids = document.createElement("div");
      kids.className = "tree-children" + (expanded ? "" : " hidden");
      for (const ch of node.children) render(ch, kids, depth + 1);
      container.appendChild(kids);
    }
  }
  for (const r of state.tree.roots) render(r, pane, 0);

  // Keep the selected row in view (only scrolls if it's off-screen).
  const sel = pane.querySelector(".tree-row.selected");
  if (sel) sel.scrollIntoView({block: "nearest"});
}

function expandAncestorsOf(qname) {
  // Walk the tree; for every node matching `qname`, mark its ancestors expanded
  // (a class can appear under more than one parent, so do them all).
  function walk(node, ancestors) {
    if (node.qname === qname) {
      for (const a of ancestors) state.expanded.add(a);
    }
    for (const ch of node.children) walk(ch, [...ancestors, node.id]);
  }
  for (const r of state.tree.roots) walk(r, []);
}

function toggleNode(id) {
  if (state.expanded.has(id)) state.expanded.delete(id);
  else state.expanded.add(id);
  renderTree();
}

function renderFlatList() {
  const pane = $("#list-pane");
  pane.innerHTML = "";
  const list = state.entities[state.activeTab] || [];
  const q = state.search.toLowerCase().trim();
  for (const e of list) {
    if (q && !e.name.toLowerCase().includes(q)
          && !(e.label || "").toLowerCase().includes(q)) continue;
    const row = document.createElement("div");
    const k = state._kinds?.get(e.qname);
    row.className = "flat-row"
      + (state.selected === e.qname ? " selected" : "")
      + (k ? " kind-" + k : "");
    row.innerHTML = `<span class="name">${escapeHTML(e.name)}</span>
                      <span class="label-hint">${escapeHTML(e.label || "")}</span>`;
    row.onclick = () => selectEntity(e.qname);
    pane.appendChild(row);
  }
}

function treeContextMenu(event, qname) {
  const items = [
    {label: "New subclass here…", onClick: () => openNewClassModal(qname)},
    {sep: true},
    {label: "Rename…",            onClick: () => state.detail && openRenameModal(state.detail)},
    {label: "Move (re-parent)…",  onClick: () => state.detail?.kind === "Class" && openMoveModal(state.detail)},
    {label: "Delete…",            onClick: () => state.detail?.kind === "Class" && openDeleteModal(state.detail)},
    {sep: true},
    {label: "Add restriction…",   onClick: () => state.detail?.kind === "Class" && openRestrictionModal(state.detail)},
  ];
  openMenu({left: event.clientX, top: event.clientY, bottom: event.clientY}, items);
}

// ----- detail panel ---------------------------------------------------

async function selectEntity(qname) {
  try { state.detail = await api.entity(qname); state.selected = qname; }
  catch (e) { toast(e.message, "error"); return; }
  // switch tab if necessary
  const kind = state.detail.kind;
  if      (kind === "ObjectProperty")     state.activeTab = "object_properties";
  else if (kind === "DataProperty")       state.activeTab = "data_properties";
  else if (kind === "NamedIndividual")    state.activeTab = "individuals";
  else if (kind === "AnnotationProperty") state.activeTab = "annotation_properties";
  else if (kind === "Datatype")           state.activeTab = "datatypes";
  else {
    state.activeTab = "classes";
    expandAncestorsOf(qname);   // make sure the selected row is reachable
  }
  renderTabs(); renderList(); renderDetail();
}

function renderDetail() {
  const root = $("#detail");
  root.innerHTML = "";
  const d = state.detail;
  if (!d) {
    root.innerHTML = `
      <div class="empty-state">
        <div class="em-icon">◆</div>
        Select a class, property or individual on the left to see its details.
      </div>`;
    return;
  }
  // ---- header
  const hdr = document.createElement("div"); hdr.className = "detail-header";
  hdr.innerHTML = `
    <div style="min-width:0; flex:1;">
      <div class="detail-name">${escapeHTML(d.name)}</div>
      <div class="detail-meta">
        <span class="kind-pill">${escapeHTML(d.kind || "?")}</span>
        <span>${escapeHTML(d.qname)}</span>
      </div>
    </div>
  `;
  const actions = document.createElement("div"); actions.className = "detail-actions";
  if (d.kind === "Class") {
    actions.innerHTML = `
      <button data-act="rename">Rename</button>
      <button data-act="move">Move</button>
      <button data-act="restriction">+ Restriction</button>
      <button data-act="newsub">+ Subclass</button>
      <button data-act="delete" class="danger">Delete</button>`;
  } else {
    actions.innerHTML = `<button data-act="rename">Rename</button>`;
  }
  hdr.appendChild(actions);
  actions.onclick = (e) => {
    const a = e.target.dataset.act;
    if (a === "rename")      openRenameModal(d);
    else if (a === "move")   openMoveModal(d);
    else if (a === "delete") openDeleteModal(d);
    else if (a === "restriction") openRestrictionModal(d);
    else if (a === "newsub") openNewClassModal(d.qname);
  };
  root.appendChild(hdr);

  // ---- annotations
  root.appendChild(buildAnnotationsCard(d));

  // ---- relationship sections
  for (const sec of d.sections) root.appendChild(buildSection(sec, d));

  // ---- appears in axioms
  if (d.appears_in.length) {
    const det = document.createElement("details"); det.className = "section axiom-list";
    det.innerHTML = `<summary><h3>Appears in axioms <span class="count">(${d.appears_in.length})</span></h3><div></div></summary>`;
    for (const ax of d.appears_in) {
      const r = document.createElement("div"); r.className = "row"; r.textContent = ax;
      det.appendChild(r);
    }
    root.appendChild(det);
  }
}

function buildAnnotationsCard(d) {
  const card = document.createElement("div"); card.className = "section";
  card.innerHTML = `
    <div class="section-head"><h3>Annotations</h3></div>
    <div class="ann-grid">
      <div class="row"><label>Label</label><input type="text" data-ann="label"></div>
      <div class="row"><label>Comment</label><textarea data-ann="comment" rows="3"></textarea></div>
      <div style="display:flex;justify-content:flex-end;">
        <button class="accent compact" data-apply>Apply annotations</button>
      </div>
    </div>
  `;
  card.querySelector('[data-ann="label"]').value = d.label || "";
  const cmt = (d.annotations.find(a => a.prop === "rdfs:comment") || {}).text || "";
  card.querySelector('[data-ann="comment"]').value = cmt;
  card.querySelector("[data-apply]").onclick = async () => {
    const lbl = card.querySelector('[data-ann="label"]').value;
    const cm = card.querySelector('[data-ann="comment"]').value;
    try {
      await api.annotation(d.qname, "rdfs:label", lbl);
      await api.annotation(d.qname, "rdfs:comment", cm);
      toast("Annotations updated");
      await refresh(); await selectEntity(d.qname);
    } catch (e) { toast(e.message, "error"); }
  };
  return card;
}

function buildSection(sec, d) {
  const card = document.createElement("div"); card.className = "section";
  card.innerHTML = `
    <div class="section-head">
      <h3>${escapeHTML(sec.title)}</h3>
      ${sec.rows.length ? `<span class="count">${sec.rows.length}</span>` : ""}
    </div>
  `;
  if (!sec.rows.length) {
    const e = document.createElement("div"); e.className = "empty"; e.textContent = "(none)";
    card.appendChild(e);
  } else {
    // Fallback kind for rows whose `target` isn't a single named entity
    // (e.g. "hasShape value Circle" in SubClass-of; characteristic literals)
    const fallback = SECTION_DEFAULT_KIND[sec.title] || null;
    for (const r of sec.rows) {
      const row = document.createElement("div");
      let cls = "row" + (r.target ? " link" : "");
      let k = r.target ? state._kinds?.get(r.target) : null;
      if (!k) k = fallback;
      if (k) cls += " kind-" + k;
      row.className = cls;
      row.textContent = r.text;
      if (r.target) row.onclick = () => selectEntity(r.target);
      card.appendChild(row);
    }
  }
  // Section-specific "+ Add" buttons (Protege-style)
  let acts = null;
  if (d.kind === "Class" && sec.title === "SubClass of") {
    acts = [
      ["+ Add parent",    () => openAddParentModal(d)],
      ["Remove parent",   () => openRemoveParentModal(d)],
    ];
  } else if (d.kind === "Class" && sec.title === "Instances") {
    acts = [["+ Add instance", () => openNewIndividualModal(d.qname)]];
  } else if (d.kind === "NamedIndividual") {
    if      (sec.title === "Types")
      acts = [["+ Add type",          () => openAddTypeModal(d)]];
    else if (sec.title === "Object property assertions")
      acts = [["+ Add assertion",     () => openObjAssertModal(d)]];
    else if (sec.title === "Data property assertions")
      acts = [["+ Add assertion",     () => openDataAssertModal(d)]];
    else if (sec.title === "Same as")
      acts = [["+ Mark same as…",     () => openSameAsModal(d)]];
    else if (sec.title === "Different from")
      acts = [["+ Mark different…",   () => openDiffAsModal(d)]];
  }
  if (acts) {
    const row = document.createElement("div"); row.className = "actions";
    for (const [label, fn] of acts) {
      const b = document.createElement("button");
      b.className = "compact"; b.textContent = label; b.onclick = fn;
      row.appendChild(b);
    }
    card.appendChild(row);
  }
  return card;
}

// ----- mutation modals ------------------------------------------------

function classNames()  { return state.entities.classes.map(c => c.name); }
function objPropNames(){ return state.entities.object_properties.map(c => c.name); }
function dataPropNames(){ return state.entities.data_properties.map(c => c.name); }
function indivNames()  { return state.entities.individuals.map(c => c.name); }

async function openNewClassModal(parentQname=null) {
  const parentLocal = parentQname ? localName(parentQname) : "";
  const name    = textField({label: "Class name", placeholder: "NewClass"});
  const parents = chipsField({label: "Parent classes", candidates: classNames(),
                              value: parentLocal ? [parentLocal] : []});
  const labelF  = textField({label: "Label (rdfs:label)"});
  const comment = textField({label: "Comment (rdfs:comment)", multiline: true});
  const content = buildForm([name, parents, labelF, comment],
    "A new class is declared and linked to each parent with a SubClassOf axiom. Leave parents empty for a root class.");
  const ok = await modal({title: "New class", content, submitLabel: "Create",
    onSubmit: async () => {
      const n = name.get().trim();
      if (!n) throw new Error("Please enter a class name.");
      const r = await api.createClass({name: n, parents: parents.get(),
                                       label: labelF.get(), comment: comment.get()});
      window._lastSelect = r.selected;
      return true;
    }});
  if (ok) { toast("Class created"); await refresh(); if (window._lastSelect) await selectEntity(window._lastSelect); }
}

async function openRenameModal(d) {
  const newName = textField({label: "New name", value: localName(d.qname)});
  const content = buildForm([newName],
    `Renaming "${localName(d.qname)}" rewrites the IRI in every axiom that references it.`);
  const ok = await modal({title: "Rename entity", content, submitLabel: "Rename",
    onSubmit: async () => {
      const v = newName.get().trim();
      if (!v) throw new Error("Please enter a new name.");
      const r = await api.rename({old: d.qname, new: v});
      window._lastSelect = r.selected; return true;
    }});
  if (ok) { toast("Renamed"); await refresh(); if (window._lastSelect) await selectEntity(window._lastSelect); }
}

async function openMoveModal(d) {
  const parents = chipsField({label: "New parent classes",
    candidates: classNames().filter(n => n !== d.name),
    value: d.parents.map(localName)});
  const content = buildForm([parents],
    `Re-parents "${d.name}". Named superclasses are replaced; restrictions are left intact. An empty list makes it a root class.`);
  const ok = await modal({title: "Move class", content, submitLabel: "Move",
    onSubmit: async () => {
      await api.move({name: d.qname, parents: parents.get()});
      return true;
    }});
  if (ok) { toast("Moved"); await refresh(); await selectEntity(d.qname); }
}

async function openDeleteModal(d) {
  const mode = selectField({label: "How to delete",
    options: [
      {value: "reparent", label: "Re-parent subclasses to grandparents"},
      {value: "subtree",  label: "Delete the whole subtree"},
      {value: "block",    label: "Block if it has subclasses"},
    ], value: "reparent"});
  const content = buildForm([mode],
    `"${d.name}" has ${d.children.length} direct subclass(es). Deleting removes the class declaration and every axiom that mentions it.`);
  const ok = await modal({title: "Delete class", content, submitLabel: "Delete",
    onSubmit: async () => {
      await api.del({name: d.qname, mode: mode.get()});
      return true;
    }});
  if (ok) { toast("Deleted"); state.selected = null; state.detail = null; await refresh(); renderDetail(); }
}

async function openAddParentModal(d) {
  const candidates = classNames().filter(n => n !== d.name);
  const pick = selectField({label: "Parent to add", options: ["", ...candidates]});
  const content = buildForm([pick], "Picks an additional named superclass.");
  const ok = await modal({title: "Add parent", content, submitLabel: "Add",
    onSubmit: async () => {
      const v = pick.get(); if (!v) throw new Error("Pick a parent class.");
      await api.addParent({name: d.qname, parent: v});
      return true;
    }});
  if (ok) { toast("Parent added"); await refresh(); await selectEntity(d.qname); }
}

async function openRemoveParentModal(d) {
  if (!d.parents.length) { toast("This class has no named parent.", "error"); return; }
  const pick = selectField({label: "Which parent to remove",
    options: d.parents.map(p => ({value: p, label: localName(p)}))});
  const content = buildForm([pick]);
  const ok = await modal({title: "Remove parent", content, submitLabel: "Remove",
    onSubmit: async () => {
      await api.rmParent({name: d.qname, parent: pick.get()});
      return true;
    }});
  if (ok) { toast("Parent removed"); await refresh(); await selectEntity(d.qname); }
}

async function openRestrictionModal(d) {
  const kind = selectField({label: "Restriction kind",
    options: [
      {value: "some",  label: "some (ObjectSomeValuesFrom)"},
      {value: "only",  label: "only (ObjectAllValuesFrom)"},
      {value: "value", label: "value (ObjectHasValue)"},
      {value: "data",  label: "data value (DataHasValue)"},
    ]});
  const prop = textField({label: "Property", list: "rest-prop-dl"});
  const filler = textField({label: "Filler (class / individual)", list: "rest-fill-dl"});
  const literal = textField({label: "Literal value (data restriction only)"});
  const wrap = buildForm([kind, prop, filler, literal],
    `Adds a SubClassOf restriction axiom to "${d.name}".`);
  const dlp = document.createElement("datalist"); dlp.id = "rest-prop-dl";
  for (const n of [...objPropNames(), ...dataPropNames()]) {
    const o = document.createElement("option"); o.value = n; dlp.appendChild(o);
  }
  const dlf = document.createElement("datalist"); dlf.id = "rest-fill-dl";
  for (const n of [...classNames(), ...indivNames()]) {
    const o = document.createElement("option"); o.value = n; dlf.appendChild(o);
  }
  wrap.appendChild(dlp); wrap.appendChild(dlf);
  const ok = await modal({title: "Add restriction to " + d.name, content: wrap, submitLabel: "Add",
    onSubmit: async () => {
      const k = kind.get();
      if (!prop.get().trim()) throw new Error("Pick a property.");
      if (k === "data") {
        if (!literal.get().trim()) throw new Error("Enter a literal value.");
        await api.restriction({cls: d.qname, prop: prop.get(), filler: literal.get(), kind: "data"});
      } else {
        if (!filler.get().trim()) throw new Error("Pick a filler.");
        await api.restriction({cls: d.qname, prop: prop.get(), filler: filler.get(), kind: k});
      }
      return true;
    }});
  if (ok) { toast("Restriction added"); await refresh(); await selectEntity(d.qname); }
}

async function openDisjointModal() {
  const def = state.detail?.kind === "Class" ? [localName(state.detail.qname)] : [];
  const classes = chipsField({label: "Classes (pick 2+)", candidates: classNames(), value: def});
  const content = buildForm([classes], "Asserts that the chosen classes share no individuals.");
  const ok = await modal({title: "Disjoint classes", content, submitLabel: "Add",
    onSubmit: async () => {
      if (classes.get().length < 2) throw new Error("Pick at least two classes.");
      await api.disjoint({classes: classes.get()});
      return true;
    }});
  if (ok) { toast("Disjointness added"); await refresh(); if (state.selected) await selectEntity(state.selected); }
}

async function openEquivalentModal() {
  const a = textField({label: "Class A", value: state.detail?.kind === "Class" ? localName(state.detail.qname) : "", list: "eq-dl"});
  const b = textField({label: "Class B", list: "eq-dl"});
  const content = buildForm([a, b]);
  const dl = document.createElement("datalist"); dl.id = "eq-dl";
  for (const n of classNames()) { const o = document.createElement("option"); o.value = n; dl.appendChild(o); }
  content.appendChild(dl);
  const ok = await modal({title: "Equivalent classes", content, submitLabel: "Add",
    onSubmit: async () => {
      if (!a.get() || !b.get()) throw new Error("Pick both classes.");
      await api.equivalent({a: a.get(), b: b.get()});
      return true;
    }});
  if (ok) { toast("Equivalence added"); await refresh(); }
}

async function openNewObjPropModal() {
  const name = textField({label: "Property name", placeholder: "isPartOf"});
  const dom = textField({label: "Domain class", list: "cls-dl"});
  const ran = textField({label: "Range class",  list: "cls-dl"});
  const chars = checklistField({label: "Characteristics", items: [
    ["FunctionalObjectProperty", "Functional"],
    ["InverseFunctionalObjectProperty", "Inverse functional"],
    ["TransitiveObjectProperty", "Transitive"],
    ["SymmetricObjectProperty", "Symmetric"],
    ["AsymmetricObjectProperty", "Asymmetric"],
    ["ReflexiveObjectProperty", "Reflexive"],
    ["IrreflexiveObjectProperty", "Irreflexive"],
  ]});
  const labelF = textField({label: "Label"});
  const content = buildForm([name, dom, ran, chars, labelF], "Declares an object property.");
  const dl = document.createElement("datalist"); dl.id = "cls-dl";
  for (const n of classNames()) { const o = document.createElement("option"); o.value = n; dl.appendChild(o); }
  content.appendChild(dl);
  const ok = await modal({title: "New object property", content, submitLabel: "Create",
    onSubmit: async () => {
      if (!name.get().trim()) throw new Error("Enter a name.");
      const r = await api.newObjProp({name: name.get(), domain: dom.get(), range: ran.get(),
        characteristics: chars.get(), label: labelF.get()});
      window._lastSelect = r.selected; return true;
    }});
  if (ok) { toast("Object property created"); await refresh(); if (window._lastSelect) await selectEntity(window._lastSelect); }
}

async function openNewDataPropModal() {
  const name = textField({label: "Property name", placeholder: "hasWeight"});
  const dom = textField({label: "Domain class", list: "cls-dl"});
  const ran = selectField({label: "Range datatype",
    options: ["xsd:string", "xsd:integer", "xsd:decimal", "xsd:boolean", "xsd:float", "xsd:dateTime"],
    value: "xsd:string"});
  const func = checkField({label: "Functional data property"});
  const labelF = textField({label: "Label"});
  const content = buildForm([name, dom, ran, func, labelF], "Declares a data property.");
  const dl = document.createElement("datalist"); dl.id = "cls-dl";
  for (const n of classNames()) { const o = document.createElement("option"); o.value = n; dl.appendChild(o); }
  content.appendChild(dl);
  const ok = await modal({title: "New data property", content, submitLabel: "Create",
    onSubmit: async () => {
      if (!name.get().trim()) throw new Error("Enter a name.");
      const r = await api.newDataProp({name: name.get(), domain: dom.get(), range: ran.get(),
        functional: func.get(), label: labelF.get()});
      window._lastSelect = r.selected; return true;
    }});
  if (ok) { toast("Data property created"); await refresh(); if (window._lastSelect) await selectEntity(window._lastSelect); }
}

async function openNewIndividualModal() {
  const name = textField({label: "Individual name", placeholder: "MyInstance"});
  const types = chipsField({label: "Class types", candidates: classNames()});
  const content = buildForm([name, types], "Declares a named individual and asserts class membership.");
  const ok = await modal({title: "New individual", content, submitLabel: "Create",
    onSubmit: async () => {
      if (!name.get().trim()) throw new Error("Enter a name.");
      const r = await api.newIndiv({name: name.get(), types: types.get()});
      window._lastSelect = r.selected; return true;
    }});
  if (ok) { toast("Individual created"); await refresh(); if (window._lastSelect) await selectEntity(window._lastSelect); }
}

// ----- property assertions, same/different, type, metadata --------------

async function openObjAssertModal(d) {
  const prop   = textField({label: "Object property", list: "obj-ops-dl"});
  const target = textField({label: "Target individual", list: "obj-tgt-dl"});
  const content = buildForm([prop, target],
    `Add an ObjectPropertyAssertion for ${d.name}.`);
  const dl1 = document.createElement("datalist"); dl1.id = "obj-ops-dl";
  for (const n of objPropNames()) { const o=document.createElement("option"); o.value=n; dl1.appendChild(o); }
  const dl2 = document.createElement("datalist"); dl2.id = "obj-tgt-dl";
  for (const n of indivNames())   { const o=document.createElement("option"); o.value=n; dl2.appendChild(o); }
  content.appendChild(dl1); content.appendChild(dl2);
  const ok = await modal({title: "Object property assertion", content, submitLabel: "Add",
    onSubmit: async () => {
      if (!prop.get().trim() || !target.get().trim())
        throw new Error("Pick both a property and a target individual.");
      await api.objAssert({individual: d.qname, prop: prop.get(), target: target.get()});
      return true;
    }});
  if (ok) { toast("Assertion added"); await refresh(); await selectEntity(d.qname); }
}

async function openDataAssertModal(d) {
  const prop  = textField({label: "Data property", list: "data-props-dl"});
  const value = textField({label: "Value (literal)"});
  const dtype = selectField({label: "Datatype (optional)",
    options: ["", "xsd:string", "xsd:integer", "xsd:decimal", "xsd:boolean",
              "xsd:float", "xsd:dateTime"], value: ""});
  const content = buildForm([prop, value, dtype],
    `Add a DataPropertyAssertion for ${d.name}.`);
  const dl = document.createElement("datalist"); dl.id = "data-props-dl";
  for (const n of dataPropNames()) { const o=document.createElement("option"); o.value=n; dl.appendChild(o); }
  content.appendChild(dl);
  const ok = await modal({title: "Data property assertion", content, submitLabel: "Add",
    onSubmit: async () => {
      if (!prop.get().trim() || !value.get().trim())
        throw new Error("Pick a property and enter a value.");
      await api.dataAssert({individual: d.qname, prop: prop.get(),
                            value: value.get(),
                            datatype: dtype.get() || null});
      return true;
    }});
  if (ok) { toast("Assertion added"); await refresh(); await selectEntity(d.qname); }
}

async function openAddTypeModal(d) {
  const cls = textField({label: "Class", list: "type-cls-dl"});
  const content = buildForm([cls],
    `Add a ClassAssertion for ${d.name} (declares the individual to be of this class).`);
  const dl = document.createElement("datalist"); dl.id = "type-cls-dl";
  for (const n of classNames()) { const o=document.createElement("option"); o.value=n; dl.appendChild(o); }
  content.appendChild(dl);
  const ok = await modal({title: "Add class assertion", content, submitLabel: "Add",
    onSubmit: async () => {
      if (!cls.get().trim()) throw new Error("Pick a class.");
      await api.classAssert({individual: d.qname, type: cls.get()});
      return true;
    }});
  if (ok) { toast("Type added"); await refresh(); await selectEntity(d.qname); }
}

async function openSameAsModal(d) {
  const cands = indivNames().filter(n => n !== d.name);
  const others = chipsField({label: "Same as (one or more individuals)", candidates: cands});
  const content = buildForm([others],
    `Adds  SameIndividual(${d.name}, …).`);
  const ok = await modal({title: "Mark as same individual", content, submitLabel: "Add",
    onSubmit: async () => {
      if (others.get().length < 1) throw new Error("Pick at least one other individual.");
      await api.sameAs({individuals: [d.name, ...others.get()]});
      return true;
    }});
  if (ok) { toast("SameIndividual axiom added"); await refresh(); await selectEntity(d.qname); }
}

async function openDiffAsModal(d) {
  const cands = indivNames().filter(n => n !== d.name);
  const others = chipsField({label: "Different from (one or more individuals)", candidates: cands});
  const content = buildForm([others],
    `Adds  DifferentIndividuals(${d.name}, …).`);
  const ok = await modal({title: "Mark as different individuals", content, submitLabel: "Add",
    onSubmit: async () => {
      if (others.get().length < 1) throw new Error("Pick at least one other individual.");
      await api.diffAs({individuals: [d.name, ...others.get()]});
      return true;
    }});
  if (ok) { toast("DifferentIndividuals axiom added"); await refresh(); await selectEntity(d.qname); }
}

async function openSameOrDiffAnywhere(kind) {
  // kind = 'same' | 'different'
  const list = chipsField({label: "Individuals (two or more)",
                            candidates: indivNames()});
  const content = buildForm([list],
    kind === "same" ? "Adds a SameIndividual axiom." :
                       "Adds a DifferentIndividuals axiom.");
  const ok = await modal({title: kind === "same"
                          ? "Mark individuals as same"
                          : "Mark individuals as different",
                          content, submitLabel: "Add",
    onSubmit: async () => {
      if (list.get().length < 2) throw new Error("Pick at least two individuals.");
      if (kind === "same") await api.sameAs({individuals: list.get()});
      else                  await api.diffAs({individuals: list.get()});
      return true;
    }});
  if (ok) { toast("Axiom added"); await refresh(); }
}

async function openOntologyMetadataModal() {
  let m;
  try { m = await api.metadata(); }
  catch (e) { toast(e.message, "error"); return; }
  const lines = [];
  lines.push("Ontology IRI:   " + (m.ontology_iri || "(none)"));
  if (m.version_iri) lines.push("Version IRI:    " + m.version_iri);
  lines.push("");
  lines.push("Prefixes:");
  for (const [p, iri] of Object.entries(m.prefixes))
    lines.push("  " + ((p || "(default)") + ":").padEnd(14) + "  →  " + iri);
  lines.push("");
  lines.push("Ontology-level annotations: " + m.annotations.length);
  for (const a of m.annotations) {
    const lang = a.language ? "@" + a.language : "";
    const val = (a.value || "").replace(/\s+/g, " ").trim();
    const trim = val.length > 220 ? val.slice(0, 220) + "…" : val;
    lines.push("  " + a.prop + lang + ":");
    lines.push("    " + trim);
  }
  const pre = document.createElement("pre");
  pre.className = "pre"; pre.textContent = lines.join("\n");
  modal({title: "Ontology metadata", content: pre, wide: true, hideFooter: true});
}

async function openRawAxiomModal() {
  const text = textField({label: "Functional-syntax axiom",
    multiline: true, rows: 5,
    placeholder: "SubClassOf(:ChildClass :ParentClass)"});
  const content = buildForm([text],
    "Type any OWL 2 functional-syntax axiom. It is parsed and validated before being added.");
  const ok = await modal({title: "Add raw axiom", content, submitLabel: "Add",
    onSubmit: async () => {
      if (!text.get().trim()) throw new Error("Enter an axiom.");
      await api.rawAxiom({text: text.get()});
      return true;
    }});
  if (ok) { toast("Axiom added"); await refresh(); }
}

// ----- undo / redo / save --------------------------------------------

async function doUndo() {
  try { await api.undo(); toast("Undone"); await refresh(); if (state.selected) await selectEntity(state.selected); }
  catch (e) { toast(e.message, "error"); }
}
async function doRedo() {
  try { await api.redo(); toast("Redone"); await refresh(); if (state.selected) await selectEntity(state.selected); }
  catch (e) { toast(e.message, "error"); }
}
async function doSave() {
  try {
    const r = await api.save();
    toast("Saved" + (r.backup ? "  (backup: " + r.backup + ")" : ""));
    await refresh();
  } catch (e) { toast(e.message, "error"); }
}

// ----- analysis modals ------------------------------------------------

async function openValidateModal() {
  const data = await api.validate();
  const body = buildReportTree(data.issues, true);
  modal({title: "Validation results", content: body, wide: true,
         hideFooter: true, submitLabel: "Done", onSubmit: async () => true});
}
async function openPitfallsModal() {
  const data = await api.pitfalls();
  const body = buildReportTree(data.issues, true);
  modal({title: "Pitfall & quality scan", content: body, wide: true,
         hideFooter: true});
}

function buildReportTree(issues, navigable) {
  const wrap = document.createElement("div");
  if (!issues.length) {
    wrap.innerHTML = `<div class="intro">No issues found.</div>`;
    return wrap;
  }
  const summary = document.createElement("div"); summary.className = "intro";
  summary.textContent = issues.length + " issue(s) found.";
  wrap.appendChild(summary);
  const grouped = {};
  for (const i of issues) (grouped[i.category || "Structural"] ||= []).push(i);
  for (const cat of Object.keys(grouped).sort()) {
    const det = document.createElement("details"); det.className = "report-cat";
    det.open = grouped[cat].length <= 8;
    det.innerHTML = `<summary>${escapeHTML(cat)} <span class="count-pill">${grouped[cat].length}</span></summary>`;
    for (const i of grouped[cat]) {
      const item = document.createElement("div"); item.className = "item" + (navigable && i.entity ? " link" : "");
      item.innerHTML = `<span class="sev ${escapeAttr(i.severity)}">${escapeHTML(i.severity || "")}</span><span>${escapeHTML(i.message)}</span>`;
      if (navigable && i.entity) item.onclick = () => selectEntity(i.entity);
      det.appendChild(item);
    }
    wrap.appendChild(det);
  }
  return wrap;
}

async function openStatisticsModal() {
  const s = await api.statistics();
  const table = document.createElement("table"); table.className = "kv-table";
  for (const [k, v] of Object.entries(s)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="k">${escapeHTML(k)}</td><td class="v">${escapeHTML(String(v))}</td>`;
    table.appendChild(tr);
  }
  modal({title: "Ontology statistics", content: table, hideFooter: true});
}

async function openMetricsModal() {
  const m = await api.metrics();
  const table = document.createElement("table"); table.className = "kv-table";
  for (const row of m) {
    const tr = document.createElement("tr");
    if (row.label === "") { tr.className = "spacer"; tr.innerHTML = "<td></td><td></td>"; }
    else tr.innerHTML = `<td class="k">${escapeHTML(row.label)}</td><td class="v">${escapeHTML(String(row.value))}</td>`;
    table.appendChild(tr);
  }
  modal({title: "Metrics report", content: table, wide: true, hideFooter: true});
}

async function openOutlineModal() {
  const root = selectField({label: "Root class (blank = all roots)",
    options: ["", ...classNames()]});
  const labels = checkField({label: "Show labels", value: true});
  const out = document.createElement("pre"); out.className = "pre"; out.textContent = "(click Generate)";
  const btn = document.createElement("button"); btn.className = "accent"; btn.textContent = "Generate";
  const saveBtn = document.createElement("button"); saveBtn.textContent = "Download .txt";
  const ctrl = document.createElement("div"); ctrl.style.display = "flex"; ctrl.style.gap = "8px"; ctrl.style.marginBottom = "10px";
  ctrl.appendChild(btn); ctrl.appendChild(saveBtn);
  const content = document.createElement("div");
  content.appendChild(intro("An indented taxonomy outline you can paste into your report."));
  content.appendChild(root.el); content.appendChild(labels.el); content.appendChild(ctrl); content.appendChild(out);
  btn.onclick = async () => {
    out.textContent = "Loading…";
    try { out.textContent = await api.outline(root.get() || null, labels.get()); }
    catch (e) { out.textContent = "Error: " + e.message; }
  };
  saveBtn.onclick = () => download("hierarchy_outline.txt", out.textContent);
  modal({title: "Hierarchy outline", content, wide: true, hideFooter: true});
  setTimeout(() => btn.click(), 50);
}

async function openGlossaryModal() {
  const fmt = selectField({label: "Format", options: [
    {value: "text", label: "Plain text"},
    {value: "markdown", label: "Markdown table"},
    {value: "csv", label: "CSV"}], value: "text"});
  const out = document.createElement("pre"); out.className = "pre"; out.textContent = "Loading…";
  const ctrl = document.createElement("div"); ctrl.style.display="flex"; ctrl.style.gap = "8px"; ctrl.style.marginBottom = "10px";
  const gen = document.createElement("button"); gen.className = "accent"; gen.textContent = "Generate";
  const save = document.createElement("button"); save.textContent = "Download";
  ctrl.appendChild(gen); ctrl.appendChild(save);
  const content = document.createElement("div");
  content.appendChild(intro("Every class with its label and comment."));
  content.appendChild(fmt.el); content.appendChild(ctrl); content.appendChild(out);
  async function run() { out.textContent = "Loading…"; try { out.textContent = await api.glossary(fmt.get()); } catch (e) { out.textContent = "Error: " + e.message; } }
  gen.onclick = run; fmt.input.onchange = run;
  save.onclick = () => {
    const ext = {text:"txt", markdown:"md", csv:"csv"}[fmt.get()] || "txt";
    download("glossary." + ext, out.textContent);
  };
  modal({title: "Glossary / data dictionary", content, wide: true, hideFooter: true});
  setTimeout(run, 50);
}

async function openDiffModal() {
  const fileF = document.createElement("input"); fileF.type = "file";
  fileF.accept = ".owl,.ofn,application/xml";
  const out = document.createElement("div"); out.className = "intro";
  out.textContent = "Pick another .owl file to compare against the current one.";
  const run = document.createElement("button"); run.className = "accent"; run.textContent = "Compare";
  const ctrl = document.createElement("div"); ctrl.style.display="flex"; ctrl.style.gap = "8px"; ctrl.style.alignItems="center"; ctrl.style.marginBottom="12px";
  ctrl.appendChild(fileF); ctrl.appendChild(run);
  const results = document.createElement("div");
  const content = document.createElement("div");
  content.appendChild(intro("Axiom-level diff against another ontology file."));
  content.appendChild(ctrl); content.appendChild(out); content.appendChild(results);
  run.onclick = async () => {
    if (!fileF.files.length) { out.textContent = "Pick a file first."; return; }
    out.textContent = "Comparing…";
    try {
      const d = await api.diff(fileF.files[0]);
      results.innerHTML = "";
      out.textContent = `vs ${d.filename}  -  +${d.added_entities.length} / -${d.removed_entities.length} entities,  +${d.added_axioms.length} / -${d.removed_axioms.length} axioms`;
      const groups = [
        ["Entities added (in current, not other)", d.added_entities.map(([k,n]) => `${k}  ${n}`)],
        ["Entities removed (in other, not current)", d.removed_entities.map(([k,n]) => `${k}  ${n}`)],
        ["Axioms added", d.added_axioms.map(a => a.replace(/\s+/g," "))],
        ["Axioms removed", d.removed_axioms.map(a => a.replace(/\s+/g," "))],
      ];
      for (const [title, rows] of groups) {
        const det = document.createElement("details"); det.className = "report-cat";
        det.open = rows.length <= 12;
        det.innerHTML = `<summary>${escapeHTML(title)} <span class="count-pill">${rows.length}</span></summary>`;
        for (const r of rows) {
          const i = document.createElement("div"); i.className = "item"; i.textContent = r;
          det.appendChild(i);
        }
        results.appendChild(det);
      }
    } catch (e) { out.textContent = "Error: " + e.message; }
  };
  modal({title: "Compare with another file", content, wide: true, hideFooter: true});
}

async function openSparqlModal() {
  // Build a sensible sample query, using the loaded ontology's IRI if any.
  const iri = state.ontoState?.ontology_iri;
  const prefixLine = iri
    ? `PREFIX o: <${iri.endsWith("#") || iri.endsWith("/") ? iri : iri + "#"}>\n`
    : "";
  const sample =
    prefixLine +
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n" +
    "PREFIX owl:  <http://www.w3.org/2002/07/owl#>\n\n" +
    "SELECT ?subclass ?super\n" +
    "WHERE { ?subclass rdfs:subClassOf ?super .\n" +
    "        FILTER(isIRI(?super)) }\n" +
    "LIMIT 25";
  const q = textField({label: "SPARQL query", multiline: true, rows: 7, value: sample});
  const result = document.createElement("pre"); result.className = "pre"; result.textContent = "Click Run to execute.";
  const run = document.createElement("button"); run.className = "accent"; run.textContent = "Run query";
  const ctrl = document.createElement("div"); ctrl.style.marginBottom = "10px";
  ctrl.appendChild(run);
  const content = document.createElement("div");
  content.appendChild(intro("Real SPARQL via owlready2 over a translated copy of the current ontology. Needs owlready2 installed (pip)."));
  content.appendChild(q.el); content.appendChild(ctrl); content.appendChild(result);
  run.onclick = async () => {
    result.textContent = "Running…";
    try {
      const r = await api.sparql(q.get());
      result.textContent = r.rows.length
        ? r.rows.map(row => row.join("\t")).join("\n") + `\n\n${r.rows.length} row(s)${r.skipped?` (${r.skipped} axioms skipped)`:""}`
        : "No results.";
    } catch (e) { result.textContent = "Error: " + e.message; }
  };
  modal({title: "SPARQL query", content, wide: true, hideFooter: true});
}

async function openReasonerModal() {
  const reasoners = await api.reasoners();
  const byKey = Object.fromEntries(reasoners.map(r => [r.key, r]));

  const content = document.createElement("div");
  content.innerHTML = `
    <div class="intro">
      Pick a reasoner and click <strong>Run</strong>.
      HermiT and Pellet check logical consistency, list unsatisfiable classes
      and inferred subclass relations. BORN is a Bayesian reasoner that needs
      a BORN-style copy of your ontology - annotated with probabilities on
      SubClassOf axioms.
    </div>
    <div class="field"><label>Reasoner</label>
      <select id="r-name">
        ${reasoners.map(r => `<option value="${r.key}">${escapeHTML(r.name)}${r.available?"":" (not available)"}</option>`).join("")}
      </select>
    </div>
    <div id="r-status" style="font-size:12.5px; margin-bottom:12px;"></div>
    <div id="r-born" style="display:none; padding:12px; background:var(--bg-card2); border:1px solid var(--border); border-radius:8px; margin-bottom:12px;">
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <label style="color:var(--muted);">Default probability for SubClassOf axioms</label>
        <input type="text" id="r-prob" value="1.0" style="width:80px;">
        <button id="r-create-born">Create BORN file now</button>
      </div>
      <div id="r-born-info" style="color:var(--muted); font-size:12px; margin-top:8px;">
        BORN needs an ontology file with probability annotations on its
        SubClassOf axioms. Use this button to generate <code>&lt;name&gt;_BORN.owl</code>
        next to the current ontology, then click Run.
      </div>
    </div>
    <div style="display:flex; gap:8px; margin-bottom:10px;">
      <button class="accent" id="r-run">Run reasoner</button>
    </div>
    <pre class="pre" id="r-out" style="min-height:140px;">(reasoner output appears here)</pre>
  `;
  const sel        = content.querySelector("#r-name");
  const statusEl   = content.querySelector("#r-status");
  const bornBlock  = content.querySelector("#r-born");
  const runBtn     = content.querySelector("#r-run");
  const out        = content.querySelector("#r-out");

  function refreshStatus() {
    const cur = byKey[sel.value];
    statusEl.innerHTML = (cur.available
      ? `<span style="color:var(--green);">●</span>  Ready  ·  needs ${escapeHTML(cur.requires)}`
      : `<span style="color:var(--red);">●</span>  ${escapeHTML(cur.message)}`);
    bornBlock.style.display = sel.value === "born" ? "block" : "none";
    runBtn.disabled = !cur.available;
  }
  sel.onchange = refreshStatus;
  refreshStatus();

  content.querySelector("#r-create-born").onclick = async () => {
    const info = content.querySelector("#r-born-info");
    info.textContent = "Creating BORN file…";
    try {
      const r = await api.createBorn({probability: content.querySelector("#r-prob").value || "1.0"});
      info.innerHTML = `✓ ${escapeHTML(r.message)}  ·  <a href="/api/download?path=${encodeURIComponent(r.path)}" style="color:var(--accent);">Download</a>`;
      toast(r.message);
    } catch (e) { info.innerHTML = `<span style="color:var(--red);">Error: ${escapeHTML(e.message)}</span>`; }
  };

  runBtn.onclick = async () => {
    out.textContent = "Running…";
    try {
      const r = await api.reason({name: sel.value});
      out.textContent = formatReasonerResult(r);
    } catch (e) { out.textContent = "Error: " + e.message; }
  };

  modal({title: "OWL reasoners  (HermiT · Pellet · BORN)",
         content, wide: true, hideFooter: true});
}

function formatReasonerResult(r) {
  if (r.reasoner === "BORN") {
    if (r.mode === "built-in") {
      const c = r.counts, d = r.distribution;
      const lines = [];
      lines.push("BORN  ·  built-in probabilistic analyser");
      lines.push("file:  " + r.file);
      lines.push("");
      lines.push("Axiom counts");
      lines.push("  SubClassOf total       " + c.subclass_axioms);
      lines.push("  With probability       " + c.with_probability);
      lines.push("  Deterministic          " + c.without_probability);
      lines.push("  Distinct classes       " + c.classes_involved);
      lines.push("");
      lines.push("Probability distribution");
      for (const [k, v] of Object.entries(d)) lines.push("  " + k.padEnd(10) + "  " + v);
      lines.push("");
      lines.push(`Marginal subsumptions (max-product over named subclass chains): ${r.marginal_total}`);
      lines.push("");
      lines.push("Most-uncertain (lowest compound probability):");
      for (const [a, b, p] of r.most_uncertain)
        lines.push("  " + p.toFixed(3) + "   " + a + "   ⊑   " + b);
      lines.push("");
      lines.push("Most-certain (highest compound probability):");
      for (const [a, b, p] of r.most_certain.slice(0, 10))
        lines.push("  " + p.toFixed(3) + "   " + a + "   ⊑   " + b);
      return lines.join("\n");
    }
    return [
      "BORN  ·  external born.jar  ·  exit code " + r.returncode,
      "file: " + r.file,
      "jar:  " + r.born_jar,
      "",
      "── stdout ──", r.stdout || "(empty)",
      "",
      "── stderr ──", r.stderr || "(empty)",
    ].join("\n");
  }
  const lines = [];
  lines.push((r.consistent ? "✓ " : "✗ ") + r.reasoner + "  -  "
             + (r.consistent ? "Ontology is consistent." : "Ontology is INCONSISTENT."));
  if (r.skipped) lines.push(`(${r.skipped} axioms skipped in translation)`);
  lines.push("", `Unsatisfiable classes: ${r.unsatisfiable.length}`);
  for (const c of r.unsatisfiable) lines.push("  " + c);
  lines.push("", `Inferred SubClassOf relations: ${r.inferred_subclasses.length}`);
  for (const [a, b] of r.inferred_subclasses.slice(0, 200)) lines.push(`  ${a}  is-a  ${b}`);
  if (r.inferred_subclasses.length > 200) lines.push(`  … (${r.inferred_subclasses.length - 200} more)`);
  return lines.join("\n");
}

async function openCreateBornModal() {
  const prob = textField({label: "Default probability for every SubClassOf axiom",
                          value: "1.0"});
  const content = buildForm([prob],
    `Writes a copy of the current ontology with a probability annotation on
     every SubClassOf axiom. The output goes next to the loaded ontology as
     <name>_BORN.owl. Used by the BORN (Bayesian OWL Reasoner) tool.`);
  const ok = await modal({
    title: "Create BORN ontology file",
    content, submitLabel: "Create",
    onSubmit: async () => {
      const r = await api.createBorn({probability: prob.get() || "1.0"});
      window._lastBorn = r;
      return true;
    },
  });
  if (ok) {
    const r = window._lastBorn;
    toast(r.message);
    // open a tiny "saved" dialog with a download link
    const note = document.createElement("div");
    note.innerHTML = `
      <div class="intro">${escapeHTML(r.message)}</div>
      <p><strong>Path:</strong> <code>${escapeHTML(r.path)}</code></p>
      <p><a href="/api/download?path=${encodeURIComponent(r.path)}"
            class="btn accent" download style="display:inline-block;">
        ↓ Download ${escapeHTML(r.filename)}
      </a></p>
      <p style="color:var(--muted); font-size:12.5px;">
        To run BORN: get <code>born.jar</code> from
        <code>github.com/julianmendez/born</code>, either drop it into the
        project folder or set the <code>BORN_JAR</code> env var, then in
        <em>Tools → Run reasoner</em> pick <strong>BORN</strong>.
      </p>`;
    modal({title: "BORN file created", content: note, hideFooter: true});
  }
}

async function openQueryModal() {
  const types = await api.queryTypes();
  const sel = selectField({label: "Question",
    options: types.map(t => ({value: t.key, label: t.label}))});
  const cls = textField({label: "Class", list: "qc-dl"});
  const prop = textField({label: "Property", list: "qp-dl"});
  const val = textField({label: "Value", list: "qv-dl"});
  if (state.detail?.kind === "Class") cls.input.value = state.detail.name;
  const results = document.createElement("div");
  const run = document.createElement("button"); run.className = "accent"; run.textContent = "Run query";
  const ctrl = document.createElement("div"); ctrl.style.display="flex"; ctrl.style.gap="8px"; ctrl.style.marginBottom="10px";
  ctrl.appendChild(run);
  const content = document.createElement("div");
  content.appendChild(intro("Pick a question, fill the inputs it needs, then Run. Double-click a result row to jump to it."));
  content.appendChild(sel.el); content.appendChild(cls.el); content.appendChild(prop.el); content.appendChild(val.el);
  content.appendChild(ctrl); content.appendChild(results);

  const dl1 = document.createElement("datalist"); dl1.id = "qc-dl";
  for (const n of classNames()) { const o = document.createElement("option"); o.value = n; dl1.appendChild(o); }
  const dl2 = document.createElement("datalist"); dl2.id = "qp-dl";
  for (const n of [...objPropNames(), ...dataPropNames()]) { const o = document.createElement("option"); o.value = n; dl2.appendChild(o); }
  const dl3 = document.createElement("datalist"); dl3.id = "qv-dl";
  for (const n of [...classNames(), ...indivNames()]) { const o = document.createElement("option"); o.value = n; dl3.appendChild(o); }
  content.appendChild(dl1); content.appendChild(dl2); content.appendChild(dl3);

  function syncEnabled() {
    const t = types.find(t => t.key === sel.get());
    cls.input.disabled  = !t.needs[0];
    prop.input.disabled = !t.needs[1];
    val.input.disabled  = !t.needs[2];
  }
  sel.input.onchange = syncEnabled; syncEnabled();
  run.onclick = async () => {
    results.innerHTML = "Running…";
    try {
      const r = await api.query({key: sel.get(), cls: cls.get(), prop: prop.get(), value: val.get()});
      results.innerHTML = "";
      const head = document.createElement("div"); head.className = "intro";
      head.textContent = `${r.rows.length} result(s)`;
      results.appendChild(head);
      const list = document.createElement("div");
      list.style.maxHeight = "44vh"; list.style.overflow = "auto";
      list.style.border = "1px solid var(--border)";
      list.style.borderRadius = "8px";
      list.style.background = "var(--bg-card2)";
      list.style.padding = "6px";
      for (const row of r.rows) {
        const i = document.createElement("div"); i.className = "row link";
        i.style.padding = "5px 8px"; i.style.borderRadius = "6px"; i.style.cursor = "pointer";
        i.textContent = row.text;
        i.onmouseover = () => i.style.background = "var(--bg-card3)";
        i.onmouseout  = () => i.style.background = "";
        i.ondblclick = () => selectEntity(row.qname);
        list.appendChild(i);
      }
      results.appendChild(list);
    } catch (e) { results.innerHTML = `<div class="error-msg">${escapeHTML(e.message)}</div>`; }
  };
  modal({title: "Competency-question query", content, wide: true, hideFooter: true});
}

// ----- graph view modal ----------------------------------------------

async function openGraphModal() {
  const engines = await api.graphEngines();
  // Default focus: currently-selected class, else the first class in the ontology.
  const init = state.detail?.kind === "Class"
    ? state.detail.name
    : (state.entities.classes[0]?.name || "");

  const content = document.createElement("div");
  content.style.display = "flex"; content.style.flexDirection = "column"; content.style.height = "100%"; content.style.minHeight = "520px";
  content.innerHTML = `
    <div class="graph-toolbar">
      <div class="group"><label>Focus</label>
        <input type="text" id="g-focus" list="g-focus-dl" value="${escapeAttr(init)}"
               style="width:200px;"></div>
      <div class="group"><label>Up</label>
        <input type="number" id="g-up" value="2" min="0" max="20"></div>
      <div class="group"><label>Down</label>
        <input type="number" id="g-down" value="2" min="0" max="20"></div>
      <div class="group"><label><input type="checkbox" id="g-whole"> Whole hierarchy</label></div>
      <div class="group"><label><input type="checkbox" id="g-restr"> Show restrictions</label></div>
      <div class="group"><label><input type="checkbox" id="g-light"> White background</label></div>
      <div class="group"><label>Layout</label>
        <select id="g-engine">${engines.map(e => `<option value="${e.name}">${escapeHTML(e.desc)}</option>`).join("")}</select>
      </div>
      <button class="accent" id="g-render">Render</button>
      <button id="g-save-svg">Download SVG</button>
      <button id="g-save-png">Download PNG</button>
    </div>
    <datalist id="g-focus-dl">${classNames().map(c => `<option value="${escapeAttr(c)}">`).join("")}</datalist>
    <div class="graph-canvas" id="g-canvas">
      <div class="placeholder">Click <strong>Render</strong> to draw the graph.</div>
      <div class="graph-zoom">
        <button data-z="out" title="Zoom out (−)">−</button>
        <button data-z="reset" title="Reset">⟳</button>
        <button data-z="in" title="Zoom in (+)">+</button>
      </div>
    </div>
  `;

  modal({title: "Graph view  (Graphviz · drag to pan, wheel to zoom)",
         content, fullscreen: true, hideFooter: true});

  const canvas = content.querySelector("#g-canvas");
  let scale = 1, tx = 0, ty = 0, svg = null, lastSvgText = "";
  function apply() { if (svg) svg.style.transform = `translate(${tx}px,${ty}px) scale(${scale})`; }
  function zoom(f, cx, cy) {
    const newScale = Math.max(0.08, Math.min(12, scale * f));
    if (cx !== undefined) {
      // zoom centered at cursor
      const r = canvas.getBoundingClientRect();
      const ratio = newScale / scale;
      tx = cx - r.left - (cx - r.left - tx) * ratio;
      ty = cy - r.top  - (cy - r.top  - ty) * ratio;
    }
    scale = newScale; apply();
  }

  function graphParams() {
    const p = new URLSearchParams({
      up:   content.querySelector("#g-up").value,
      down: content.querySelector("#g-down").value,
      whole: content.querySelector("#g-whole").checked,
      restrictions: content.querySelector("#g-restr").checked,
      engine: content.querySelector("#g-engine").value,
      bg: content.querySelector("#g-light").checked ? "light" : "dark",
    });
    const focus = content.querySelector("#g-focus").value.trim();
    if (focus) p.set("focus", focus);
    return p;
  }

  function applyCanvasBg() {
    canvas.style.background =
      content.querySelector("#g-light").checked ? "#ffffff" : "var(--bg-card)";
  }

  async function render() {
    const params = graphParams();
    applyCanvasBg();
    canvas.querySelectorAll(":scope > *").forEach(n => {
      if (!n.classList.contains("graph-zoom")) n.remove();
    });
    const ph = document.createElement("div");
    ph.className = "placeholder"; ph.textContent = "Rendering…";
    canvas.insertBefore(ph, canvas.firstChild);
    try {
      const r = await fetch("/api/graph?" + params);
      if (!r.ok) {
        let msg; try { msg = (await r.json()).detail; } catch { msg = "HTTP " + r.status; }
        throw new Error(msg);
      }
      lastSvgText = await r.text();
      ph.remove();
      const wrap = document.createElement("div");
      wrap.innerHTML = lastSvgText;
      svg = wrap.querySelector("svg");
      if (!svg) throw new Error("No SVG in response.");
      svg.removeAttribute("width"); svg.removeAttribute("height");
      svg.style.display = "block";
      canvas.insertBefore(svg, canvas.querySelector(".graph-zoom"));
      scale = 1; tx = 0; ty = 0; apply();
    } catch (e) {
      ph.textContent = "Error: " + e.message;
      ph.style.color = "var(--red)";
    }
  }

  async function downloadGraph(fmt) {
    const params = graphParams(); params.set("format", fmt);
    try {
      const r = await fetch("/api/graph?" + params);
      if (!r.ok) {
        let msg; try { msg = (await r.json()).detail; } catch { msg = "HTTP " + r.status; }
        throw new Error(msg);
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "ontology_graph." + fmt;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 500);
      toast("Saved ontology_graph." + fmt);
    } catch (e) { toast("Download failed: " + e.message, "error"); }
  }

  content.querySelector("#g-render").onclick = render;
  content.querySelector("#g-light").onchange = () => {
    applyCanvasBg();
    if (lastSvgText) render();          // re-render to recolour nodes/edges
  };
  content.querySelector("#g-save-svg").onclick = () => downloadGraph("svg");
  content.querySelector("#g-save-png").onclick = () => downloadGraph("png");
  content.querySelectorAll(".graph-zoom button").forEach(b => {
    b.onclick = () => {
      if (b.dataset.z === "in")  zoom(1.25);
      else if (b.dataset.z === "out") zoom(1/1.25);
      else { scale = 1; tx = 0; ty = 0; apply(); }
    };
  });
  // pan + wheel zoom on the canvas
  let dragging = false, sx = 0, sy = 0;
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); zoom(Math.exp(-e.deltaY/350), e.clientX, e.clientY); }, {passive: false});
  canvas.addEventListener("mousedown", (e) => {
    if (e.target.closest(".graph-zoom")) return;
    dragging = true; canvas.classList.add("dragging");
    sx = e.clientX - tx; sy = e.clientY - ty;
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return; tx = e.clientX - sx; ty = e.clientY - sy; apply();
  });
  window.addEventListener("mouseup", () => { dragging = false; canvas.classList.remove("dragging"); });

  // render initially
  setTimeout(render, 80);
}

// ----- download helper ------------------------------------------------

function download(filename, content, mime="text/plain") {
  const blob = new Blob([content], {type: mime});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 500);
}

// ----- refresh + init -------------------------------------------------

function rebuildKindMap() {
  // qname -> short kind key used for CSS colour classes
  const m = new Map();
  const e = state.entities || {};
  for (const x of e.classes || [])               m.set(x.qname, "class");
  for (const x of e.object_properties || [])     m.set(x.qname, "objprop");
  for (const x of e.data_properties || [])       m.set(x.qname, "dataprop");
  for (const x of e.individuals || [])           m.set(x.qname, "indiv");
  for (const x of e.annotation_properties || []) m.set(x.qname, "annoprop");
  for (const x of e.datatypes || [])             m.set(x.qname, "datatype");
  state._kinds = m;
}

async function refresh() {
  try {
    const s = await api.state();
    state.ontoState = s;
    if (s.loaded) {
      const [t, e] = await Promise.all([api.tree(), api.entities()]);
      state.tree = t; state.entities = e;
      rebuildKindMap();
    } else {
      state.tree = {roots: []};
      state.entities = {classes: [], object_properties: [],
                         data_properties: [], individuals: [],
                         annotation_properties: [], datatypes: []};
      state.selected = null; state.detail = null;
    }
    renderTopbar(); renderStatus(); renderList(); renderDetail();
  } catch (err) { toast("Failed to refresh: " + err.message, "error"); }
}

// ----- upload / splash screen ---------------------------------------------

function showUploadScreen(prefillMsg = "") {
  hideUploadScreen();
  const scr = document.createElement("div");
  scr.id = "upload-screen-el";
  scr.className = "upload-screen";
  scr.innerHTML = `
    <div class="upload-card">
      <img class="splash-logo" src="/static/logo.png" alt="">
      <h1>Prestige</h1>
      <div class="sub">An OWL ontology editor. Upload a <code>.owl</code> file (OWL 2 Functional Syntax) to begin.</div>
      <label class="drop-zone" id="drop-zone">
        <div class="dz-main">Click to choose a file</div>
        <div class="dz-sub">or drop a <code>.owl</code> here</div>
        <input type="file" id="upload-input" accept=".owl,.ofn,application/xml,text/plain">
      </label>
      <div class="chosen" id="chosen" style="display:none;"></div>
      <button class="accent load-btn" id="upload-btn" disabled>Load ontology</button>
      <div class="error-msg" id="upload-err" style="display:none; margin-top:14px;"></div>
    </div>
  `;
  document.body.appendChild(scr);

  const input  = scr.querySelector("#upload-input");
  const dz     = scr.querySelector("#drop-zone");
  const chosen = scr.querySelector("#chosen");
  const btn    = scr.querySelector("#upload-btn");
  const err    = scr.querySelector("#upload-err");
  let file = null;

  function setFile(f) {
    file = f; btn.disabled = false;
    chosen.style.display = "block";
    chosen.textContent = `${f.name}  ·  ${Math.round(f.size/1024)} KB`;
  }
  input.onchange = (e) => { if (e.target.files[0]) setFile(e.target.files[0]); };
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("dragover"); };
  dz.ondragleave = () => dz.classList.remove("dragover");
  dz.ondrop = (e) => {
    e.preventDefault(); dz.classList.remove("dragover");
    if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
  };
  btn.onclick = async () => {
    if (!file) return;
    err.style.display = "none";
    btn.disabled = true; btn.textContent = "Loading…";
    try {
      await api.load(file);
      hideUploadScreen();
      toast("Loaded " + file.name);
      await refresh();
      const initial = state.entities.classes[0];
      if (initial) await selectEntity(initial.qname);
    } catch (e) {
      err.textContent = e.message;
      err.style.display = "block";
      btn.disabled = false; btn.textContent = "Load ontology";
    }
  };
  if (prefillMsg) { err.textContent = prefillMsg; err.style.display = "block"; }
}

function hideUploadScreen() {
  const el = $("#upload-screen-el");
  if (el) el.remove();
}

function bindShortcuts() {
  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    const tag = (e.target.tagName || "").toLowerCase();
    const inField = tag === "input" || tag === "textarea" || tag === "select";
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); doSave(); }
    else if (mod && e.key.toLowerCase() === "z" && !e.shiftKey) { if (!inField) { e.preventDefault(); doUndo(); } }
    else if (mod && (e.key.toLowerCase() === "y" || (e.shiftKey && e.key.toLowerCase() === "z"))) { if (!inField) { e.preventDefault(); doRedo(); } }
    else if (mod && e.key.toLowerCase() === "n") { e.preventDefault(); openNewClassModal(); }
    else if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); $("#search-input").focus(); $("#search-input").select(); }
  });
}

async function init() {
  $("#search-input").addEventListener("input", (e) => {
    state.search = e.target.value;
    renderList();
  });
  $("#btn-new-class").onclick    = () => state.ontoState?.loaded && openNewClassModal();
  $("#btn-new-subclass").onclick = () => state.selected && openNewClassModal(state.selected);

  renderTabs();
  bindShortcuts();
  ai.load(); ai.render();
  await refresh();
  // Deep-link via ?open=settings / ?open=ai (debug helpers).
  const qs = new URLSearchParams(location.search);
  if (qs.get("ai") === "on") {
    ai.settings.enabled = true;
    if (!ai.settings.apiKey) ai.settings.apiKey = "(your-key-here)";
    ai.render();
  }
  const open = qs.get("open");
  if (open === "settings") setTimeout(openSettingsModal, 120);
  else if (open === "ai") { ai.expanded = true; ai.render(); }
  if (!state.ontoState?.loaded) {
    showUploadScreen();
  } else {
    const initial = state.entities.classes[0];
    if (initial) await selectEntity(initial.qname);
  }
}

window.addEventListener("DOMContentLoaded", init);
