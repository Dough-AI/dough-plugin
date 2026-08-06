# Proposal Evidence Channel — Dough-Alpha Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `proposals.propose` a way to carry a session transcript and backing files that were uploaded out-of-band, so evidence never has to travel through model-generated tool arguments.

**Architecture:** A new `proposals.beginEvidence` tool mints one credential-free presigned PUT per declared object and records a pending evidence set. A client uploads bytes directly to storage, then calls `proposals.propose` with `transcript: { evidenceId, sessionId, manifest }`. On consume, the server streams each object, verifies its declared sha256, writes the transcript to `proposal_transcripts.objectPath`, and inserts one `proposal_evidence` row per file. Unconsumed sets expire after 1 hour and are swept.

**Tech Stack:** Next.js (App Router), Drizzle ORM + Postgres, Supabase Storage, Zod, Vitest.

**Repo:** `/usr/local/code/Dough-Alpha`
**Companion plan:** the client half lives in `dough-plugin` and builds against the same contract in parallel. Do not wait for it.
**Design spec:** `/usr/local/code/dough-plugin/docs/superpowers/specs/2026-08-05-propose-command-evidence-design.md`

## Global Constraints

- Per-object size cap: **25 MB**, matching `MAX_CONTENT_BYTES` in `src/features/registry/observability.ts:30`.
- Unconsumed evidence set TTL: **1 hour**. Presigned URL lifetime matches.
- An `evidenceId` is **single-use** and **org-bound** — consumable exactly once, only by the org that minted it.
- Integrity is **strict**: on consume, stream every object and verify sha256 against the client's declaration. Mismatch refuses the proposal.
- `proposal_transcripts` keeps its existing check constraint: exactly one of `content` / `objectPath` is set, never both, never neither.
- Existing behaviour must not regress — a `proposals.propose` call with an inline `transcript` object (no `evidenceId`) keeps writing `content` exactly as today.
- Tests: `pnpm test` (Vitest). Migrations: `pnpm db:generate` then `pnpm db:migrate`.
- Follow the house comment style in `src/db/schema.ts` — explain *why* a constraint exists, not what it does.

## The frozen contract

Both repos build against this. Do not change it without updating the companion plan.

**`proposals.beginEvidence` request:**

```jsonc
{
  "sessionId": "440239c9-fca1-433c-968f-bfe670ed634d",
  "objects": [
    { "key": "transcript", "role": "transcript",
      "filename": "session.jsonl", "mime": "application/x-ndjson",
      "bytes": 187829, "sha256": "a3f9…" },
    { "key": "f0", "role": "file",
      "filename": "contractors.csv", "mime": "text/csv",
      "bytes": 4210, "sha256": "7c21…" }
  ]
}
```

**Response:**

```jsonc
{
  "evidenceId": "ev_01JQ…",
  "expiresAt": "2026-08-05T16:45:00Z",
  "uploads": [
    { "key": "transcript", "method": "PUT", "url": "https://…", "headers": {} }
  ],
  "rejected": [
    { "key": "f2", "code": "over_object_cap",
      "message": "31 MB exceeds the 25 MB limit" }
  ]
}
```

**Consumption** — rides inside the existing `transcript` field on `proposals.propose`:

```jsonc
"transcript": {
  "evidenceId": "ev_01JQ…",
  "sessionId": "440239c9-…",
  "manifest": [
    { "key": "f0", "filename": "contractors.csv", "sha256": "7c21…",
      "bytes": 4210, "mime": "text/csv", "role": "file",
      "note": "sourced the $8,400.50" }
  ]
}
```

---

### Task 1: Schema for evidence sets, evidence rows, and status

**Files:**
- Modify: `src/db/schema.ts` (append after `proposalTranscripts`, ~line 1804)
- Create: `drizzle/00NN_proposal_evidence.sql` (generated)
- Test: `src/features/proposals/evidence-schema.test.ts`

**Interfaces:**
- Consumes: `proposals`, `proposalTranscripts` from `src/db/schema.ts`
- Produces: `proposalEvidenceSets`, `proposalEvidence` table exports; `proposals.evidenceStatus` column

- [ ] **Step 1: Write the failing test**

```ts
// src/features/proposals/evidence-schema.test.ts
import { describe, expect, it } from "vitest"
import { proposalEvidence, proposalEvidenceSets, proposals } from "@/db/schema"

describe("evidence schema", () => {
  it("keys an evidence set by its public evidence id", () => {
    expect(proposalEvidenceSets.evidenceId.primary).toBe(true)
  })

  it("binds an evidence set to an org so it cannot be consumed cross-tenant", () => {
    expect(proposalEvidenceSets.workosOrganizationId.notNull).toBe(true)
  })

  it("records consumption so an evidence id cannot be replayed", () => {
    expect(proposalEvidenceSets.consumedAt).toBeDefined()
  })

  it("stores one evidence row per file with its verified hash", () => {
    expect(proposalEvidence.sha256.notNull).toBe(true)
    expect(proposalEvidence.storageObjectPath.notNull).toBe(true)
  })

  it("gives every proposal an evidence status", () => {
    expect(proposals.evidenceStatus.notNull).toBe(true)
    expect(proposals.evidenceStatus.default).toBe("none")
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm test src/features/proposals/evidence-schema.test.ts`
Expected: FAIL — `proposalEvidenceSets` is not exported from `@/db/schema`

- [ ] **Step 3: Add the tables and the column**

Append to `src/db/schema.ts` after the `proposalTranscripts` block:

