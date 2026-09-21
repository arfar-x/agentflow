#!/usr/bin/env node
/*
 * Declarative agent management for LibreChat -- runs INSIDE the `api`
 * container, driven by scripts/agents.sh (`make agent-export` /
 * `make agent-import`). Not meant to be run by hand.
 *
 * LibreChat stores agents as Mongo documents and their sharing as ACL
 * entries; nothing in librechat.yaml defines them. This script turns both
 * into files (one YAML per agent) and back, using LibreChat's own models
 * and permission methods (the same approach as its shipped
 * config/migrate-agent-permissions.js) rather than raw collection writes.
 *
 * Environment:
 *   AGENTS_MODE  export | import
 *   AGENTS_DIR   directory inside the container to write to / read from
 *   DRY_RUN      1 = import only prints what it would change
 *   OWNER_EMAIL  optional, import only: force this account as owner
 *   MODEL_PROVIDER / MODEL_NAME
 *                optional, import only: use this provider / model for every
 *                agent instead of what the files say, so the files can stay
 *                exactly as committed (see "Provider and model" below)
 *   ALLOW_RENAME 1 = let a file change the name of an agent already in the
 *                database (see "Name guard" below)
 *   ADMIN_EMAIL  already in the container from .env, see "Owner" below
 *
 * File format (version 1):
 *   version: 1
 *   agent:   the agent document exactly as stored, minus deployment-local
 *            fields (see RESERVED below). `id` is kept, so handoff `edges`,
 *            `subagents` and `agent_ids` references stay valid.
 *   access:  grants only -- who else can use the agent. No owner: the
 *            owner is a person, and people differ between deployments, so
 *            import infers it (see "Owner" below). Principals are named,
 *            not ObjectId'd, so files survive a fresh database:
 *              user   -> email
 *              group  -> name + source (+ idOnTheSource)
 *              role   -> role name
 *              public -> everyone
 *            Each grant carries an access role (agent_viewer/editor/owner)
 *            or, if the stored entry matches none, raw permBits. The
 *            author's own owner grant is left out along with `author`.
 *
 * Owner (import): whoever owns the agent is inferred, first match wins:
 *   1. OWNER_EMAIL   optional override, applied to every agent, existing or new
 *   2. `access.owner` in a file (older exports only)
 *   3. the agent's current owner, if it is already in the database
 *   4. ADMIN_EMAIL  (.env), if that account exists here
 *   5. the oldest ADMIN account
 * The owner always holds agent_owner on the agent.
 *
 * Provider and model (import): a file's provider/model may not exist on the
 * instance being imported into. Rather than skip the agent, the importer
 * falls back and says so, so the person importing can fix it in the UI:
 *   1. MODEL_PROVIDER / MODEL_NAME, if set, win (per field, every agent).
 *   2. Otherwise the file's values, if they work here: the provider is
 *      allowed (endpoints.agents.allowedProviders) and resolvable -- checked
 *      with LibreChat's own getProviderConfig against librechat.yaml -- and
 *      the model is one the provider lists (only checkable for a custom
 *      endpoint with `models.default` and `fetch: false`).
 *   3. Otherwise a NEW agent gets this instance's default (the first allowed
 *      / defined custom endpoint and its first listed model), and an agent
 *      ALREADY in the database keeps whatever provider/model it has -- so
 *      what someone then picks in the UI is never overwritten by a file
 *      value that doesn't work here.
 * Every fallback is listed at the end under "Review in the LibreChat UI".
 * The checks read /app/librechat.yaml only.
 *
 * Name guard (import): an agent's id is its identity, so a file whose id
 * belongs to an existing agent with a DIFFERENT name looks like an id
 * collision, not an edit -- and importing it would overwrite that agent.
 * It is skipped unless ALLOW_RENAME=1 (for a real rename).
 *
 * Files may also be written by hand, without ever exporting: only
 * agent.id/name/provider/model are required. Missing fields mean what
 * LibreChat itself would default to (never "delete"), and a file that
 * is wrong (typo'd field, bad type, disallowed provider) skips just that
 * agent with a reason.
 *
 * Import semantics: the file is the source of truth for the agents it
 * lists -- fields missing from a file go back to their default (or are
 * removed if they have none), and
 * grants not in `access.grants` are revoked. Agents that are in the
 * database but have no file are left untouched. A file without an
 * `access` section leaves that agent's existing sharing alone.
 *
 * Only fields the running LibreChat version's agent schema knows are
 * written (Mongoose would silently drop the rest, and the import would
 * then look "changed" on every run). Fields from a newer LibreChat are
 * reported and skipped, and left alone if the database already has them.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const MODE = process.env.AGENTS_MODE;
const DIR = process.env.AGENTS_DIR;
const DRY_RUN = process.env.DRY_RUN === '1';
const OWNER_EMAIL = (process.env.OWNER_EMAIL || '').trim();
const ADMIN_EMAIL = (process.env.ADMIN_EMAIL || '').trim();
const MODEL_PROVIDER = (process.env.MODEL_PROVIDER || '').trim();
const MODEL_NAME = (process.env.MODEL_NAME || '').trim();
const ALLOW_RENAME = process.env.ALLOW_RENAME === '1';
const FORMAT_VERSION = 1;

/**
 * Agent document fields that are not part of an agent's definition:
 * identity/bookkeeping (_id, __v, timestamps, tenantId), history (versions,
 * rebuilt by LibreChat on every change), the owner (inferred on import,
 * see "Owner" above) and mcpServerNames (derived from `tools` by
 * LibreChat on every write).
 */
