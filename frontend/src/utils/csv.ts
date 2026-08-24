/** RFC 4180 CSV field escaping.
 *
 * A field needs quoting if it contains the delimiter, a quote, a CR, or a
 * LF. Quotes inside the field are escaped by doubling.
 */
export function csvEscape(s: string): string {
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
