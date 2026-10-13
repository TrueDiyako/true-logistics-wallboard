// Date/number formatting without Intl/toLocale*: smart-TV browsers often ship a reduced
// Intl that returns only "15" for {weekday, day, month} - so everything is spelled out here.
const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
  "October", "November", "December"];
const pad = (n: number) => (n < 10 ? "0" : "") + n;

// "2026-10-15" (date only, read as a calendar day - no time-zone shift)
function parts(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return { y, m, d, wd };
}
export const fmtDay = (iso: string) => { const p = parts(iso); return `${WD[p.wd]} ${p.d} ${MON[p.m - 1]}`; };
export const fmtDayNum = (iso: string) => { const p = parts(iso); return `${WD[p.wd]} ${p.d}/${p.m}`; };
export const fmtDayMonth = (iso: string) => { const p = parts(iso); return `${p.d} ${MON[p.m - 1]}`; };
export const fmtDayMonthLong = (iso: string) => { const p = parts(iso); return `${p.d} ${MONTH[p.m - 1]}`; };
export const fmtClockDate = (d: Date) => `${WD[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]}`;
export const fmtTime = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
export const fmtNum = (n: number) => {
  const s = String(Math.round(Math.abs(n)));
  return (n < 0 ? "-" : "") + s.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
};
export const fmtOneDecimal = (n: number) => (Math.round(n * 10) / 10).toFixed(Math.abs(n) >= 10 ? 0 : 1);