```ts
/**
 * An intent to upload evidence, minted before any byte moves.
 *
 * Two-phase because the bytes cannot travel through a model-generated tool
 * argument: the client declares what it is about to send, gets presigned URLs,
 * uploads directly to storage, and only then proposes. This row is what links
 * those two calls together, and what a sweep collects when the second never
 * comes.
 */
export const proposalEvidenceSets = pgTable(
  "proposal_evidence_sets",
  {
    evidenceId: text("evidence_id").primaryKey(),
    // Org-bound at mint time. Without this an evidence id is a handle for
    // attaching one org's session to another org's proposal.
    workosOrganizationId: text("workos_organization_id").notNull(),
    // Who minted it, for the audit trail and for separation-of-duties reads.
    userId: uuid("user_id"),
    // The Claude Code session this evidence came from.
    sessionId: text("session_id").notNull(),
    // What the client SAID it would upload: [{key, role, filename, mime, bytes,
    // sha256, storageObjectPath}]. Verified against reality on consume.
    declared: jsonb("declared").$type<Record<string, unknown>[]>().notNull(),
    expiresAt: timestamp("expires_at", { withTimezone: true, mode: "date" }).notNull(),
    // Set exactly once. A second propose citing the same id is refused rather
    // than silently re-attaching evidence to a different entry.
    consumedAt: timestamp("consumed_at", { withTimezone: true, mode: "date" }),
    createdAt: timestamp("created_at", { withTimezone: true, mode: "date" })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    index("proposal_evidence_sets_sweep_idx").on(table.expiresAt, table.consumedAt),
    index("proposal_evidence_sets_org_idx").on(table.workosOrganizationId),
  ],
)

/**
 * One uploaded file behind a proposal.
 *
 * Separate from `proposal_transcripts` because that table is shaped for a
 * single blob under a one-of check constraint. Evidence is a list, each item
 * individually addressable, hashable, and extractable.
 */
export const proposalEvidence = pgTable(
  "proposal_evidence",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    proposalId: uuid("proposal_id")
      .notNull()
      .references(() => proposals.id, { onDelete: "cascade" }),
    // The client's local handle for this object, e.g. "f0". Kept so a manifest
    // entry can be traced back to the upload that produced it.
    key: text("key").notNull(),
    filename: text("filename").notNull(),
    mime: text("mime").notNull(),
    bytes: integer("bytes").notNull(),
    // Verified server-side against the uploaded bytes, not merely recorded.
    sha256: text("sha256").notNull(),
    storageObjectPath: text("storage_object_path").notNull(),
    // The agent's one-line justification for including this file.
    note: text("note"),
    // Populated from the existing extractors so evidence is searchable later.
    extractedText: text("extracted_text"),
    createdAt: timestamp("created_at", { withTimezone: true, mode: "date" })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    index("proposal_evidence_proposal_idx").on(table.proposalId),
    check("proposal_evidence_role_ck", sql`${table.key} <> ''`),
  ],
)
```

Then add to the existing `proposals` table definition (inside its column block):

```ts
    // complete = every declared object landed and verified. partial = a human
    // chose to proceed after an upload failed, and knows what is missing.
    // none = raised without evidence at all.
    evidenceStatus: text("evidence_status").notNull().default("none"),
```

and to its constraint array:

```ts
    check(
      "proposals_evidence_status_ck",
      sql`${table.evidenceStatus} IN ('none', 'partial', 'complete')`,
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm test src/features/proposals/evidence-schema.test.ts`
Expected: PASS

- [ ] **Step 5: Generate and apply the migration**

```bash
pnpm db:generate
pnpm db:migrate
```

Expected: a new `drizzle/00NN_*.sql` creating both tables, the column, and both check constraints.

- [ ] **Step 6: Commit**

```bash
git add src/db/schema.ts src/features/proposals/evidence-schema.test.ts drizzle/
git commit -m "feat(proposals): schema for evidence sets, evidence rows, and status"
```

---

### Task 2: Evidence set repository — mint, load, consume, sweep

**Files:**
- Create: `src/features/proposals/evidence-repository.ts`
- Test: `src/features/proposals/evidence-repository.test.ts`

**Interfaces:**
- Consumes: `proposalEvidenceSets` from Task 1; `createPresignedUploadUrl` from `src/features/files/storage.ts`
- Produces:
  - `mintEvidenceSet(input: MintInput): Promise<MintResult>`
  - `loadConsumableSet(orgId: string, evidenceId: string): Promise<EvidenceSetRow>` — throws `EvidenceSetUnusableError`
  - `markConsumed(evidenceId: string): Promise<void>`
  - `sweepExpiredSets(now: Date): Promise<number>`
  - `EvidenceSetUnusableError` with `.code: "unknown" | "expired" | "already_consumed"`
  - `EVIDENCE_OBJECT_CAP_BYTES = 25 * 1024 * 1024`
  - `EVIDENCE_SET_TTL_SECONDS = 3600`

- [ ] **Step 1: Write the failing test**

```ts
// src/features/proposals/evidence-repository.test.ts
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/features/files/storage", () => ({
  createPresignedUploadUrl: vi.fn(async (input: { objectPath: string }) => ({
    method: "PUT" as const,
    url: `https://storage.test/${input.objectPath}`,
    requiredHeaders: {},
    objectPath: input.objectPath,
  })),
}))

import {
  EVIDENCE_OBJECT_CAP_BYTES,
  EvidenceSetUnusableError,
  mintEvidenceSet,
} from "./evidence-repository"

const ORG = "org_test"
const OBJECT = {
  key: "f0",
  role: "file" as const,
  filename: "contractors.csv",
  mime: "text/csv",
  bytes: 4210,
  sha256: "a".repeat(64),
}