const RESERVED = new Set([
  '_id',
  '__v',
  'createdAt',
  'updatedAt',
  'tenantId',
  'versions',
  'author',
  'mcpServerNames',
]);

/** Leading keys in exported files, for readable diffs; the rest sort A-Z. */
const KEY_ORDER = [
  'id',
  'name',
  'description',
  'category',
  'provider',
  'model',
  'model_parameters',
  'instructions',
  'tools',
  'edges',
  'subagents',
  'agent_ids',
];

const PRINCIPAL_ORDER = { public: 0, role: 1, group: 2, user: 3 };

const plain = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));

const sortKeys = (_, x) =>
  x && typeof x === 'object' && !Array.isArray(x)
    ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
    : x;
const stable = (v) => JSON.stringify(v, sortKeys);

function orderedAgent(agent) {
  const out = {};
  for (const k of KEY_ORDER) {
    if (k in agent) out[k] = agent[k];
  }
  for (const k of Object.keys(agent).sort()) {
    if (!(k in out)) out[k] = agent[k];
  }
  return out;
}

/** Agent ids an agent points at via handoff edges, subagents or the legacy agent_ids. */
function referencedAgentIds(agent) {
  const found = new Set();
  const walk = (v) => {
    if (typeof v === 'string') {
      if (/^agent_[A-Za-z0-9_-]+$/.test(v)) found.add(v);
    } else if (Array.isArray(v)) {
      v.forEach(walk);
    } else if (v && typeof v === 'object') {
      Object.values(v).forEach(walk);
    }
  };
  walk(agent.edges);
  walk(agent.agent_ids);
  walk(agent.subagents);
  found.delete(agent.id);
  return found;
}

/** Fields that point outside the agent document and won't exist in a fresh database. */
function nonPortableNotes(agent) {
  const notes = [];
  if (Array.isArray(agent.actions) && agent.actions.length > 0) {
    notes.push('has Actions (stored in a separate collection, not exported)');
  }
  const fileIds = Object.values(agent.tool_resources || {}).flatMap((r) => (r && r.file_ids) || []);
  if (fileIds.length > 0) {
    notes.push('has attached files (stored in a separate collection, not exported)');
  }
  return notes;
}

