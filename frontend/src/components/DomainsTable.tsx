// The data-needs catalog, rendered ONCE.
//
// The console had this table three times, drifting apart as it went:
//   - `viewDomains` (`console.js:766-783`) — the full table, six columns.
//   - `subDomains`  (`console.js:1337-1359`) — the same six columns, a different
//     summary strip, and a different lede.
//   - `viewCoverage`'s row header (`console.js:611-625`) — the same
//     satisfied/landed judgement inlined a fourth way, as `n/m landed`.
//
// TIER3_MIGRATION_PLAN.md §3 flags the duplication ("2 console copies → 1"). The
// cost of three copies was not the lines; it was that "satisfied" was decided in
// three places, so a change to what counts as satisfied had to be found three
// times. Here it is `domain.satisfied` from the server, read once.
//
// Deliberately presentational: it takes rows and renders them. Coverage owns the
// query, so the same array feeds both the matrix and this table without a second
// request.

import type { Domain } from '../types'

/** Landed-vs-serving, phrased so `0/0` does not read as a failure. */
function landedLabel(domain: Domain): string {
  const serving = domain.serving_asset_count ?? 0
  if (!serving) return 'no source'
  return `${domain.ready_asset_count ?? 0}/${serving} landed`
}

export function SatisfiedBadge({ satisfied }: { satisfied?: boolean | null }) {
  return (
    <span className={satisfied ? 'badge-low' : 'badge-critical'}>
      {satisfied ? 'satisfied' : 'gap'}
    </span>
  )
}

export function DomainsTable({ domains }: { domains: Domain[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm min-w-[720px]">
        <thead className="text-xs uppercase text-navy-500 border-b border-navy-600">
          <tr>
            <th className="text-left px-3 py-2.5 font-medium">Data need</th>
            <th className="text-left px-3 py-2.5 font-medium">Category</th>
            <th className="text-right px-3 py-2.5 font-medium">Sources</th>
            <th className="text-right px-3 py-2.5 font-medium">Landed</th>
            <th className="text-right px-3 py-2.5 font-medium">Used by</th>
            <th className="text-left px-3 py-2.5 font-medium">State</th>
          </tr>
        </thead>
        <tbody>
          {domains.map((domain) => (
            <tr key={domain.id} className="border-b border-navy-600 hover:bg-lava/5">
              <td className="px-3 py-2.5">
                <div className="font-medium text-white">{domain.label}</div>
                {/* The machine name is what an API caller and a SQL query use, so
                    it stays visible — in mono, small, under the human label. */}
                <div className="text-xs text-navy-500 font-mono">{domain.name}</div>
              </td>
              <td className="px-3 py-2.5 text-xs text-navy-400">{domain.category ?? '—'}</td>
              <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                {domain.serving_asset_count ?? 0}
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                {domain.ready_asset_count ?? 0}
              </td>
              <td className="px-3 py-2.5 text-right font-mono text-navy-300">
                {domain.required_by_count ?? 0}
              </td>
              <td className="px-3 py-2.5">
                <SatisfiedBadge satisfied={domain.satisfied} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The one-line summary Coverage puts in a matrix row header. Exported so the
 *  `n/m landed` phrasing has a single definition too. */
export { landedLabel }
