import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const KEYS = ["lk:kpi", "lk:live", "lk:health:nightly", "lk:health:live"];

export async function GET() {
  const url = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) {
    return NextResponse.json({ error: "Redis is not configured on Vercel" }, { status: 500 });
  }
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(["MGET", ...KEYS]),
      cache: "no-store",
    });
    const body = await res.json();
    const vals: (string | null)[] = body.result || [];
    // emails removed by hand: the page hides them at once, before the next job run
    let dismissed: string[] = [];
    try {
      const d = await fetch(url, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(["HKEYS", "lk:dismissed"]),
        cache: "no-store",
      });
      dismissed = (await d.json()).result || [];
    } catch {
      dismissed = [];
    }
    const parse = (v: string | null) => (v ? JSON.parse(v) : null);
    return NextResponse.json(
      {
        kpi: parse(vals[0]),
        live: parse(vals[1]),
        health: { nightly: parse(vals[2]), live: parse(vals[3]) },
        dismissed,
        served_at: new Date().toISOString(),
      },
      { headers: { "Cache-Control": "no-store" } }
    );
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