/** Edit distance, for "did you mean" on hand-typed field names. */
function editDistance(a, b) {
  const row = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    let prev = row[0];
    row[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const tmp = row[j];
      row[j] = Math.min(row[j] + 1, row[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = tmp;
    }
  }
  return row[b.length];
}

function slugify(name) {
  return String(name || '')
    .normalize('NFKD')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function loadModels() {
  // connect.js registers the `~` module alias (-> /app/api) LibreChat's own
  // config scripts rely on, and connects to the database.
  const connect = require('/app/config/connect');
  return connect().then(() => {
    const models = require('~/db/models');
    const methods = require('~/models');
    const mongoose = require('mongoose');
    return { mongoose, models, methods };
  });
}

// ---------------------------------------------------------------- export

async function exportAgents({ models }) {
  const yaml = require('js-yaml');
  const { Agent, AclEntry, AccessRole, User, Group } = models;

  const agents = await Agent.find({}).sort({ name: 1, id: 1 }).lean();
  const acl = await AclEntry.find({
    resourceType: 'agent',
    resourceId: { $in: agents.map((a) => a._id) },
  }).lean();
  const accessRoles = await AccessRole.find({ resourceType: 'agent' }).lean();
  const roleById = new Map(accessRoles.map((r) => [String(r._id), r]));
  const ownerBits = (accessRoles.find((r) => r.accessRoleId === 'agent_owner') || {}).permBits;

  const userIds = new Set(
    [...agents.map((a) => a.author), ...acl.filter((e) => e.principalType === 'user').map((e) => e.principalId)]
      .filter(Boolean)
      .map(String),
  );
  const emailById = new Map(
    (await User.find({ _id: { $in: [...userIds] } }, 'email').lean()).map((u) => [
      String(u._id),
      u.email,
    ]),
  );
  const groupIds = acl.filter((e) => e.principalType === 'group').map((e) => e.principalId);
  const groupById = new Map(
    (await Group.find({ _id: { $in: groupIds } }).lean()).map((g) => [String(g._id), g]),
  );

  const slugCounts = new Map();
  for (const a of agents) {
    const s = slugify(a.name) || a.id;
    slugCounts.set(s, (slugCounts.get(s) || 0) + 1);
  }

  fs.mkdirSync(DIR, { recursive: true });
  const warnings = [];

  for (const a of agents) {
    const grants = [];
    for (const e of acl.filter((x) => String(x.resourceId) === String(a._id))) {
      // The author's own owner grant is implied by ownership, not declared.
      if (
        e.principalType === 'user' &&
        String(e.principalId) === String(a.author) &&
        e.permBits === ownerBits
      ) {
        continue;
      }
      let principal;
      if (e.principalType === 'public') {
        principal = { type: 'public' };
      } else if (e.principalType === 'role') {
        principal = { type: 'role', name: String(e.principalId) };
      } else if (e.principalType === 'user') {
        const email = emailById.get(String(e.principalId));
        if (!email) {
          warnings.push(`${a.id}: skipped a grant to a user that no longer exists (${e.principalId})`);
          continue;
        }
        principal = { type: 'user', email };
      } else if (e.principalType === 'group') {
        const g = groupById.get(String(e.principalId));
        if (!g) {
          warnings.push(`${a.id}: skipped a grant to a group that no longer exists (${e.principalId})`);
          continue;
        }
        principal = { type: 'group', name: g.name, source: g.source || 'local' };
        if (g.idOnTheSource) principal.idOnTheSource = g.idOnTheSource;
      } else {
        warnings.push(`${a.id}: skipped a grant with unknown principal type "${e.principalType}"`);
        continue;
      }
      const role = e.roleId ? roleById.get(String(e.roleId)) : undefined;
      const grant = { principal };
      if (role && role.permBits === e.permBits) grant.role = role.accessRoleId;
      else grant.permBits = e.permBits;
      if (e.expiredAt) grant.expiresAt = new Date(e.expiredAt).toISOString();
      grants.push(grant);
    }
    const identity = (g) => g.principal.email || g.principal.name || '';
    grants.sort(
      (x, y) =>
        PRINCIPAL_ORDER[x.principal.type] - PRINCIPAL_ORDER[y.principal.type] ||
        identity(x).localeCompare(identity(y)),
    );

    const agent = {};
    for (const [k, v] of Object.entries(a)) {
      if (!RESERVED.has(k) && v !== undefined) agent[k] = plain(v);
    }
    const doc = {
      version: FORMAT_VERSION,
      agent: orderedAgent(agent),
      access: { grants },
    };
    for (const note of nonPortableNotes(a)) warnings.push(`${a.id}: ${note}`);

    const text = yaml.dump(doc, { lineWidth: -1, noRefs: true });
    // An export that can't be read back identically is worse than no export.
    if (stable(yaml.load(text)) !== stable(plain(doc))) {
      throw new Error(`${a.id}: YAML round-trip mismatch, refusing to write an inexact export`);
    }

    const base = slugify(a.name) || a.id;
    const file =
      (slugCounts.get(base) > 1 ? `${base}-${a.id.replace(/^agent_/, '').slice(0, 6)}` : base) +
      '.yaml';
    const header =
      `# ${a.name}\n# Generated by \`make agent-export\` -- edit freely, apply with \`make agent-import\`.\n`;
    fs.writeFileSync(path.join(DIR, file), header + text);
    console.log(`exported  ${a.id}  ${file}  (${grants.length} grant${grants.length === 1 ? '' : 's'})`);
  }

  for (const w of warnings) console.error(`warning: ${w}`);
  console.log(`\n${agents.length} agent(s) exported.`);
  return 0;
}

// ---------------------------------------------------------------- import

function loadFiles(problems) {
  const yaml = require('js-yaml');
  const files = fs
    .readdirSync(DIR)
    .filter((f) => /\.ya?ml$/.test(f))
    .sort();
  const parsed = [];
  const seen = new Map();
  nextFile: for (const file of files) {
    let doc;
    try {
      doc = yaml.load(fs.readFileSync(path.join(DIR, file), 'utf8'));
    } catch (e) {
      problems.push(`${file}: not valid YAML (${e.message.split('\n')[0]})`);
      continue;
    }
    if (!doc || typeof doc !== 'object') {
      problems.push(`${file}: empty file -- expected \`agent:\` (and optionally \`access:\`)`);
      continue;
    }
    if (doc.version !== undefined && doc.version !== FORMAT_VERSION) {
      problems.push(`${file}: unsupported \`version\` ${doc.version} (this importer reads ${FORMAT_VERSION})`);
      continue;
    }
    const agent = doc.agent;
    if (!agent || typeof agent.id !== 'string' || !/^agent_[A-Za-z0-9_-]+$/.test(agent.id)) {
      problems.push(`${file}: agent.id must look like "agent_<letters/digits/-/_>"`);
      continue;
    }
    for (const req of ['name', 'provider', 'model']) {
      if (typeof agent[req] !== 'string' || !agent[req].trim()) {
        problems.push(`${file}: agent.${req} is required (a non-empty string)`);
        continue nextFile;
      }
    }
    if (seen.has(agent.id)) {
      problems.push(`${file}: duplicate agent.id ${agent.id} (also in ${seen.get(agent.id)})`);
      continue;
    }
    seen.set(agent.id, file);
    const bad = Object.keys(agent).filter((k) => RESERVED.has(k));
    if (bad.length) {
      problems.push(`${file}: agent must not set ${bad.join(', ')} (managed by LibreChat)`);
      continue;
    }
    parsed.push({ file, agent: plain(agent), access: doc.access });
  }
  return parsed;
}

async function importAgents({ mongoose, models, methods }) {
  const { Agent, AclEntry, AccessRole, User, Group } = models;
  const Role = mongoose.models.Role;
  const problems = [];
  // Per-agent problems, grouped by message: the same missing user usually
  // affects every file, and one line per file buries the signal.
  const grouped = new Map();
  const note = (file, msg) => {
    if (!grouped.has(msg)) grouped.set(msg, []);
    grouped.get(msg).push(file);
  };

  if (!fs.existsSync(DIR)) throw new Error(`no such directory: ${DIR}`);
  const entries = loadFiles(problems);

  const knownFields = new Set(Object.keys(Agent.schema.paths).map((p) => p.split('.')[0]));
  // What LibreChat itself fills in when it creates an agent (category, empty
  // edges/agent_ids/..., is_promoted). A file that doesn't mention them means
  // "the default", not "remove it" -- otherwise a minimal hand-written file
  // would strip them on the second run.
  const schemaDefaults = Object.fromEntries(
    Object.entries(plain(new Agent({ id: 'agent_defaults', name: 'defaults' }).toObject())).filter(
      ([k]) => !RESERVED.has(k) && k !== 'id' && k !== 'name',
    ),
  );
  // -- provider / model availability, from this instance's librechat.yaml ----
  let cfg = null; // null = unreadable: nothing below can be checked, so nothing is replaced
  try {
    cfg = require('js-yaml').load(fs.readFileSync('/app/librechat.yaml', 'utf8')) || {};
  } catch (_) {
    /* no readable librechat.yaml */
  }
  const { getProviderConfig } = require('@librechat/api');
  const customEndpoints = cfg && cfg.endpoints && Array.isArray(cfg.endpoints.custom) ? cfg.endpoints.custom : [];
  const allowedList = cfg && cfg.endpoints && cfg.endpoints.agents && cfg.endpoints.agents.allowedProviders;
  const allowedProviders = Array.isArray(allowedList) && allowedList.length ? allowedList.map(String) : null;

  /** null if `provider` works for agents here, otherwise why not. */
  const providerProblem = (provider) => {
    if (!cfg) return null;
    if (allowedProviders && !allowedProviders.includes(provider)) {
      return `is not in endpoints.agents.allowedProviders (${allowedProviders.join(', ')})`;
    }
    try {
      getProviderConfig({ provider, appConfig: { endpoints: { custom: customEndpoints } } });
      return null;
    } catch (_) {
      const names = customEndpoints.map((e) => e && e.name).filter(Boolean);
      return `is neither a built-in provider nor a custom endpoint in librechat.yaml (${names.join(', ') || 'none defined'})`;
    }
  };
  /** The models a custom endpoint lists, or null when that can't be known (built-in provider, or it fetches its list). */
  const listedModels = (provider) => {
    const ep = customEndpoints.find((e) => e && e.name === provider);
    const m = ep && ep.models;
    return m && Array.isArray(m.default) && m.default.length && m.fetch !== true ? m.default.map(String) : null;
  };
  const modelProblem = (provider, model) => {
    const listed = listedModels(provider);
    return listed && !listed.includes(model) ? `is not among the models ${provider} lists (${listed.join(', ')})` : null;
  };
  /** What a new agent gets when its file's provider/model don't work here. */
  const instanceDefault = (() => {
    for (const p of allowedProviders || customEndpoints.map((e) => e && e.name).filter(Boolean)) {
      const listed = providerProblem(p) === null ? listedModels(p) : null;
      if (listed) return { provider: p, model: listed[0] };
    }
    return null;
  })();
  const reviews = []; // agents whose provider/model were replaced or kept -- see the end of the run

  /**
   * Provider/model an agent ends up with (rules in the header). Returns
   * { provider, model, review? } or { blocker }.
   */
  function resolveModel(fileProvider, fileModel, existing) {
    let provider = MODEL_PROVIDER || fileProvider;
    let model = MODEL_NAME || fileModel;
    if (!cfg) return { provider, model };
    const notes = [];
    let providerReplaced = false;
    if (!MODEL_PROVIDER) {
      const why = providerProblem(fileProvider);
      if (why) {
        if (existing) provider = existing.provider;
        else if (instanceDefault) provider = instanceDefault.provider;
        else return { blocker: `provider "${fileProvider}" ${why}, and this instance has no default provider/model to fall back to` };
        providerReplaced = true;
        notes.push(`provider "${fileProvider}" ${why}`);
      }
    }
    if (!MODEL_NAME) {
      const why = providerReplaced ? `belongs to ${fileProvider}` : modelProblem(provider, fileModel);
      if (why) {
        const keep = existing && existing.provider === provider && !modelProblem(provider, existing.model);
        const listed = listedModels(provider);
        if (keep) model = existing.model;
        else if (providerReplaced && instanceDefault && provider === instanceDefault.provider) model = instanceDefault.model;
        else if (listed) model = listed[0];
        if (!providerReplaced) notes.push(`model "${fileModel}" ${why}`);
      }
    }
    if (!notes.length) return { provider, model };
    const source = existing ? 'kept the agent\'s current' : 'using this instance\'s';
    return {
      provider,
      model,
      review: `file asks for ${fileProvider} / ${fileModel}, but ${notes.join(' and ')} -- ${source} ${provider} / ${model}`,
    };
  }
  const dbAgents = new Map((await Agent.find({}).lean()).map((a) => [a.id, a]));
  const knownIds = new Set([...dbAgents.keys(), ...entries.map((e) => e.agent.id)]);

  // -- principals -----------------------------------------------------------
  const cache = new Map();
  const memo = async (key, fn) => {
    if (!cache.has(key)) cache.set(key, await fn());
    return cache.get(key);
  };
  async function resolvePrincipal(p) {
    if (!p || typeof p !== 'object') throw new Error('principal must be a mapping');
    switch (p.type) {
      case 'public':
        return { principalType: 'public', principalId: null };
      case 'role': {
        const role = await memo(`role:${p.name}`, () => Role.findOne({ name: p.name }, 'name').lean());
        if (!role) throw new Error(`role "${p.name}" does not exist`);
        return { principalType: 'role', principalId: role.name };
      }
      case 'user': {
        const email = String(p.email || '').toLowerCase();
        const user = await memo(`user:${email}`, () => User.findOne({ email }, '_id').lean());
        if (!user) throw new Error(`user ${email || '(no email)'} does not exist`);
        return { principalType: 'user', principalId: user._id };
      }
      case 'group': {
        const q = { name: p.name, source: p.source || 'local' };
        if (p.idOnTheSource) q.idOnTheSource = p.idOnTheSource;
        const group = await memo(`group:${stable(q)}`, () => Group.findOne(q, '_id').lean());
        if (!group) throw new Error(`group "${p.name}" (${q.source}) does not exist`);
        return { principalType: 'group', principalId: group._id };
      }
      default:
        throw new Error(`unknown principal type "${p.type}"`);
    }
  }
  const accessRoleCache = new Map();
  async function resolveGrantBits(grant) {
    if (grant.role) {
      if (!accessRoleCache.has(grant.role)) {
        accessRoleCache.set(grant.role, await AccessRole.findOne({ accessRoleId: grant.role }).lean());
      }
      const role = accessRoleCache.get(grant.role);
      if (!role || role.resourceType !== 'agent') throw new Error(`unknown agent access role "${grant.role}"`);
      return { permBits: role.permBits, roleId: role._id };
    }
    if (Number.isInteger(grant.permBits)) return { permBits: grant.permBits, roleId: undefined };
    throw new Error('grant needs either `role` or `permBits`');
  }

  // Owner inference -- see the header. The override is checked up front: an
  // explicit request that can't be honored should stop the run, not quietly
  // fall back to someone else.
  const findUser = (email) => User.findOne({ email: String(email).trim().toLowerCase() }, '_id email').lean();
  const ownerOverride = OWNER_EMAIL ? await findUser(OWNER_EMAIL) : null;
  if (OWNER_EMAIL && !ownerOverride) {
    throw new Error(`OWNER_EMAIL ${OWNER_EMAIL} is not an account on this LibreChat -- create it first (make user-create)`);
  }
  const adminEnvUser = !ownerOverride && ADMIN_EMAIL ? await findUser(ADMIN_EMAIL) : null;
  const oldestAdmin = await User.findOne({ role: 'ADMIN' }, '_id email').sort({ createdAt: 1 }).lean();
  const defaultOwner = ownerOverride || adminEnvUser || oldestAdmin;
  const ownerSource = ownerOverride
    ? 'OWNER_EMAIL'
    : adminEnvUser
      ? 'ADMIN_EMAIL'
      : 'the oldest admin account';

  console.log(
    `${DRY_RUN ? 'Dry run -- nothing will be written. ' : ''}` +
      `${entries.length} agent file(s), ${dbAgents.size} agent(s) in the database.`,
  );
  if (MODEL_PROVIDER || MODEL_NAME) {
    console.log(
      `Model override: ${MODEL_PROVIDER ? `provider ${MODEL_PROVIDER}` : 'provider from each file'}, ` +
        `${MODEL_NAME ? `model ${MODEL_NAME}` : 'model from each file'} (applied to every agent).`,
    );
    const odd = [
      MODEL_PROVIDER && providerProblem(MODEL_PROVIDER) ? `MODEL_PROVIDER ${MODEL_PROVIDER} ${providerProblem(MODEL_PROVIDER)}` : null,
      MODEL_PROVIDER && MODEL_NAME && !providerProblem(MODEL_PROVIDER) && modelProblem(MODEL_PROVIDER, MODEL_NAME)
        ? `MODEL_NAME ${MODEL_NAME} ${modelProblem(MODEL_PROVIDER, MODEL_NAME)}`
        : null,
    ].filter(Boolean);
    for (const o of odd) console.log(`  warning: ${o} -- applied as given`);
  }
  console.log(
    defaultOwner
      ? `${ownerOverride ? 'Owner of every agent' : 'Owner of new agents'}: ${defaultOwner.email} (${ownerSource}).` +
          `${!ownerOverride && ADMIN_EMAIL && !adminEnvUser ? ` ADMIN_EMAIL ${ADMIN_EMAIL} is not an account here.` : ''}\n`
      : 'No account can own new agents (no OWNER_EMAIL, ADMIN_EMAIL or admin user found) -- new agents will be skipped.\n',
  );

  const tally = { created: 0, updated: 0, unchanged: 0, skipped: 0 };
  const skippedFieldNames = new Set();

  async function processEntry({ file, agent, access }) {
    const label = `${agent.id}  ${agent.name}`;
    const errs = []; // reported, but the agent is still applied
    const blockers = []; // reported, and the agent is skipped

    const existing = dbAgents.get(agent.id);

    // Owner: OWNER_EMAIL > `access.owner` (older exports) > the agent's
    // current owner > the default owner above. See the header.
    let owner = ownerOverride;
    if (!owner && access && access.owner) owner = await findUser(access.owner);
    if (!owner && existing && existing.author) owner = { _id: existing.author };
    if (!owner) owner = defaultOwner;
    if (!owner) errs.push('no account available to own this agent -- run `make up` to create the admin, or set OWNER_EMAIL');

    // Declared grants
    let declared = null; // null = leave existing sharing alone
    if (access && Array.isArray(access.grants)) {
      declared = [];
      const legacyOwner = access.owner ? String(access.owner).toLowerCase() : null;
      for (const grant of access.grants) {
        // Older exports listed the owner's own grant; ownership is inferred now.
        if (
          legacyOwner &&
          grant.role === 'agent_owner' &&
          grant.principal && grant.principal.type === 'user' &&
          String(grant.principal.email || '').toLowerCase() === legacyOwner
        ) {
          continue;
        }
        try {
          const principal = await resolvePrincipal(grant.principal);
          const bits = await resolveGrantBits(grant);
          declared.push({ ...principal, ...bits, expiredAt: grant.expiresAt ? new Date(grant.expiresAt) : undefined });
        } catch (e) {
          errs.push(`grant skipped: ${e.message}`);
        }
      }
    }

    // Dangling handoff / subagent references
    for (const ref of referencedAgentIds(agent)) {
      if (!knownIds.has(ref)) errs.push(`references ${ref}, which has no file and is not in the database`);
    }
    for (const note of nonPortableNotes(agent)) console.log(`  note: ${agent.id} ${note}`);

    // Field names, types, required fields. Checked in dry runs too, so a
    // preview can't promise a create that would fail.
    const { id, ...allFields } = agent;
    const skippedFields = Object.keys(allFields).filter((k) => !knownFields.has(k));
    for (const k of skippedFields) {
      const near = [...knownFields].find((f) => f !== k && !RESERVED.has(f) && editDistance(k, f) <= 2);
      if (near) blockers.push(`unknown field "${k}" -- did you mean "${near}"?`);
    }
    const fields = Object.fromEntries(Object.entries(allFields).filter(([k]) => knownFields.has(k)));
    const desired = { ...schemaDefaults, ...fields };
    const resolved = resolveModel(agent.provider, agent.model, existing);
    if (resolved.blocker) {
      blockers.push(resolved.blocker);
    } else {
      desired.provider = resolved.provider;
      desired.model = resolved.model;
      if (resolved.review) reviews.push({ id, name: agent.name, msg: resolved.review });
    }
    if (existing && existing.name && existing.name !== desired.name && !ALLOW_RENAME) {
      blockers.push(
        `id ${id} belongs to the existing agent "${existing.name}", but this file calls it "${desired.name}". ` +
          'If it is the same agent being renamed, re-run with ALLOW_RENAME=1; otherwise give this file its own id',
      );
    }
    if (owner) {
      const invalid = new Agent({ ...desired, id, author: owner._id }).validateSync();
      if (invalid) blockers.push(`invalid agent: ${Object.values(invalid.errors).map((e) => e.message).join('; ')}`);
    }

    if (blockers.length || !owner) {
      [...blockers, ...errs].forEach((m) => note(file, m));
      console.log(`skipped   ${label}`);
      tally.skipped++;
      return;
    }

    const ownerChanged = existing ? String(existing.author) !== String(owner._id) : false;

    // Agent document
    skippedFields.forEach((k) => skippedFieldNames.add(k));
    let action = 'unchanged';
    let detail = [];
    let dbDoc = existing;

    if (!existing) {
      action = 'created';
      if (!DRY_RUN) dbDoc = await methods.createAgent({ ...desired, id, author: owner._id });
    } else {
      const current = plain(existing);
      const changed = Object.keys(desired).filter((k) => stable(desired[k]) !== stable(current[k]));
      const stale = Object.keys(current).filter(
        (k) => !RESERVED.has(k) && k !== 'id' && knownFields.has(k) && !(k in desired),
      );
      if (changed.length || stale.length || ownerChanged) {
        action = 'updated';
        detail = [...changed, ...stale.map((k) => `-${k}`), ...(ownerChanged ? ['owner'] : [])];
        if (!DRY_RUN) {
          if (changed.length) {
            const set = Object.fromEntries(changed.map((k) => [k, desired[k]]));
            await methods.updateAgent({ id }, set);
          }
          if (stale.length) {
            await Agent.updateOne({ id }, { $unset: Object.fromEntries(stale.map((k) => [k, ''])) });
          }
          if (ownerChanged) await Agent.updateOne({ id }, { $set: { author: owner._id } });
        }
      }
    }

    // Sharing
    if (declared === null && (!existing || ownerChanged)) {
      // No access section. A new agent gets none beyond its owner (same as
      // one created in the UI); for an existing agent whose owner is
      // changing, keep its current sharing and only add the new owner.
      declared = existing
        ? (await AclEntry.find({ resourceType: 'agent', resourceId: existing._id }).lean()).map((e) => ({
            principalType: e.principalType,
            principalId: e.principalId,
            permBits: e.permBits,
            roleId: e.roleId,
            expiredAt: e.expiredAt,
          }))
        : [];
    }
    if (declared !== null) {
      // The owner always holds owner access -- unless the file grants that
      // account something explicit.
      const has = declared.some((g) => g.principalType === 'user' && String(g.principalId) === String(owner._id));
      if (!has) declared.push({ principalType: 'user', principalId: owner._id, ...(await resolveGrantBits({ role: 'agent_owner' })) });
    }
    let acl = null;
    if (declared !== null) {
      const existingAcl = existing ? await AclEntry.find({ resourceType: 'agent', resourceId: existing._id }).lean() : [];
      const keyOf = (e) => `${e.principalType}:${e.principalId == null ? '' : String(e.principalId)}`;
      const have = new Map(existingAcl.map((e) => [keyOf(e), e]));
      const want = new Map(declared.map((g) => [keyOf(g), g]));
      const add = [];
      const change = [];
      const remove = [];
      for (const [k, g] of want) {
        const e = have.get(k);
        if (!e) add.push(g);
        else if (
          e.permBits !== g.permBits ||
          String(e.roleId || '') !== String(g.roleId || '') ||
          String(e.expiredAt ? e.expiredAt.getTime() : '') !== String(g.expiredAt ? g.expiredAt.getTime() : '')
        ) {
          change.push(g);
        }
      }
      for (const [k, e] of have) if (!want.has(k)) remove.push(e);
      acl = { add: add.length, change: change.length, remove: remove.length };
      if (!DRY_RUN && dbDoc) {
        for (const g of [...add, ...change]) {
          await methods.grantPermission(g.principalType, g.principalId, 'agent', dbDoc._id, g.permBits, owner._id, undefined, g.roleId, g.expiredAt);
        }
        for (const e of remove) await AclEntry.deleteOne({ _id: e._id });
      }
    }

    const aclChanged = acl && (acl.add || acl.change || acl.remove);
    if (action === 'unchanged' && aclChanged) action = 'updated';
    tally[action === 'created' ? 'created' : action === 'updated' ? 'updated' : 'unchanged']++;
    const parts = [];
    if (detail.length) parts.push(`fields: ${detail.join(', ')}`);
    if (aclChanged) parts.push(`access: +${acl.add} ~${acl.change} -${acl.remove}`);
    console.log(`${action.padEnd(9)} ${label}${parts.length ? `  (${parts.join('; ')})` : ''}`);
    errs.forEach((m) => note(file, m));
  }

  // One bad file must not stop the others (or leave the run half-reported).
  for (const entry of entries) {
    try {
      await processEntry(entry);
    } catch (e) {
      note(entry.file, `failed: ${e.message}`);
      console.log(`failed    ${entry.agent.id}  ${entry.agent.name}`);
      tally.skipped++;
    }
  }

  if (reviews.length) {
    console.log('\nReview in the LibreChat UI (Agent Builder -> Model) -- these agents were imported with a different provider/model than their file names:');
    for (const r of reviews) console.log(`  ${r.id}  ${r.name}: ${r.msg}`);
  }
  if (skippedFieldNames.size) {
    console.log(
      `note: ignored fields that aren't in this LibreChat version's agent schema: ${[...skippedFieldNames].sort().join(', ')}`,
    );
  }
  const untouched = [...dbAgents.values()].filter((a) => !entries.some((e) => e.agent.id === a.id));
  console.log(
    `\n${tally.created} created, ${tally.updated} updated, ${tally.unchanged} unchanged` +
      `${tally.skipped ? `, ${tally.skipped} skipped` : ''}.` +
      `${DRY_RUN ? ' (dry run)' : ''}`,
  );
  if (untouched.length) {
    console.log(`${untouched.length} agent(s) in the database have no file and were left untouched:`);
    for (const a of untouched) console.log(`  ${a.id}  ${a.name}`);
  }
  const count = problems.length + grouped.size;
  if (count) {
    console.error(`\n${count} problem(s):`);
    for (const p of problems) console.error(`  - ${p}`);
    for (const [msg, files] of grouped) {
      const where = files.length > 3 ? `${files.length} files` : files.join(', ');
      console.error(`  - ${msg}  [${where}]`);
    }
    return 1;
  }
  return 0;
}

// ------------------------------------------------------------------ main

(async () => {
  if (!['export', 'import'].includes(MODE) || !DIR) {
    console.error('AGENTS_MODE (export|import) and AGENTS_DIR are required -- run via scripts/agents.sh');
    process.exit(2);
  }
  let code = 1;
  let ctx;
  try {
    ctx = await loadModels();
    code = MODE === 'export' ? await exportAgents(ctx) : await importAgents(ctx);
  } catch (e) {
    console.error(`error: ${e.message}`);
  } finally {
    if (ctx) await ctx.mongoose.disconnect();
  }
  process.exit(code);
})();
