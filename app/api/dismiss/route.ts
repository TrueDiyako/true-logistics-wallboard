import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

// Emails removed by hand on the dashboard ("x" on "Emails waiting over 1 day").
// POST {key} stores it, DELETE {key} undoes it. The jobs read the same hash: a removed email
// leaves the list and counts as answered at the moment of the click.
const HASH = "lk:dismissed";
const KEY_RE = /^[^|\s]{3,200}\|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/;   // "sender|2026-10-30T09:15"

async function redis(cmd: (string | number)[]) {
  const url = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) throw new Error("Redis is not configured on Vercel");
  const res = await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(cmd),
    cache: "no-store",
  });
  const body = await res.json();
  if (body.error) throw new Error(body.error);
  return body.result;
}

async function keyFrom(req: Request) {
  try {
    const { key } = await req.json();
    return typeof key === "string" && KEY_RE.test(key) ? key : null;
  } catch {
    return null;
  }
}

export async function POST(req: Request) {
  const key = await keyFrom(req);
  if (!key) return NextResponse.json({ error: "bad key" }, { status: 400 });
  try {
    await redis(["HSET", HASH, key, JSON.stringify({ at: new Date().toISOString() })]);
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}

export async function DELETE(req: Request) {
  const key = await keyFrom(req);
  if (!key) return NextResponse.json({ error: "bad key" }, { status: 400 });
  try {
    await redis(["HDEL", HASH, key]);
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