describe("mintEvidenceSet", () => {
  it("returns one presigned upload per accepted object", async () => {
    const result = await mintEvidenceSet({
      organizationId: ORG,
      userId: null,
      sessionId: "sess-1",
      objects: [OBJECT],
    })
    expect(result.uploads).toHaveLength(1)
    expect(result.uploads[0].key).toBe("f0")
    expect(result.uploads[0].method).toBe("PUT")
    expect(result.rejected).toEqual([])
  })

  it("rejects an over-cap object before any URL is minted for it", async () => {
    const result = await mintEvidenceSet({
      organizationId: ORG,
      userId: null,
      sessionId: "sess-1",
      objects: [{ ...OBJECT, key: "big", bytes: EVIDENCE_OBJECT_CAP_BYTES + 1 }],
    })
    expect(result.uploads).toHaveLength(0)
    expect(result.rejected[0]).toMatchObject({ key: "big", code: "over_object_cap" })
  })

  it("rejects a malformed sha256 rather than storing an unverifiable claim", async () => {
    const result = await mintEvidenceSet({
      organizationId: ORG,
      userId: null,
      sessionId: "sess-1",
      objects: [{ ...OBJECT, sha256: "not-a-hash" }],
    })
    expect(result.rejected[0]).toMatchObject({ code: "invalid_sha256" })
  })

  it("stamps an expiry one hour out", async () => {
    const before = Date.now()
    const result = await mintEvidenceSet({
      organizationId: ORG,
      userId: null,
      sessionId: "sess-1",
      objects: [OBJECT],
    })
    const delta = result.expiresAt.getTime() - before
    expect(delta).toBeGreaterThan(3_500_000)
    expect(delta).toBeLessThanOrEqual(3_600_000)
  })
})

describe("EvidenceSetUnusableError", () => {
  it("carries a code a caller can branch on", () => {
    const err = new EvidenceSetUnusableError("expired")
    expect(err.code).toBe("expired")
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm test src/features/proposals/evidence-repository.test.ts`
Expected: FAIL — cannot resolve `./evidence-repository`

- [ ] **Step 3: Write the implementation**

```ts
// src/features/proposals/evidence-repository.ts
import { randomUUID } from "node:crypto"

import { and, eq, isNull, lt } from "drizzle-orm"

import { db } from "@/db/client"
import { proposalEvidenceSets } from "@/db/schema"
import { createPresignedUploadUrl } from "@/features/files/storage"

export const EVIDENCE_OBJECT_CAP_BYTES = 25 * 1024 * 1024
export const EVIDENCE_SET_TTL_SECONDS = 3600

const SHA256_RE = /^[0-9a-f]{64}$/
const ROLES = new Set(["transcript", "file"])

export type EvidenceRole = "transcript" | "file"

export type DeclaredObject = {
  key: string
  role: EvidenceRole
  filename: string
  mime: string
  bytes: number
  sha256: string
}

export type MintInput = {
  organizationId: string
  userId: string | null
  sessionId: string
  objects: DeclaredObject[]
}

export type MintedUpload = {
  key: string
  method: "PUT"
  url: string
  headers: Record<string, string>
}

export type MintRejection = {
  key: string
  code: "over_object_cap" | "invalid_sha256" | "invalid_role" | "duplicate_key"
  message: string
}

export type MintResult = {
  evidenceId: string
  expiresAt: Date
  uploads: MintedUpload[]
  rejected: MintRejection[]
}

/**
 * Reject before minting, never after. The client shows this list to a human
 * for confirmation, so it has to be truthful about what will actually land.
 */
function screen(objects: DeclaredObject[]): {
  accepted: DeclaredObject[]
  rejected: MintRejection[]
} {
  const accepted: DeclaredObject[] = []
  const rejected: MintRejection[] = []
  const seen = new Set<string>()

  for (const object of objects) {
    if (seen.has(object.key)) {
      rejected.push({
        key: object.key,
        code: "duplicate_key",
        message: `Duplicate key "${object.key}".`,
      })
      continue
    }
    seen.add(object.key)

    if (!ROLES.has(object.role)) {
      rejected.push({
        key: object.key,
        code: "invalid_role",
        message: `Role must be "transcript" or "file".`,
      })
      continue
    }
    if (!SHA256_RE.test(object.sha256)) {
      rejected.push({
        key: object.key,
        code: "invalid_sha256",
        message: "sha256 must be 64 lowercase hex characters.",
      })
      continue
    }
    if (object.bytes > EVIDENCE_OBJECT_CAP_BYTES) {
      const mb = (object.bytes / 1024 / 1024).toFixed(0)
      rejected.push({
        key: object.key,
        code: "over_object_cap",
        message: `${mb} MB exceeds the 25 MB limit`,
      })
      continue
    }
    accepted.push(object)
  }

  return { accepted, rejected }
}

function evidenceObjectPath(evidenceId: string, key: string): string {
  return `proposal-evidence/${evidenceId}/${key}`
}

export async function mintEvidenceSet(input: MintInput): Promise<MintResult> {
  const evidenceId = `ev_${randomUUID().replace(/-/g, "")}`
  const { accepted, rejected } = screen(input.objects)
  const expiresAt = new Date(Date.now() + EVIDENCE_SET_TTL_SECONDS * 1000)

  const uploads: MintedUpload[] = []
  const declared: Record<string, unknown>[] = []

  for (const object of accepted) {
    const objectPath = evidenceObjectPath(evidenceId, object.key)
    const signed = await createPresignedUploadUrl({
      objectPath,
      contentType: object.mime,
      expiresInSeconds: EVIDENCE_SET_TTL_SECONDS,
    })
    uploads.push({
      key: object.key,
      method: "PUT",
      url: signed.url,
      headers: signed.requiredHeaders ?? {},
    })
    declared.push({ ...object, storageObjectPath: objectPath })
  }

  await db.insert(proposalEvidenceSets).values({
    evidenceId,
    workosOrganizationId: input.organizationId,
    userId: input.userId,
    sessionId: input.sessionId,
    declared,
    expiresAt,
  })

  return { evidenceId, expiresAt, uploads, rejected }
}

export class EvidenceSetUnusableError extends Error {
  constructor(public readonly code: "unknown" | "expired" | "already_consumed") {
    super(`Evidence set is ${code}.`)
    this.name = "EvidenceSetUnusableError"
  }
}

/**
 * Load a set that may still be consumed. Org-scoped: a set belonging to another
 * org reads as `unknown`, never as "forbidden", so the id space stays opaque.
 */
export async function loadConsumableSet(organizationId: string, evidenceId: string) {
  const [row] = await db
    .select()
    .from(proposalEvidenceSets)
    .where(
      and(
        eq(proposalEvidenceSets.evidenceId, evidenceId),
        eq(proposalEvidenceSets.workosOrganizationId, organizationId),
      ),
    )
    .limit(1)

  if (!row) throw new EvidenceSetUnusableError("unknown")
  if (row.consumedAt) throw new EvidenceSetUnusableError("already_consumed")
  if (row.expiresAt.getTime() <= Date.now()) throw new EvidenceSetUnusableError("expired")
  return row
}

export async function markConsumed(evidenceId: string): Promise<void> {
  await db
    .update(proposalEvidenceSets)
    .set({ consumedAt: new Date() })
    .where(eq(proposalEvidenceSets.evidenceId, evidenceId))
}

/** Collect sets nobody consumed. Returns how many rows were removed. */
export async function sweepExpiredSets(now: Date): Promise<number> {
  const removed = await db
    .delete(proposalEvidenceSets)
    .where(
      and(
        isNull(proposalEvidenceSets.consumedAt),
        lt(proposalEvidenceSets.expiresAt, now),
      ),
    )
    .returning({ evidenceId: proposalEvidenceSets.evidenceId })
  return removed.length
}
```

If `createPresignedUploadUrl` is not the exported name in `src/features/files/storage.ts`, read that file and use the real export that wraps `createSupabasePresignedUploadUrl` (line ~201). Adjust the mock in the test to match.

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm test src/features/proposals/evidence-repository.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/features/proposals/evidence-repository.ts src/features/proposals/evidence-repository.test.ts
git commit -m "feat(proposals): mint, load, consume, and sweep evidence sets"
```

---

### Task 3: The `proposals.beginEvidence` tool

**Files:**
- Modify: `src/features/tools/proposals.ts`
- Modify: `src/features/tools/index.ts` (register the tool)
- Test: `src/features/tools/proposals-evidence.test.ts`

**Interfaces:**
- Consumes: `mintEvidenceSet`, `EVIDENCE_OBJECT_CAP_BYTES` from Task 2
- Produces: tool `proposals.beginEvidence` returning `{ evidenceId, expiresAt, uploads, rejected }`

- [ ] **Step 1: Write the failing test**

```ts
// src/features/tools/proposals-evidence.test.ts
import { beforeEach, describe, expect, it, vi } from "vitest"

const mintEvidenceSet = vi.fn()
vi.mock("@/features/proposals/evidence-repository", () => ({
  mintEvidenceSet: (...args: unknown[]) => mintEvidenceSet(...args),
  EVIDENCE_OBJECT_CAP_BYTES: 25 * 1024 * 1024,
}))

import { beginEvidenceTool } from "./proposals"

const CTX = { organizationId: "org_1", userId: "user_1", role: "member" } as never

const INPUT = {
  sessionId: "sess-1",
  objects: [
    {
      key: "transcript",
      role: "transcript",
      filename: "session.jsonl",
      mime: "application/x-ndjson",
      bytes: 187829,
      sha256: "a".repeat(64),
    },
  ],
}

beforeEach(() => {
  mintEvidenceSet.mockReset()
  mintEvidenceSet.mockResolvedValue({
    evidenceId: "ev_abc",
    expiresAt: new Date("2026-08-05T16:45:00Z"),
    uploads: [
      { key: "transcript", method: "PUT", url: "https://storage.test/x", headers: {} },
    ],
    rejected: [],
  })
})

describe("proposals.beginEvidence", () => {
  it("mints a set scoped to the caller's org", async () => {
    await beginEvidenceTool.handler(INPUT as never, CTX)
    expect(mintEvidenceSet).toHaveBeenCalledWith(
      expect.objectContaining({ organizationId: "org_1", sessionId: "sess-1" }),
    )
  })

  it("returns the evidence id and one upload per object", async () => {
    const result = (await beginEvidenceTool.handler(INPUT as never, CTX)) as {
      evidenceId: string
      uploads: unknown[]
    }
    expect(result.evidenceId).toBe("ev_abc")
    expect(result.uploads).toHaveLength(1)
  })

  it("surfaces rejections so the client can show them before uploading", async () => {
    mintEvidenceSet.mockResolvedValue({
      evidenceId: "ev_abc",
      expiresAt: new Date(),
      uploads: [],
      rejected: [{ key: "big", code: "over_object_cap", message: "31 MB exceeds the 25 MB limit" }],
    })
    const result = (await beginEvidenceTool.handler(INPUT as never, CTX)) as {
      rejected: { code: string }[]
    }
    expect(result.rejected[0].code).toBe("over_object_cap")
  })

  it("refuses a caller whose role cannot write", async () => {
    await expect(
      beginEvidenceTool.handler(INPUT as never, { ...CTX, role: "viewer" } as never),
    ).rejects.toThrow()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm test src/features/tools/proposals-evidence.test.ts`
Expected: FAIL — `beginEvidenceTool` is not exported from `./proposals`

- [ ] **Step 3: Add the tool**

Append to `src/features/tools/proposals.ts`. Match the surrounding `defineTool` style — read the existing `proposeTool` first for the exact shape of `handler`, auth, and audit calls, and mirror them.

```ts
const evidenceObjectSchema = z.object({
  key: z.string().min(1).max(64).describe("Your local handle for this object, e.g. \"f0\"."),
  role: z.enum(["transcript", "file"]),
  filename: z.string().min(1).max(512),
  mime: z.string().min(1).max(255),
  bytes: z.number().int().positive(),
  sha256: z
    .string()
    .regex(/^[0-9a-f]{64}$/, "sha256 must be 64 lowercase hex characters")
    .describe("Hash the bytes BEFORE uploading. Verified server-side on propose."),
})

export const beginEvidenceTool = defineTool({
  name: "proposals.beginEvidence",
  title: "Begin Evidence Upload",
  summary: "Get presigned upload URLs for the evidence behind a proposal.",
  description: [
    "Declares the files and session transcript you are about to attach to a proposal,",
    "and returns one presigned PUT per object so the bytes go straight to storage.",
    "",
    "This exists because tool arguments are model-generated: a transcript or an invoice",
    "cannot travel inside a tool call at any realistic size, and base64 you emit yourself",
    "is not trustworthy for an audit record. Upload the bytes out of band, then cite the",
    "`evidenceId` in `proposals.propose`.",
    "",
    "Rules:",
    "- Hash each object with sha256 BEFORE uploading. The server streams what actually",
    "  landed and refuses the proposal if a hash does not match.",
    "- Objects over 25 MB come back in `rejected`, not `uploads`. Show that list to the",
    "  person before uploading — it is the difference between a truthful confirmation",
    "  and a surprise.",
    "- The set expires in one hour and is single-use. An unconsumed set is swept.",
    "- Exactly one object should carry `role: \"transcript\"`.",
  ].join("\n"),
  annotations: { readOnlyHint: false, idempotentHint: false, destructiveHint: false },
  inputSchema: baseToolInputSchema.extend({
    sessionId: z.string().min(1).max(128),
    objects: z.array(evidenceObjectSchema).min(1).max(64),
  }),
  outputSchema: jsonObjectSchema,
  handler: async (input, ctx: ToolContext) => {
    const organizationId = await resolveOrgId(ctx)
    if (!roleCanWrite(ctx.role)) {
      throw new PolicyDeniedError("Your role cannot raise proposals.")
    }
    const result = await mintEvidenceSet({
      organizationId,
      userId: ctx.userId ?? null,
      sessionId: input.sessionId,
      objects: input.objects,
    })
    await auditToolCall(ctx, "proposals.beginEvidence", {
      evidenceId: result.evidenceId,
      objectCount: result.uploads.length,
      rejectedCount: result.rejected.length,
    })
    return {
      evidenceId: result.evidenceId,
      expiresAt: result.expiresAt.toISOString(),
      uploads: result.uploads,
      rejected: result.rejected,
    }
  },
})
```

Add the import at the top of the file:

```ts
import { mintEvidenceSet } from "@/features/proposals/evidence-repository"
```

Then register it in `src/features/tools/index.ts` alongside the existing proposals tools — read how `proposals.propose` and `proposals.actions` are listed and add `beginEvidenceTool` the same way, under the same SKU/entitlement gate.

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm test src/features/tools/proposals-evidence.test.ts`
Expected: PASS

- [ ] **Step 5: Verify the tool catalog still validates**

Run: `pnpm tools:validate`
Expected: passes, with `proposals.beginEvidence` present.

- [ ] **Step 6: Commit**

```bash
git add src/features/tools/proposals.ts src/features/tools/index.ts src/features/tools/proposals-evidence.test.ts
git commit -m "feat(proposals): add proposals.beginEvidence"
```

---

### Task 4: Verify and attach evidence on consume

**Files:**
- Create: `src/features/proposals/evidence-consume.ts`
- Modify: `src/features/proposals/repository.ts` (the `createProposal` transaction, ~line 355)
- Modify: `src/features/tools/proposals.ts` (pass the evidence through)
- Test: `src/features/proposals/evidence-consume.test.ts`

**Interfaces:**
- Consumes: `loadConsumableSet`, `markConsumed`, `EvidenceSetUnusableError` from Task 2
- Produces:
  - `parseEvidenceReference(transcript: unknown): EvidenceReference | null`
  - `verifyAndAttachEvidence(tx, proposalId, orgId, ref): Promise<"complete" | "partial">`
  - `EvidenceIntegrityError` with `.mismatches: {key, expected, actual}[]`

- [ ] **Step 1: Write the failing test**

```ts
// src/features/proposals/evidence-consume.test.ts
import { describe, expect, it } from "vitest"
import { parseEvidenceReference } from "./evidence-consume"

describe("parseEvidenceReference", () => {
  it("reads an evidence reference out of the transcript field", () => {
    const ref = parseEvidenceReference({
      evidenceId: "ev_abc",
      sessionId: "sess-1",
      manifest: [{ key: "f0", filename: "a.csv", sha256: "b".repeat(64) }],
    })
    expect(ref).toMatchObject({ evidenceId: "ev_abc", sessionId: "sess-1" })
  })

  it("returns null for a plain inline transcript so today's behaviour is untouched", () => {
    expect(parseEvidenceReference({ messages: [{ role: "user", content: "hi" }] })).toBeNull()
  })

  it("returns null for a null transcript", () => {
    expect(parseEvidenceReference(null)).toBeNull()
  })

  it("treats a non-string evidenceId as no reference rather than trusting it", () => {
    expect(parseEvidenceReference({ evidenceId: 42, sessionId: "s" })).toBeNull()
  })

  it("carries the declared partial flag through", () => {
    const ref = parseEvidenceReference({
      evidenceId: "ev_abc",
      sessionId: "sess-1",
      manifest: [],
      evidenceStatus: "partial",
    })
    expect(ref?.declaredStatus).toBe("partial")
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm test src/features/proposals/evidence-consume.test.ts`
Expected: FAIL — cannot resolve `./evidence-consume`

- [ ] **Step 3: Write the parser and the verifier**

```ts
// src/features/proposals/evidence-consume.ts
import { createHash } from "node:crypto"

import { createStorageReadStream, getFilesBucketName } from "@/features/files/storage"

export type EvidenceReference = {
  evidenceId: string
  sessionId: string | null
  manifest: Record<string, unknown>[]
  declaredStatus: "complete" | "partial"
}

/**
 * An evidence-backed transcript is just a transcript object carrying an
 * `evidenceId`. Anything else is an ordinary inline transcript and keeps
 * working exactly as it did before this feature existed.
 */
export function parseEvidenceReference(transcript: unknown): EvidenceReference | null {
  if (!transcript || typeof transcript !== "object" || Array.isArray(transcript)) return null
  const record = transcript as Record<string, unknown>
  const evidenceId = record.evidenceId
  if (typeof evidenceId !== "string" || evidenceId.length === 0) return null

  const manifest = Array.isArray(record.manifest)
    ? (record.manifest.filter(
        (m) => m && typeof m === "object" && !Array.isArray(m),
      ) as Record<string, unknown>[])
    : []

  return {
    evidenceId,
    sessionId: typeof record.sessionId === "string" ? record.sessionId : null,
    manifest,
    declaredStatus: record.evidenceStatus === "partial" ? "partial" : "complete",
  }
}

export class EvidenceIntegrityError extends Error {
  constructor(
    public readonly mismatches: { key: string; expected: string; actual: string }[],
  ) {
    super(
      `Uploaded evidence does not match its declared hash: ${mismatches
        .map((m) => m.key)
        .join(", ")}.`,
    )
    this.name = "EvidenceIntegrityError"
  }
}

/**
 * Stream an object out of storage and hash it.
 *
 * Strict rather than an ETag comparison: this artifact's whole job is to be
 * provably what it claims. A transcript that truncated mid-upload still looks
 * complete to whoever reads it later, which is worse than having none.
 */
export async function hashStoredObject(objectPath: string): Promise<string> {
  const stream = await createStorageReadStream({
    bucket: getFilesBucketName(),
    objectPath,
  })
  const hash = createHash("sha256")
  for await (const chunk of stream) hash.update(chunk as Buffer)
  return hash.digest("hex")
}
```

Now wire it into `createProposal`. Read `src/features/proposals/repository.ts` around line 355 — the existing block is:

```ts
    if (input.transcript) {
      await tx.insert(transcripts).values({
        proposalId: proposal.id,
        content: input.transcript,
      })
    }
```

Replace it with a branch that keeps the old path intact:

```ts
    const evidenceRef = parseEvidenceReference(input.transcript ?? null)

    if (evidenceRef) {
      // Evidence-backed: the bytes are already in storage. Verify every one of
      // them, point the transcript row at the uploaded JSONL instead of inlining
      // it, and record each file.
      const set = await loadConsumableSet(input.organizationId, evidenceRef.evidenceId)
      const declared = set.declared as {
        key: string
        role: string
        filename: string
        mime: string
        bytes: number
        sha256: string
        storageObjectPath: string
      }[]

      const mismatches: { key: string; expected: string; actual: string }[] = []
      for (const object of declared) {
        const actual = await hashStoredObject(object.storageObjectPath)
        if (actual !== object.sha256) {
          mismatches.push({ key: object.key, expected: object.sha256, actual })
        }
      }
      if (mismatches.length > 0) throw new EvidenceIntegrityError(mismatches)

      const transcriptObject = declared.find((d) => d.role === "transcript")
      if (transcriptObject) {
        await tx.insert(transcripts).values({
          proposalId: proposal.id,
          objectPath: transcriptObject.storageObjectPath,
        })
      }

      const notes = new Map(
        evidenceRef.manifest
          .filter((m) => typeof m.key === "string")
          .map((m) => [m.key as string, typeof m.note === "string" ? m.note : null]),
      )

      const files = declared.filter((d) => d.role === "file")
      if (files.length > 0) {
        await tx.insert(evidence).values(
          files.map((f) => ({
            proposalId: proposal.id,
            key: f.key,
            filename: f.filename,
            mime: f.mime,
            bytes: f.bytes,
            sha256: f.sha256,
            storageObjectPath: f.storageObjectPath,
            note: notes.get(f.key) ?? null,
          })),
        )
      }

      await tx
        .update(t)
        .set({ evidenceStatus: evidenceRef.declaredStatus })
        .where(eq(t.id, proposal.id))

      await markConsumed(evidenceRef.evidenceId)
    } else if (input.transcript) {
      await tx.insert(transcripts).values({
        proposalId: proposal.id,
        content: input.transcript,
      })
    }
```

Add at the top of `repository.ts`:

```ts
import {
  EvidenceIntegrityError,
  hashStoredObject,
  parseEvidenceReference,
} from "./evidence-consume"
import { loadConsumableSet, markConsumed } from "./evidence-repository"

const evidence = schema.proposalEvidence
```

Finally, in `src/features/tools/proposals.ts`, map the two new failure modes onto refusal codes the way the existing errors are mapped — read how `InvalidAssigneeError` is caught and mirror it:

- `EvidenceSetUnusableError` → code `invalid_evidence`, message naming whether it was unknown, expired, or already consumed.
- `EvidenceIntegrityError` → code `evidence_integrity`, message listing the mismatched keys.

Both are refusals: nothing is queued, and the client should re-upload rather than retry blindly.

- [ ] **Step 4: Populate `extractedText` for the formats that support it**

The column exists so evidence is searchable later, and the extractors already
exist — `src/features/files/text-extract.ts` and `src/features/files/spreadsheets.ts`.
Read both for their real export names and input shapes before wiring this.

In the evidence-insert block above, before building the `values` array, extract
text for each file whose mime the extractors handle (PDF, xlsx, csv, plain text),
and pass it as `extractedText`. Anything unsupported stays `null` — an image or an
opaque binary has no text, and that is not a failure.

Extraction must not be able to fail the proposal: wrap each call so an extractor
error leaves `extractedText` null and the evidence row still lands. The bytes are
already verified and stored by this point; losing searchability is a far smaller
loss than refusing a legitimate write.

Add this test to `src/features/proposals/evidence-consume.test.ts`:

```ts
import { safeExtractText } from "./evidence-consume"

describe("safeExtractText", () => {
  it("returns null for a mime no extractor handles", async () => {
    await expect(safeExtractText("image/png", "path/x.png")).resolves.toBeNull()
  })

  it("returns null rather than throwing when an extractor fails", async () => {
    await expect(safeExtractText("application/pdf", "path/missing.pdf")).resolves.toBeNull()
  })
})
```

and the corresponding helper in `evidence-consume.ts`:

```ts
const EXTRACTABLE = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/csv",
  "text/plain",
  "text/markdown",
])

/** Searchability is a nice-to-have; a verified write is not. Never throws. */
export async function safeExtractText(
  mime: string,
  objectPath: string,
): Promise<string | null> {
  if (!EXTRACTABLE.has(mime)) return null
  try {
    return await extractTextForEvidence(mime, objectPath)
  } catch {
    return null
  }
}
```

where `extractTextForEvidence` is a thin adapter you write over whatever
`text-extract.ts` and `spreadsheets.ts` actually export.

- [ ] **Step 5: Add the integration tests**

Append to `src/features/proposals/evidence-consume.test.ts`:

```ts
import { EvidenceIntegrityError } from "./evidence-consume"

describe("EvidenceIntegrityError", () => {
  it("names every mismatched object so the client knows what to re-upload", () => {
    const err = new EvidenceIntegrityError([
      { key: "f0", expected: "a".repeat(64), actual: "b".repeat(64) },
      { key: "f1", expected: "c".repeat(64), actual: "d".repeat(64) },
    ])
    expect(err.message).toContain("f0")
    expect(err.message).toContain("f1")
    expect(err.mismatches).toHaveLength(2)
  })
})
```

- [ ] **Step 6: Run the full proposals suite**

Run: `pnpm test src/features/proposals`
Expected: PASS, including the pre-existing test at `src/features/tools/proposals.test.ts:236` that asserts an inline transcript still passes straight through.

- [ ] **Step 7: Commit**

```bash
git add src/features/proposals/evidence-consume.ts src/features/proposals/evidence-consume.test.ts src/features/proposals/repository.ts src/features/tools/proposals.ts
git commit -m "feat(proposals): verify and attach uploaded evidence on propose"
```

---

### Task 5: Sweep orphaned evidence sets

**Files:**
- Create: `src/app/api/cron/sweep-evidence/route.ts`
- Test: `src/features/proposals/evidence-sweep.test.ts`
- Modify: `vercel.json` (add the cron entry)

**Interfaces:**
- Consumes: `sweepExpiredSets` from Task 2
- Produces: `GET /api/cron/sweep-evidence` returning `{ removed: number }`

- [ ] **Step 1: Write the failing test**

```ts
// src/features/proposals/evidence-sweep.test.ts
import { describe, expect, it, vi } from "vitest"

const sweepExpiredSets = vi.fn()
vi.mock("./evidence-repository", () => ({
  sweepExpiredSets: (...args: unknown[]) => sweepExpiredSets(...args),
}))

import { runEvidenceSweep } from "./evidence-sweep"

describe("runEvidenceSweep", () => {
  it("reports how many orphaned sets it removed", async () => {
    sweepExpiredSets.mockResolvedValue(3)
    await expect(runEvidenceSweep(new Date())).resolves.toEqual({ removed: 3 })
  })

  it("is a no-op when nothing has expired", async () => {
    sweepExpiredSets.mockResolvedValue(0)
    await expect(runEvidenceSweep(new Date())).resolves.toEqual({ removed: 0 })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm test src/features/proposals/evidence-sweep.test.ts`
Expected: FAIL — cannot resolve `./evidence-sweep`

- [ ] **Step 3: Write the sweep and its route**

```ts
// src/features/proposals/evidence-sweep.ts
import { sweepExpiredSets } from "./evidence-repository"

/**
 * beginEvidence mints storage objects before a human confirms. Someone who
 * cancels at that gate leaves them unreferenced, so they expire and get
 * collected rather than accumulating forever.
 */
export async function runEvidenceSweep(now: Date): Promise<{ removed: number }> {
  return { removed: await sweepExpiredSets(now) }
}
```

```ts
// src/app/api/cron/sweep-evidence/route.ts
import { type NextRequest } from "next/server"

import { runEvidenceSweep } from "@/features/proposals/evidence-sweep"

export const runtime = "nodejs"
export const dynamic = "force-dynamic"

export async function GET(req: NextRequest) {
  const secret = process.env.CRON_SECRET
  if (secret && req.headers.get("authorization") !== `Bearer ${secret}`) {
    return new Response("Unauthorized", { status: 401 })
  }
  const result = await runEvidenceSweep(new Date())
  return Response.json(result)
}
```

Before writing the route, read an existing cron route under `src/app/api/` and match its auth convention — if the codebase already has a shared cron guard, use that instead of the inline `CRON_SECRET` check above.

Add to `vercel.json`, alongside any existing crons:

```json
{ "path": "/api/cron/sweep-evidence", "schedule": "15 * * * *" }
```

Hourly at :15 — the TTL is one hour, so nothing lives much beyond two.

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm test src/features/proposals/evidence-sweep.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/features/proposals/evidence-sweep.ts src/features/proposals/evidence-sweep.test.ts src/app/api/cron/sweep-evidence/route.ts vercel.json
git commit -m "feat(proposals): sweep orphaned evidence sets hourly"
```

---

### Task 6: Replace the transcript placeholder with a real viewer

**Files:**
- Modify: `src/app/action-gateway/proposals/[id]/page.tsx:355-385`
- Modify: `src/features/proposals/repository.ts` (add `getProposalEvidence`)
- Create: `src/app/api/proposals/[id]/evidence/[evidenceRowId]/route.ts`
- Test: `src/features/proposals/evidence-view.test.ts`

**Interfaces:**
- Consumes: `proposalEvidence` from Task 1
- Produces: `getProposalEvidence(orgId, proposalId): Promise<EvidenceRow[]>`

- [ ] **Step 1: Write the failing test**

```ts
// src/features/proposals/evidence-view.test.ts
import { describe, expect, it } from "vitest"
import { formatEvidenceSize, evidenceStatusLabel } from "./evidence-view"

describe("formatEvidenceSize", () => {
  it("uses KB below a megabyte", () => {
    expect(formatEvidenceSize(4210)).toBe("4.1 KB")
  })

  it("uses MB above a megabyte", () => {
    expect(formatEvidenceSize(884102)).toBe("863.4 KB")
    expect(formatEvidenceSize(5 * 1024 * 1024)).toBe("5.0 MB")
  })
})

describe("evidenceStatusLabel", () => {
  it("warns plainly when a human accepted an incomplete record", () => {
    expect(evidenceStatusLabel("partial")).toContain("incomplete")
  })

  it("says nothing alarming for a complete record", () => {
    expect(evidenceStatusLabel("complete")).toBe("Complete")
  })

  it("distinguishes no evidence from partial evidence", () => {
    expect(evidenceStatusLabel("none")).toBe("No evidence attached")
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm test src/features/proposals/evidence-view.test.ts`
Expected: FAIL — cannot resolve `./evidence-view`

- [ ] **Step 3: Write the presenters**

```ts
// src/features/proposals/evidence-view.ts
export function formatEvidenceSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024).toFixed(1)} KB`
}

export function evidenceStatusLabel(status: string): string {
  if (status === "partial") {
    return "Partial — some evidence failed to upload and the proposer accepted an incomplete record"
  }
  if (status === "complete") return "Complete"
  return "No evidence attached"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm test src/features/proposals/evidence-view.test.ts`
Expected: PASS

- [ ] **Step 5: Add the repository read and the download route**

In `src/features/proposals/repository.ts`, add alongside `getProposalTranscript` and following its exact org-gating pattern (it joins through `proposals` because the evidence table carries no org column):

```ts
export async function getProposalEvidence(organizationId: string, proposalId: string) {
  return db
    .select({
      id: evidence.id,
      key: evidence.key,
      filename: evidence.filename,
      mime: evidence.mime,
      bytes: evidence.bytes,
      sha256: evidence.sha256,
      note: evidence.note,
    })
    .from(evidence)
    .innerJoin(t, eq(t.id, evidence.proposalId))
    .where(and(eq(evidence.proposalId, proposalId), eq(t.workosOrganizationId, organizationId)))
}
```

Create the download route, modelled on `src/app/api/registry/session-content/[sessionId]/route.ts` — read that file and mirror its streaming and auth shape, adding the same `mayReadTranscript` permission check the proposal page already applies.

- [ ] **Step 6: Replace the placeholder in the page**

At `src/app/action-gateway/proposals/[id]/page.tsx:370`, the branch currently reads:

```tsx
            ) : transcript.objectPath ? (
              "Stored in object storage — no inline viewer yet."
            ) : (
```

Replace that string with a link that streams the stored transcript through the download route, keeping the surrounding `mayReadTranscript` gate exactly as narrow as it is today. Then add an evidence panel below the transcript panel listing each row: filename, `formatEvidenceSize(bytes)`, the first 12 characters of `sha256`, the `note`, and a download link. Render `evidenceStatusLabel(proposal.evidenceStatus)` above the list, and make the `partial` case visually distinct — an approver needs to notice they are looking at an incomplete record before they approve it, not after.

- [ ] **Step 7: Run the full suite and lint**

Run: `pnpm test && pnpm lint`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/features/proposals/evidence-view.ts src/features/proposals/evidence-view.test.ts src/features/proposals/repository.ts src/app/action-gateway/proposals/ src/app/api/proposals/
git commit -m "feat(proposals): view stored transcripts and attached evidence"
```

---

## Done when

- `proposals.beginEvidence` appears in `pnpm tools:validate` output.
- A propose call carrying `{ evidenceId, sessionId, manifest }` writes `proposal_transcripts.objectPath` and one `proposal_evidence` row per file.
- A propose call carrying a plain inline transcript still writes `content` — `src/features/tools/proposals.test.ts:236` passes unchanged.
- A tampered or truncated object refuses the proposal with `evidence_integrity`.
- A replayed or expired `evidenceId` refuses with `invalid_evidence`.
- The proposal page renders the transcript and evidence instead of a placeholder.
- `pnpm test && pnpm lint` pass.
